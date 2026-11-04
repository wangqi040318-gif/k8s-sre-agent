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


_LABELS = [
    "ImagePullBackOff",
    "OOMKilled",
    "CrashLoopBackOff",
    "Pending / 无法调度",
    "ConfigMap 挂载失败",
    "探针未就绪",
    "Service 无 Endpoints",
]

_ALERT_NEEDLES = {
    "imagepull": "ImagePullBackOff",
    "oom": "OOMKilled",
    "crashloop": "CrashLoopBackOff",
    "pending": "FailedScheduling",
    "configmount": "app-config",
    "probe": "healthz",
    "connectivity": "Endpoints",
}


def _scenario_box(at: AppTest):
    matches = [box for box in at.selectbox if box.label == "故障场景"]
    assert matches, "找不到故障场景下拉框"
    return matches[0]


def test_sidebar_selectbox_lists_every_scenario():
    at = AppTest.from_file(str(_APP), default_timeout=60).run()
    assert not at.exception
    box = _scenario_box(at)
    assert list(box.options) == _LABELS

    for scenario_id, needle in _ALERT_NEEDLES.items():
        at = _scenario_box(at).set_value(scenario_id).run()
        assert not at.exception
        alerts = [area.value or "" for area in at.text_area]
        assert any(needle in text for text in alerts), scenario_id


def test_sidebar_loads_when_config_module_predates_scenario_choices():
    """Cloud 会重跑 app.py，但进程里的 src.config 可能还是扩展场景之前的模块。"""
    import src.config as config

    saved_choices = getattr(config, "SCENARIO_CHOICES", None)
    saved_labels = getattr(config, "SCENARIO_LABELS", None)
    if hasattr(config, "SCENARIO_CHOICES"):
        delattr(config, "SCENARIO_CHOICES")
    if hasattr(config, "SCENARIO_LABELS"):
        delattr(config, "SCENARIO_LABELS")
    try:
        at = AppTest.from_file(str(_APP), default_timeout=60).run()
        assert not at.exception
        assert list(_scenario_box(at).options) == _LABELS
        assert hasattr(config, "SCENARIO_CHOICES")
        assert list(config.SCENARIO_CHOICES) == list(_ALERT_NEEDLES)

        at = _scenario_box(at).set_value("connectivity").run()
        at = _button(at, "运行诊断").click().run()
        assert not at.exception
        blob = "\n".join(item.value for item in at.markdown)
        assert "Endpoints" in blob or "fix-selector" in blob
        execute = _button(at, "执行修复")
        assert execute.proto.disabled is True

        at = _confirm_box(at).set_value("yes").run()
        assert _button(at, "执行修复").proto.disabled is True
        at = _confirm_box(at).set_value("CONFIRM").run()
        assert _button(at, "执行修复").proto.disabled is False
    finally:
        if saved_choices is not None:
            config.SCENARIO_CHOICES = saved_choices
        if saved_labels is not None:
            config.SCENARIO_LABELS = saved_labels


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
