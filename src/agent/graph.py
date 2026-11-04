"""诊断流水线：优先 LangGraph，不可用时回退纯 Python 状态机。"""

from __future__ import annotations

import json
import re
from typing import Any

from src import config
from src.agent.state import DiagnoseState
from src.rag.retrieve import build_query_from_evidence, retrieve_runbooks
from src.remediation.catalog import build_remediation, render_remediation_markdown
from src.tools.k8s_tools import collect_evidence


def _read_prompt(name: str) -> str:
    path = config.PROMPTS_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _detect_fault_type(evidence: dict[str, Any], alert_text: str = "") -> str:
    """根据证据/告警启发式判定故障类型。

    显式场景优先。其余按值班里更具体的证据短语匹配，探针先于 CrashLoop，
    避免存活探针把容器打重启后被收成「进程自己退出」。
    """
    scenario = config.normalize_scenario(evidence.get("scenario"))
    if scenario:
        return scenario

    blob = " ".join(
        [
            alert_text or "",
            json.dumps(evidence.get("pod") or {}, ensure_ascii=False),
            json.dumps(evidence.get("events") or [], ensure_ascii=False),
            evidence.get("describe") or "",
            evidence.get("logs") or "",
        ]
    ).lower()

    if "imagepull" in blob or "errimagepull" in blob or "pull image" in blob:
        return "imagepull"
    if "oomkill" in blob or "oom" in blob or "exit code\": 137" in blob or "exitcode\": 137" in blob:
        return "oom"
    if (
        "readiness probe failed" in blob
        or "liveness probe failed" in blob
        or "probe failed" in blob
    ):
        return "probe"
    if (
        "createcontainerconfigerror" in blob
        or "failedmount" in blob
        or "configmap" in blob
        or 'secret "' in blob
    ):
        return "configmount"
    if (
        "failedscheduling" in blob
        or "unschedulable" in blob
        or "insufficient cpu" in blob
        or "insufficient memory" in blob
        or "node affinity" in blob
    ):
        return "pending"
    if (
        "endpoints is empty" in blob
        or "endpoints: <none>" in blob
        or "endpoints <none>" in blob
        or "matches 0 pods" in blob
        or "no ready addresses" in blob
    ):
        return "connectivity"
    if "crashloop" in blob or "back-off restarting" in blob:
        return "crashloop"
    return "general"


def _suggest_commands(fault: str, namespace: str, pod_name: str | None) -> list[str]:
    ns = namespace or "default"
    pod = pod_name or "<pod-name>"
    common = [
        f"kubectl get pod {pod} -n {ns} -o wide  # 需人工确认",
        f"kubectl describe pod {pod} -n {ns}  # 需人工确认",
        f"kubectl get events -n {ns} --field-selector involvedObject.name={pod}  # 需人工确认",
        f"kubectl logs {pod} -n {ns} --previous --tail=100  # 需人工确认",
    ]
    extra = {
        "imagepull": [
            f"kubectl get pod {pod} -n {ns} -o jsonpath='{{.spec.containers[*].image}}'  # 需人工确认",
            "# 检查镜像仓库可达性与 imagePullSecrets（勿在无审批时修改 Secret）",
        ],
        "oom": [
            f"kubectl top pod {pod} -n {ns}  # 需人工确认（需 metrics-server）",
            "# 评估 memory limit / 泄漏；变更资源需变更评审",
        ],
        "crashloop": [
            f"kubectl logs {pod} -n {ns} --all-containers  # 需人工确认",
            "# 检查 ConfigMap/环境变量与探针配置，避免盲目删重建",
        ],
        "pending": [
            f"kubectl get pod {pod} -n {ns} -o jsonpath='{{.spec.nodeSelector}}'  # 需人工确认",
            "# 先看 FailedScheduling 是资源不够，还是 affinity/taint。不要直接改节点",
        ],
        "configmount": [
            f"kubectl get configmap,secret -n {ns}  # 需人工确认",
            "# 只补缺失对象。Secret 走审批，不要把密钥写进命令行或 Runbook",
        ],
        "probe": [
            "# 对照容器实际监听的端口和路径，再改探针。不要靠删探针假装 Ready",
        ],
        "connectivity": [
            "kubectl get svc demo-web -n "
            + ns
            + " -o jsonpath='{.spec.selector}'  # 需人工确认",
            "kubectl get endpoints demo-web -n " + ns + "  # 需人工确认",
            "# Endpoints 为空先对选择器。有地址仍不通，再查 NetworkPolicy 和 CoreDNS",
        ],
        "general": [],
    }
    return common + extra.get(fault, [])


def _rule_based_report(
    fault: str,
    evidence: dict[str, Any],
    runbooks: list[dict[str, str]],
    alert_text: str,
) -> tuple[str, dict[str, Any]]:
    """无 LLM 时的规则/模板诊断。"""
    titles = {
        "imagepull": "ImagePullBackOff：镜像拉取失败",
        "oom": "OOMKilled：容器内存超限被杀",
        "crashloop": "CrashLoopBackOff：容器反复崩溃",
        "pending": "Pending / Unschedulable：Pod 排不上节点",
        "configmount": "CreateContainerConfigError / FailedMount：配置对象缺失",
        "probe": "探针未就绪：Pod 在跑但没有 Ready",
        "connectivity": "Service 无 Endpoints：选择器对不上 Pod",
        "general": "通用排查：尚未匹配到明确故障类型",
    }
    root_causes = {
        "imagepull": (
            "容器镜像名/Tag 不存在、仓库不可达，或缺少正确的 imagePullSecrets，"
            "导致 kubelet 拉取失败并进入 ImagePullBackOff。"
        ),
        "oom": (
            "容器实际内存占用超过 resources.limits.memory，被 cgroup OOM Killer 终止"
            "（常见 exitCode=137 / reason=OOMKilled）。"
        ),
        "crashloop": (
            "进程非 0 退出后被 kubelet 反复重启；常见原因包括配置缺失、依赖未就绪、"
            "启动命令错误或健康检查失败。"
        ),
        "pending": (
            "调度器没有可用节点：requests 超过节点可分配资源，"
            "和/或 nodeSelector、nodeAffinity 要求的标签在集群里不存在。"
            "本例同时带了这两类条件，所以只改其中一个，Pod 仍会 Pending。"
        ),
        "configmount": (
            "Pod 通过环境变量和卷引用 ConfigMap `app-config`，对象不存在。"
            "kubelet 因此报 CreateContainerConfigError，卷挂载报 FailedMount。"
            "这不是进程启动后的崩溃。"
        ),
        "probe": (
            "容器在 80 端口提供 HTTP，readiness 和 liveness 却访问 8080 的 `/healthz`，"
            "所以 Pod 一直 Running 但不 Ready，存活探针失败还会重启容器。"
        ),
        "connectivity": (
            "Pod 本身 Ready，标签是 `app=demo`。Service `demo-web` 的选择器仍是 "
            "`app=frontend`，Endpoints 为空。DNS 能解析到 ClusterIP，但没有后端。"
            "本例没有拒绝流量的 NetworkPolicy；Endpoints 非空仍不通时才继续查策略和 CoreDNS。"
        ),
        "general": "证据不足以自动判定，请结合 Events 与日志继续人工排查。",
    }
    risks = {
        "imagepull": "盲目改镜像 Tag 或共享凭证可能导致更多错误部署；生产变更需审批。",
        "oom": "直接调高 limit 可能掩盖泄漏并挤占节点；删除 Pod 会短暂中断流量。",
        "crashloop": "频繁删重建可能丢失排障窗口；错误改 ConfigMap 会影响同命名空间工作负载。",
        "pending": "把 requests 降得过低会在节点上超卖；删掉亲和性可能把 Pod 调度到不该去的节点。",
        "configmount": "补错 ConfigMap 或把 Secret 内容写进清单会扩大泄露面。本动作只创建非敏感 ConfigMap，且不覆盖已有键。",
        "probe": "改错端口会让流量打到未就绪的 Pod，或让存活探针继续重启容器。不要用删除探针代替修正路径。",
        "connectivity": "改选择器会立刻切换 Service 的后端。本动作只改 demo-web 的 selector，不改端口，也不放宽 NetworkPolicy。",
        "general": "在证据不足时执行写操作风险高，请先只读取证。",
    }

    ns = evidence.get("namespace") or config.K8S_NAMESPACE
    pod_name = evidence.get("pod_name")
    events = evidence.get("events") or []
    evidence_lines = []
    for e in events[:5]:
        evidence_lines.append(f"- [{e.get('type')}] {e.get('reason')}: {e.get('message')}")
    if evidence.get("logs"):
        log_preview = evidence["logs"].strip().splitlines()[:8]
        evidence_lines.append("- 日志摘录:")
        evidence_lines.extend([f"  {ln}" for ln in log_preview])

    rb_titles = [r.get("title") or r.get("id") for r in runbooks]
    cmds = _suggest_commands(fault, ns, pod_name)

    report_json: dict[str, Any] = {
        "fault_type": fault,
        "title": titles.get(fault, titles["general"]),
        "phenomenon": alert_text
        or f"检测到疑似 {fault} 故障（Pod={pod_name}, ns={ns}）",
        "root_cause": root_causes.get(fault, root_causes["general"]),
        "evidence": evidence_lines,
        "runbooks_used": rb_titles,
        "suggestions": cmds,
        "risks": risks.get(fault, risks["general"]),
        "human_confirm_required": True,
        "mode": evidence.get("mode", "mock"),
    }

    runbook_lines = [f"- {t}" for t in rb_titles] or ["- （未检索到）"]
    suggestion_lines = []
    for i, c in enumerate(cmds, 1):
        if c.startswith("#"):
            suggestion_lines.append(f"{i}. {c}")
        else:
            suggestion_lines.append(f"{i}. `{c}`")
    md_parts = [
        f"# 诊断报告：{report_json['title']}",
        "",
        f"**模式**: `{report_json['mode']}` | **故障类型**: `{fault}` | **LLM**: 未使用（规则回退）",
        "",
        "## 现象",
        report_json["phenomenon"],
        "",
        "## 根因判断",
        report_json["root_cause"],
        "",
        "## 关键证据",
        *evidence_lines,
        "",
        "## 参考 Runbook",
        *runbook_lines,
        "",
        "## 建议操作（均需人工确认）",
        *suggestion_lines,
        "",
        "## 风险提示",
        report_json["risks"],
        "",
        "## 结构化 JSON",
        "```json",
        json.dumps(report_json, ensure_ascii=False, indent=2),
        "```",
    ]
    return "\n".join(md_parts), report_json


def _llm_report(
    fault: str,
    evidence: dict[str, Any],
    runbooks: list[dict[str, str]],
    alert_text: str,
) -> tuple[str, dict[str, Any]] | None:
    """调用兼容 OpenAI 的 Chat Completions；失败返回 None。"""
    if not config.has_llm():
        return None
    try:
        from openai import OpenAI

        system = _read_prompt("system.md")
        schema = _read_prompt("report_schema.md")
        user_payload = {
            "alert_text": alert_text,
            "detected_fault": fault,
            "evidence": {
                "namespace": evidence.get("namespace"),
                "pod_name": evidence.get("pod_name"),
                "describe": evidence.get("describe"),
                "events": evidence.get("events"),
                "logs": (evidence.get("logs") or "")[:3000],
            },
            "runbooks": [
                {"id": r.get("id"), "content": r.get("content", "")[:2000]}
                for r in runbooks
            ],
        }
        client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
        )
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system + "\n\n" + schema},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            temperature=0.2,
        )
        content = resp.choices[0].message.content or ""
        # 尝试抽取 JSON
        report_json: dict[str, Any] = {
            "fault_type": fault,
            "raw": content,
            "mode": evidence.get("mode", "mock"),
        }
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            try:
                report_json = json.loads(match.group(0))
                report_json.setdefault("fault_type", fault)
                report_json.setdefault("mode", evidence.get("mode", "mock"))
            except json.JSONDecodeError:
                pass
        md = content if content.strip().startswith("#") else (
            f"# 诊断报告\n\n{content}\n"
        )
        return md, report_json
    except Exception:  # noqa: BLE001
        return None


def node_collect(state: DiagnoseState) -> DiagnoseState:
    """取证节点。"""
    errors = list(state.get("errors") or [])
    try:
        evidence = collect_evidence(
            scenario=state.get("scenario"),
            pod_name=state.get("pod_name"),
            namespace=state.get("namespace"),
        )
        return {
            **state,
            "evidence": evidence,
            "scenario": evidence.get("scenario") or state.get("scenario"),
            "pod_name": evidence.get("pod_name") or state.get("pod_name"),
            "mode": evidence.get("mode", "mock"),
            "errors": errors,
        }
    except Exception as exc:  # noqa: BLE001
        errors.append(f"collect: {exc}")
        return {**state, "errors": errors, "evidence": state.get("evidence") or {}}


def node_retrieve(state: DiagnoseState) -> DiagnoseState:
    """RAG 检索节点。"""
    evidence = state.get("evidence") or {}
    query = build_query_from_evidence(evidence, state.get("alert_text") or "")
    if state.get("scenario"):
        query = f"{state['scenario']} {query}"
    runbooks = retrieve_runbooks(query, top_k=2)
    return {**state, "runbooks": runbooks}


def _replace_embedded_json(markdown: str, report_json: dict[str, Any]) -> str:
    """让文末 JSON 与 report_json 一致（含 remediation）。"""
    block = "```json\n" + json.dumps(report_json, ensure_ascii=False, indent=2) + "\n```"
    pattern = r"```json\n[\s\S]*?\n```"
    if re.search(pattern, markdown):
        return re.sub(pattern, block, markdown, count=1)
    return markdown


def _upsert_remediation_section(markdown: str, section: str) -> str:
    heading = "## 修复方案（需人工确认）"
    section = section.strip() + "\n"
    if heading in markdown:
        pattern = rf"{re.escape(heading)}[\s\S]*?(?=\n## |\Z)"
        return re.sub(pattern, section + "\n", markdown, count=1)
    for marker in ("## 风险提示", "## 结构化 JSON"):
        if marker in markdown:
            return markdown.replace(marker, section + "\n" + marker, 1)
    return markdown.rstrip() + "\n\n" + section


def _ensure_remediation(
    markdown: str,
    report_json: dict[str, Any] | None,
    fault: str,
    evidence: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """把目录中的修复提案写入报告。不执行，也不采用模型自由生成的命令。"""
    actions = build_remediation(
        fault,
        namespace=evidence.get("namespace") or config.K8S_NAMESPACE,
        pod_name=evidence.get("pod_name"),
    )
    report = dict(report_json or {})
    report["remediation"] = actions
    report["human_confirm_required"] = True
    report.setdefault("fault_type", fault)
    section = render_remediation_markdown(actions)
    markdown = _upsert_remediation_section(markdown, section)
    markdown = _replace_embedded_json(markdown, report)
    return markdown, report


def node_diagnose(state: DiagnoseState) -> DiagnoseState:
    """诊断报告节点。只产出提案，不调用 apply。"""
    evidence = state.get("evidence") or {}
    runbooks = state.get("runbooks") or []
    alert_text = state.get("alert_text") or ""
    fault = _detect_fault_type(evidence, alert_text)

    llm_result = _llm_report(fault, evidence, runbooks, alert_text)
    if llm_result:
        md, report_json = llm_result
        llm_used = True
    else:
        md, report_json = _rule_based_report(fault, evidence, runbooks, alert_text)
        llm_used = False

    md, report_json = _ensure_remediation(md, report_json, fault, evidence)

    return {
        **state,
        "report_markdown": md,
        "report_json": report_json,
        "llm_used": llm_used,
        "scenario": fault,
    }


def _run_state_machine(state: DiagnoseState) -> DiagnoseState:
    """纯 Python 三阶段状态机。"""
    state = node_collect(state)
    state = node_retrieve(state)
    state = node_diagnose(state)
    return state


def _run_langgraph(state: DiagnoseState) -> DiagnoseState:
    """使用 LangGraph 的简洁图；导入失败则抛出由上层回退。"""
    from langgraph.graph import END, StateGraph

    # 必须使用 TypedDict 通道，StateGraph(dict) 在新版不会正确合并字段
    graph = StateGraph(DiagnoseState)
    graph.add_node("collect", node_collect)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("diagnose", node_diagnose)
    graph.set_entry_point("collect")
    graph.add_edge("collect", "retrieve")
    graph.add_edge("retrieve", "diagnose")
    graph.add_edge("diagnose", END)
    app = graph.compile()
    result = app.invoke(dict(state))
    return result  # type: ignore[return-value]


def run_diagnose(
    scenario: str | None = None,
    alert_text: str = "",
    namespace: str | None = None,
    pod_name: str | None = None,
) -> DiagnoseState:
    """对外入口：执行完整诊断流水线。"""
    initial: DiagnoseState = {
        "scenario": config.normalize_scenario(scenario) or scenario,
        "alert_text": alert_text
        or (f"告警：检测到 {scenario} 相关 Pod 异常" if scenario else "告警：Pod 异常"),
        "namespace": namespace or config.K8S_NAMESPACE,
        "pod_name": pod_name,
        "evidence": {},
        "runbooks": [],
        "report_markdown": "",
        "report_json": {},
        "mode": "mock" if config.MOCK else "live",
        "llm_used": False,
        "errors": [],
    }

    try:
        return _run_langgraph(initial)
    except Exception:  # noqa: BLE001
        return _run_state_machine(initial)
