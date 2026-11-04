"""诊断流水线状态定义。"""

from __future__ import annotations

from typing import Any, TypedDict


class DiagnoseState(TypedDict, total=False):
    """Agent 状态：告警 → 取证 → 检索 → 报告。"""

    scenario: str | None
    alert_text: str
    namespace: str
    pod_name: str | None
    evidence: dict[str, Any]
    runbooks: list[dict[str, str]]
    report_markdown: str
    report_json: dict[str, Any]
    mode: str  # mock | live
    llm_used: bool
    errors: list[str]
