"""Streamlit：执行按钮在输入 CONFIRM 之前保持禁用。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ["MOCK"] = "1"
os.environ.pop("OPENAI_API_KEY", None)
os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")

from streamlit.testing.v1 import AppTest

_APP = Path(__file__).resolve().parents[1] / "src" / "ui" / "app.py"


def _button(at: AppTest, label: str):
    matches = [button for button in at.button if button.label == label]
    assert matches, f"找不到按钮 {label}"
    return matches[0]


def _confirm_box(at: AppTest):
    matches = [box for box in at.text_input if "CONFIRM" in (box.label or "")]
    assert matches, "找不到 CONFIRM 输入框"
    return matches[0]


def test_execute_stays_disabled_until_confirm_typed():
    at = AppTest.from_file(str(_APP), default_timeout=60)
    at.run()
    assert _button(at, "运行诊断")
    assert not any(button.label == "执行修复" for button in at.button)

    at = _button(at, "运行诊断").click().run()
    assert "ImagePull" in "\n".join(item.value for item in at.markdown)
    execute = _button(at, "执行修复")
    assert execute.proto.disabled is True

    at = _confirm_box(at).set_value("yes").run()
    assert _button(at, "执行修复").proto.disabled is True

    at = _confirm_box(at).set_value("CONFIRM").run()
    execute = _button(at, "执行修复")
    assert execute.proto.disabled is False

    at = at.checkbox[0].check().run()
    at = _button(at, "执行修复").click().run()
    assert any("模拟" in item.value for item in at.success)
