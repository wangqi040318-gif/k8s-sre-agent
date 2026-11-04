"""确认门闩：诊断只提案；未确认拒绝；MOCK 下确认后模拟成功。"""

from __future__ import annotations

import json
import logging
import os
import sys

os.environ["MOCK"] = "1"
os.environ.pop("OPENAI_API_KEY", None)

import pytest

from src.agent.graph import run_diagnose
from src.cli import main
from src.rag.ingest import build_index
from src.remediation.apply import apply_action, get_mock_state, reset_mock_state
from src.remediation.catalog import FIX_IMAGE, MEMORY_LIMIT
from src.remediation.manifest import build_json_patch, sanitized_pod_manifest


@pytest.fixture(autouse=True)
def _clean_mock_state():
    reset_mock_state()
    yield
    reset_mock_state()


def _remediation(scenario: str) -> list[dict]:
    build_index()
    result = run_diagnose(scenario=scenario, alert_text=f"告警：{scenario}")
    actions = (result.get("report_json") or {}).get("remediation") or []
    return result, actions


def test_diagnose_still_works_and_includes_remediation():
    result, actions = _remediation("imagepull")
    blob = (result.get("report_markdown") or "") + json.dumps(
        result.get("report_json") or {}, ensure_ascii=False
    )
    assert "ImagePull" in blob or "imagepull" in blob.lower()
    assert result.get("llm_used") is False
    assert result.get("evidence", {}).get("mode") == "mock"
    assert result["report_json"]["human_confirm_required"] is True
    assert actions and actions[0]["id"] == "fix-image"
    for key in ("id", "description", "command", "api_op", "risk_level", "reversible"):
        assert key in actions[0]
    assert actions[0]["risk_level"] in {"low", "medium", "high"}
    assert actions[0]["reversible"] is True
    assert "修复方案（需人工确认）" in result["report_markdown"]
    assert get_mock_state() == {}


@pytest.mark.parametrize(
    ("scenario", "action_id"),
    [("imagepull", "fix-image"), ("oom", "bump-memory"), ("crashloop", "fix-command")],
)
def test_each_demo_fault_proposes_its_action(scenario, action_id):
    _result, actions = _remediation(scenario)
    assert [item["id"] for item in actions] == [action_id]


def test_diagnose_cli_does_not_apply(capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    code = main(["--scenario", "oom"])
    captured = capsys.readouterr()
    assert code == 0
    assert "bump-memory" in captured.out
    assert "修复方案（需人工确认）" in captured.out
    assert "已模拟执行" not in captured.out
    assert get_mock_state() == {}


def test_apply_without_confirm_is_refused(caplog):
    caplog.set_level(logging.INFO)
    result = apply_action("fix-image", confirm=None, scenario="imagepull")
    assert result.ok is False
    assert result.refused is True
    assert result.simulated is False
    assert "拒绝" in result.message
    assert get_mock_state() == {}

    lowercase = apply_action("fix-image", confirm="yes", scenario="imagepull")
    assert lowercase.refused is True
    assert get_mock_state() == {}
    assert "decision=refused_unconfirmed" in caplog.text


def test_apply_unknown_action_is_refused():
    result = apply_action("drop-database", confirm="YES")
    assert result.refused is True
    assert result.ok is False
    assert "未知动作" in result.message
    assert get_mock_state() == {}

    mismatch = apply_action("fix-image", confirm="YES", scenario="oom")
    assert mismatch.refused is True
    assert get_mock_state() == {}


def test_apply_with_confirm_mock_is_simulated():
    image = apply_action("fix-image", confirm="YES", scenario="imagepull")
    assert image.ok is True
    assert image.simulated is True
    assert image.refused is False
    assert "模拟" in image.message
    state = get_mock_state()
    blob = json.dumps(state, ensure_ascii=False)
    assert FIX_IMAGE in blob
    assert "demo-imagepull" in blob

    memory = apply_action("bump-memory", confirm="CONFIRM", scenario="oom")
    assert memory.ok and memory.simulated
    assert MEMORY_LIMIT in json.dumps(get_mock_state(), ensure_ascii=False)

    command = apply_action("fix-command", confirm="YES", scenario="crashloop")
    assert command.ok and command.simulated
    assert "echo healthy; sleep 3600" in json.dumps(get_mock_state(), ensure_ascii=False)


def test_cli_apply_without_confirm_refuses(capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    code = main(["--apply", "--action", "fix-image", "--scenario", "imagepull"])
    captured = capsys.readouterr()
    assert code == 2
    assert "拒绝" in captured.out
    assert get_mock_state() == {}
    assert "apply_audit" in captured.err
    assert "decision=refused_unconfirmed" in captured.err


def test_cli_apply_confirm_yes_simulates(capsys, monkeypatch):
    def _forbid_prompt(_prompt=""):
        raise AssertionError("已提供 --confirm YES，不应再交互询问")

    monkeypatch.setattr("builtins.input", _forbid_prompt)
    code = main(
        ["--apply", "--confirm", "YES", "--action", "fix-image", "--scenario", "imagepull"]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "已模拟执行" in captured.out
    assert FIX_IMAGE in json.dumps(get_mock_state(), ensure_ascii=False)
    assert "decision=simulated" in captured.err


def test_cli_interactive_confirm(capsys, monkeypatch):
    prompts: list[str] = []

    def _input(prompt=""):
        prompts.append(prompt)
        return "CONFIRM"

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _input)
    code = main(["--apply", "--action", "bump-memory", "--scenario", "oom"])
    captured = capsys.readouterr()
    assert code == 0
    assert prompts and "输入 CONFIRM 后执行" in prompts[0]
    assert "已模拟执行" in captured.out


def test_cli_interactive_wrong_token_refuses(capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")
    code = main(["--apply", "--action", "fix-command", "--scenario", "crashloop"])
    captured = capsys.readouterr()
    assert code == 2
    assert "拒绝" in captured.out
    assert get_mock_state() == {}


def test_cli_apply_requires_action(capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    code = main(["--apply", "--confirm", "YES"])
    captured = capsys.readouterr()
    assert code == 2
    assert "拒绝" in captured.out


def test_llm_text_cannot_replace_catalog_actions(monkeypatch):
    monkeypatch.setattr(
        "src.agent.graph._llm_report",
        lambda *args, **kwargs: (
            "# 诊断报告\n\n模型只是在说话\n",
            {"fault_type": "imagepull", "suggestions": ["kubectl delete ns kube-system"]},
        ),
    )
    result = run_diagnose(scenario="imagepull", alert_text="ImagePullBackOff")
    actions = result["report_json"]["remediation"]
    assert actions[0]["id"] == "fix-image"
    assert "kube-system" not in actions[0]["command"]
    assert "fix-image" in result["report_markdown"]
    assert get_mock_state() == {}


def test_json_patch_changes_only_target_field():
    container = {
        "name": "app",
        "image": "nginx:bad",
        "resources": {
            "limits": {"memory": "64Mi", "cpu": "200m"},
            "requests": {"memory": "32Mi"},
        },
        "command": ["sh", "-c", "exit 1"],
        "livenessProbe": {"httpGet": {"path": "/", "port": 80}},
    }
    memory_ops = build_json_patch(
        container,
        0,
        {"op": "bump_memory", "container": "app", "memory_limit": "256Mi"},
    )
    assert memory_ops == [
        {
            "op": "replace",
            "path": "/spec/template/spec/containers/0/resources/limits/memory",
            "value": "256Mi",
        }
    ]
    command_ops = build_json_patch(
        container,
        0,
        {
            "op": "fix_command",
            "container": "app",
            "command": ["sh", "-c", "echo healthy; sleep 3600"],
            "remove_probes": True,
        },
    )
    paths = [item["path"] for item in command_ops]
    assert "/spec/template/spec/containers/0/command" in paths
    assert "/spec/template/spec/containers/0/livenessProbe" in paths
    assert "/spec/template/spec/containers/0/resources/limits/cpu" not in paths


def test_sanitized_pod_manifest_rewrites_image_and_drops_status():
    pod = {
        "metadata": {
            "name": "demo-imagepull",
            "namespace": "default",
            "resourceVersion": "9",
            "uid": "abc",
            "labels": {"app": "demo"},
            "annotations": {"kubernetes.io/config.seen": "1", "demo": "keep"},
        },
        "spec": {
            "nodeName": "node-a",
            "containers": [
                {
                    "name": "app",
                    "image": "nginx:this-tag-does-not-exist-xyz",
                    "volumeMounts": [{"name": "kube-api-access-xxx", "mountPath": "/var/run/secrets"}],
                }
            ],
            "volumes": [{"name": "kube-api-access-xxx", "projected": {}}],
        },
        "status": {"phase": "Pending"},
    }
    manifest = sanitized_pod_manifest(
        pod,
        {
            "op": "fix_image",
            "namespace": "default",
            "pod": "demo-imagepull",
            "container": "app",
            "image": FIX_IMAGE,
        },
    )
    assert manifest["kind"] == "Pod"
    assert "status" not in manifest
    assert "resourceVersion" not in manifest["metadata"]
    assert manifest["spec"]["containers"][0]["image"] == FIX_IMAGE
    assert "nodeName" not in manifest["spec"]
    assert "volumes" not in manifest["spec"]
    assert manifest["metadata"]["annotations"] == {"demo": "keep"}


def test_live_without_cluster_fails_closed(monkeypatch):
    monkeypatch.setattr("src.config.MOCK", False)

    def _no_client():
        return None

    def _no_kubectl(_name):
        return None

    def _forbid_subprocess(*args, **kwargs):
        raise AssertionError(f"未确认到集群时不应调用子进程: {args} {kwargs}")

    monkeypatch.setattr("src.k8s.client.get_core_v1", _no_client)
    monkeypatch.setattr("src.k8s.client.get_apps_v1", _no_client)
    monkeypatch.setattr("src.remediation.live.shutil.which", _no_kubectl)
    monkeypatch.setattr("src.remediation.live.subprocess.run", _forbid_subprocess)

    result = apply_action("fix-image", confirm="YES", scenario="imagepull")
    assert result.ok is False
    assert result.simulated is False
    assert result.refused is False
    assert "未执行任何变更" in result.message
    assert get_mock_state() == {}


def test_remediation_package_does_not_shell_out():
    from pathlib import Path

    blob = "\n".join(path.read_text(encoding="utf-8") for path in Path("src/remediation").glob("*.py"))
    assert "shell=True" not in blob
    assert "os.system" not in blob
