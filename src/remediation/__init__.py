"""人工确认后的修复提案与执行。

诊断阶段只生成 remediation 提案，不会改集群。
真正的变更只能通过 apply_action，且必须带上显式确认令牌。
"""

from src.remediation.apply import apply_action, get_mock_state, reset_mock_state
from src.remediation.catalog import build_remediation, render_remediation_markdown

__all__ = [
    "apply_action",
    "build_remediation",
    "get_mock_state",
    "render_remediation_markdown",
    "reset_mock_state",
]
