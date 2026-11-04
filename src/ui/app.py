"""Streamlit UI：选择场景 → 运行诊断 → 展示报告。"""

from __future__ import annotations

import streamlit as st

from src import config
from src.agent.graph import run_diagnose
from src.rag.ingest import build_index

st.set_page_config(page_title="K8s SRE Agent", page_icon="🩺", layout="wide")

st.title("🩺 K8s 故障诊断 Agent")
st.caption(
    "传智杯作品骨架 · 默认 MOCK 模式（无需真实集群）· "
    f"当前 MOCK={'开' if config.MOCK else '关'} · "
    f"LLM={'已配置' if config.has_llm() else '未配置（规则回退）'}"
)

with st.sidebar:
    st.header("运行设置")
    scenario = st.selectbox(
        "故障场景",
        options=["imagepull", "oom", "crashloop"],
        format_func=lambda x: {
            "imagepull": "ImagePullBackOff",
            "oom": "OOMKilled",
            "crashloop": "CrashLoopBackOff",
        }[x],
    )
    alert = st.text_area(
        "告警文本（可选）",
        value=f"Prometheus 告警：Pod 处于异常状态，疑似 {scenario}",
        height=100,
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
    st.subheader("诊断报告")
    st.markdown(result.get("report_markdown") or "_无报告_")
    with st.expander("结构化 JSON"):
        st.json(result.get("report_json") or {})
    with st.expander("原始证据"):
        st.json(result.get("evidence") or {})
    if result.get("errors"):
        st.warning(result["errors"])
else:
    st.info("请在左侧选择场景后点击「运行诊断」。")
