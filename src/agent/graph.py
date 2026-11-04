"""诊断流水线：优先 LangGraph，不可用时回退纯 Python 状态机。"""

from __future__ import annotations

import json
import re
from typing import Any

from src import config
from src.agent.state import DiagnoseState
from src.rag.retrieve import build_query_from_evidence, retrieve_runbooks
from src.tools.k8s_tools import collect_evidence


def _read_prompt(name: str) -> str:
    path = config.PROMPTS_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _detect_fault_type(evidence: dict[str, Any], alert_text: str = "") -> str:
    """根据证据/告警启发式判定故障类型。"""
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
        "general": "证据不足以自动判定，请结合 Events 与日志继续人工排查。",
    }
    risks = {
        "imagepull": "盲目改镜像 Tag 或共享凭证可能导致更多错误部署；生产变更需审批。",
        "oom": "直接调高 limit 可能掩盖泄漏并挤占节点；删除 Pod 会短暂中断流量。",
        "crashloop": "频繁删重建可能丢失排障窗口；错误改 ConfigMap 会影响同命名空间工作负载。",
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


def node_diagnose(state: DiagnoseState) -> DiagnoseState:
    """诊断报告节点。"""
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
