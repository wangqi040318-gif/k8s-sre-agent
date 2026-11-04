"""Mock 模式下 imagepull 场景诊断应包含 ImagePull 相关关键词。"""

from __future__ import annotations

import os

# 强制 Mock，避免测试环境误连集群
os.environ["MOCK"] = "1"
# 确保无 key 时走规则回退（测试不依赖外网 LLM）
os.environ.pop("OPENAI_API_KEY", None)

from src.agent.graph import run_diagnose  # noqa: E402
from src.rag.ingest import build_index  # noqa: E402
from src.tools.k8s_tools import collect_evidence  # noqa: E402


def test_mock_imagepull_report_contains_imagepull_keyword():
    build_index()
    result = run_diagnose(
        scenario="imagepull",
        alert_text="告警：demo-imagepull 处于 ImagePullBackOff",
    )
    md = result.get("report_markdown") or ""
    blob = md + " " + str(result.get("report_json") or {})
    assert "ImagePull" in blob or "imagepull" in blob.lower()
    assert result.get("llm_used") is False
    assert (result.get("mode") or result.get("evidence", {}).get("mode")) in {
        "mock",
        None,
    } or result.get("evidence", {}).get("mode") == "mock"


def test_mock_collect_evidence_imagepull():
    evidence = collect_evidence(scenario="imagepull")
    assert evidence["mode"] == "mock"
    describe = evidence["describe"]
    assert "ImagePullBackOff" in describe
