"""Streamlit UI：诊断后展示修复提案，输入 CONFIRM 才能执行。"""

from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

# `streamlit run src/ui/app.py` 会把脚本目录放进 sys.path，而不是仓库根。
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from src import config
from src.scenarios import SCENARIO_CHOICES, scenario_label

# Community Cloud 更新仓库后会重跑这个文件，但不会丢掉进程里已经导入的 src.config。
# 扩展场景之前的那个模块有 MOCK / has_llm，没有 SCENARIO_CHOICES。
# 侧栏若直接读旧对象，selectbox 会在启动时 AttributeError。
# reload 改的是同一个模块对象，修复目录里随后读取的 config.SCENARIO_CHOICES 也会就位。
_choices = getattr(config, "SCENARIO_CHOICES", None)
_labels = getattr(config, "SCENARIO_LABELS", None)
if (
    not _choices
    or not isinstance(_labels, dict)
    or list(_choices) != list(SCENARIO_CHOICES)
    or any(scenario_id not in _labels for scenario_id in SCENARIO_CHOICES)
):
    importlib.reload(config)

from src.agent.graph import run_diagnose
from src.rag.ingest import build_index
from src.remediation.apply import CONFIRM_TYPED, apply_action

logger = logging.getLogger("src.remediation")
logger.setLevel(logging.INFO)

st.set_page_config(page_title="K8s SRE Agent", page_icon="🩺", layout="wide")

# 跟场景走。切换下拉时用新的 widget key，避免 Streamlit 记住上一个场景的告警文本。
_SAMPLE_ALERTS = {
    "imagepull": (
        "告警：Pod/demo-imagepull 处于 ImagePullBackOff，"
        "镜像 nginx:this-tag-does-not-exist-xyz 拉取失败"
    ),
    "oom": "告警：Pod/demo-oom 反复重启，lastState=OOMKilled，exitCode=137，memory limit 64Mi",
    "crashloop": (
        "告警：Pod/demo-crashloop CrashLoopBackOff，退出码 1，日志缺少 /etc/app/config.yaml"
    ),
    "pending": (
        "告警：Pod/demo-pending 持续 Pending，FailedScheduling："
        "CPU/内存请求过大，且 node affinity/selector 对不上任何节点"
    ),
    "configmount": (
        "告警：Pod/demo-configmount CreateContainerConfigError / FailedMount，"
        "ConfigMap app-config 不存在"
    ),
    "probe": (
        "告警：Pod/demo-probe 处于 Running 但未 Ready，"
        "readiness/liveness 访问 :8080/healthz 失败，应用监听 80"
    ),
    "connectivity": (
        "告警：Service/demo-web Endpoints 为空，选择器 app=frontend 与 Pod 标签 app=demo 不一致"
    ),
}

st.title("🩺 K8s 故障诊断 Agent")
st.caption(
    "传智杯作品骨架 · 默认 MOCK 模式（无需真实集群）· "
    f"当前 MOCK={'开' if config.MOCK else '关'} · "
    f"LLM={'已配置' if config.has_llm() else '未配置（规则回退）'}"
)

if "report" not in st.session_state:
    st.session_state.report = None
    st.session_state.report_scenario = None
    st.session_state.report_namespace = None
    st.session_state.report_pod = None

with st.sidebar:
    st.header("运行设置")
    scenario = st.selectbox(
        "故障场景",
        options=SCENARIO_CHOICES,
        format_func=scenario_label,
    )
    alert = st.text_area(
        "告警文本（可选）",
        value=_SAMPLE_ALERTS.get(scenario, ""),
        height=110,
        key=f"alert-{scenario}",
    )
    namespace = st.text_input("Namespace", value=config.K8S_NAMESPACE)
    pod = st.text_input("Pod 名称（可选，Mock 可留空）", value="")
    run_btn = st.button("运行诊断", type="primary")
    st.markdown("---")
    st.markdown(
        """
**说明**
- `MOCK=1`：使用内置假数据，适合演示与 CI。
- `MOCK=0` + 有效 `KUBECONFIG`：对真实集群取证。
- 无 `OPENAI_API_KEY` 时自动走规则模板报告。
- 诊断只提案。勾选动作并输入 `CONFIRM` 后才会执行修复。
"""
    )

if run_btn:
    with st.spinner("取证 → 检索 Runbook → 生成报告..."):
        build_index()
        result = run_diagnose(
            scenario=scenario,
            alert_text=alert,
            namespace=namespace or None,
            pod_name=pod or None,
        )
    st.session_state.report = result
    st.session_state.report_scenario = scenario
    st.session_state.report_namespace = namespace or None
    st.session_state.report_pod = pod or None

result = st.session_state.report
if not result:
    st.info("请在左侧选择场景后点击「运行诊断」。诊断不会自动改动集群。")
else:
    st.subheader("诊断报告")
    st.markdown(result.get("report_markdown") or "_无报告_")
    with st.expander("结构化 JSON"):
        st.json(result.get("report_json") or {})
    with st.expander("原始证据"):
        st.json(result.get("evidence") or {})
    if result.get("errors"):
        st.warning(result["errors"])

    report_json = result.get("report_json") or {}
    actions = report_json.get("remediation") or []
    evidence = result.get("evidence") or {}
    st.subheader("修复方案（需人工确认）")
    st.caption(
        "诊断不会自动改集群。请勾选动作，在文本框中输入 CONFIRM，再点击「执行修复」。"
        "未输入 CONFIRM 时按钮保持禁用。"
    )
    if config.MOCK:
        st.caption("当前为 MOCK 模式：执行只会模拟成功，并更新内存中的 Mock 状态。")
    else:
        st.warning("当前 MOCK=0。确认后的执行会修改真实集群，请只在演示命名空间操作。")

    if not actions:
        st.info("当前报告没有可执行的修复动作。")
    else:
        selected_ids: list[str] = []
        for action in actions:
            checked = st.checkbox(
                f"{action['id']}：{action['description']}",
                key=f"remediation-{action['id']}",
            )
            st.code(action.get("command") or "", language="bash")
            reversible = "是" if action.get("reversible") else "否"
            st.caption(
                f"风险等级：{action.get('risk_level', 'unknown')} · 可回滚：{reversible}"
            )
            if checked:
                selected_ids.append(action["id"])

        confirm_text = st.text_input(
            "输入 CONFIRM 后执行",
            value="",
            key="remediation_confirm",
            placeholder="CONFIRM",
        )
        unlocked = confirm_text.strip() == CONFIRM_TYPED
        execute = st.button(
            "执行修复",
            type="primary",
            disabled=not unlocked,
            key="remediation_execute",
        )
        if execute:
            if confirm_text.strip() != CONFIRM_TYPED:
                st.error("未输入 CONFIRM，已拒绝执行。集群未被修改。")
            elif not selected_ids:
                st.warning("请先勾选要执行的修复动作。未执行任何变更。")
            else:
                apply_scenario = st.session_state.report_scenario or scenario
                apply_namespace = evidence.get("namespace") or st.session_state.report_namespace
                apply_pod = evidence.get("pod_name") or st.session_state.report_pod
                for action_id in selected_ids:
                    applied = apply_action(
                        action_id,
                        confirm=CONFIRM_TYPED,
                        scenario=apply_scenario,
                        namespace=apply_namespace,
                        pod_name=apply_pod,
                    )
                    # 结果正文里的 kubectl 注释以 # 开头，不能放进 st.success（会被当成 Markdown 标题）。
                    lines = [line for line in applied.message.splitlines() if line.strip()]
                    headline = lines[0] if lines else applied.message
                    detail = "\n".join(lines[1:])
                    if applied.ok:
                        st.success(headline)
                    else:
                        st.error(headline)
                    if detail:
                        st.code(detail, language="text")
