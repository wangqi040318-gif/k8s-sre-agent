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
from src.remediation.catalog import (
    CONFIGMAP_DATA,
    CONFIGMAP_NAME,
    FIX_IMAGE,
    MEMORY_LIMIT,
    PROBE_PATH,
    PROBE_PORT,
    SCHEDULE_CPU_LIMIT,
    SCHEDULE_CPU_REQUEST,
    SCHEDULE_MEMORY_LIMIT,
    SCHEDULE_MEMORY_REQUEST,
    SERVICE_NAME,
    SERVICE_SELECTOR,
)
from src.remediation.manifest import (
    build_configmap_key_patch,
    build_json_patch,
    build_selector_patch,
    pod_labels_match,
    pod_references_configmap,
    sanitized_pod_manifest,
)
from src.tools.mock_data import MOCK_PODS


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
    [
        ("imagepull", "fix-image"),
        ("oom", "bump-memory"),
        ("crashloop", "fix-command"),
        ("pending", "relax-scheduling"),
        ("unschedulable", "relax-scheduling"),
        ("configmount", "create-config"),
        ("createcontainerconfigerror", "create-config"),
        ("probe", "fix-probe"),
        ("connectivity", "fix-selector"),
        ("endpoints", "fix-selector"),
    ],
)
def test_each_demo_fault_proposes_its_action(scenario, action_id):
    result, actions = _remediation(scenario)
    assert [item["id"] for item in actions] == [action_id]
    assert result["report_json"]["human_confirm_required"] is True
    assert "修复方案（需人工确认）" in result["report_markdown"]
    assert get_mock_state() == {}


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


@pytest.mark.parametrize(
    ("action_id", "scenario"),
    [
        ("fix-image", "imagepull"),
        ("bump-memory", "oom"),
        ("fix-command", "crashloop"),
        ("relax-scheduling", "pending"),
        ("create-config", "configmount"),
        ("fix-probe", "probe"),
        ("fix-selector", "connectivity"),
    ],
)
def test_live_without_cluster_fails_closed(monkeypatch, action_id, scenario):
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

    result = apply_action(action_id, confirm="YES", scenario=scenario)
    assert result.ok is False
    assert result.simulated is False
    assert result.refused is False
    assert "未执行任何变更" in result.message
    assert get_mock_state() == {}


def test_oncall_actions_require_confirm_then_update_mock_only():
    refused = apply_action("relax-scheduling", confirm=None, scenario="pending")
    assert refused.refused is True
    assert get_mock_state() == {}

    pending = apply_action("relax-scheduling", confirm="YES", scenario="pending")
    assert pending.ok and pending.simulated
    state = get_mock_state()["default/demo-pending"]
    container = state["pod"]["spec"]["containers"][0]
    assert container["resources"]["requests"]["cpu"] == SCHEDULE_CPU_REQUEST
    assert container["resources"]["requests"]["memory"] == SCHEDULE_MEMORY_REQUEST
    assert container["resources"]["limits"]["cpu"] == SCHEDULE_CPU_LIMIT
    assert container["resources"]["limits"]["memory"] == SCHEDULE_MEMORY_LIMIT
    assert "affinity" not in state["pod"]["spec"]
    assert "nodeSelector" not in state["pod"]["spec"]
    assert state["pod"]["spec"]["tolerations"]

    reset_mock_state()
    mounted = apply_action("create-config", confirm="CONFIRM", scenario="configmount")
    assert mounted.ok and mounted.simulated
    configmaps = get_mock_state()["default/demo-configmount"]["configmaps"]
    assert configmaps[CONFIGMAP_NAME] == CONFIGMAP_DATA

    reset_mock_state()
    probed = apply_action("fix-probe", confirm="YES", scenario="probe")
    assert probed.ok and probed.simulated
    spec = get_mock_state()["default/demo-probe"]["pod"]["spec"]["containers"][0]
    for probe_name in ("readinessProbe", "livenessProbe"):
        http = spec[probe_name]["httpGet"]
        assert http["path"] == PROBE_PATH
        assert http["port"] == PROBE_PORT
        assert spec[probe_name]["periodSeconds"] == 5
        assert spec[probe_name]["failureThreshold"] == 3

    reset_mock_state()
    selected = apply_action("fix-selector", confirm="YES", scenario="connectivity")
    assert selected.ok and selected.simulated
    service = get_mock_state()["default/demo-connectivity"]["services"][SERVICE_NAME]
    assert service["selector"] == SERVICE_SELECTOR
    assert "NetworkPolicy" not in selected.command or "不改" in selected.command
    mismatch = apply_action("fix-selector", confirm="YES", scenario="pending")
    assert mismatch.refused is True


def test_scheduling_and_probe_patches_stay_narrow():
    container = {
        "name": "app",
        "image": "nginx:1.27",
        "resources": {
            "requests": {"cpu": "8", "memory": "16Gi", "ephemeral-storage": "1Gi"},
            "limits": {"cpu": "8", "memory": "16Gi"},
        },
        "readinessProbe": {
            "httpGet": {"path": "/healthz", "port": 8080},
            "periodSeconds": 5,
            "failureThreshold": 3,
        },
        "livenessProbe": {
            "httpGet": {"path": "/healthz", "port": 8080},
            "periodSeconds": 5,
            "failureThreshold": 3,
        },
    }
    pod_spec = {
        "containers": [container],
        "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {}}},
        "nodeSelector": {"demo.sre/pool": "oncall-pool-missing"},
        "tolerations": [{"key": "node.kubernetes.io/not-ready", "operator": "Exists"}],
    }
    ops = build_json_patch(
        container,
        0,
        {
            "op": "relax_scheduling",
            "container": "app",
            "cpu_request": SCHEDULE_CPU_REQUEST,
            "memory_request": SCHEDULE_MEMORY_REQUEST,
            "cpu_limit": SCHEDULE_CPU_LIMIT,
            "memory_limit": SCHEDULE_MEMORY_LIMIT,
            "remove_affinity": True,
            "remove_node_selector": True,
        },
        pod_spec=pod_spec,
    )
    paths = [item["path"] for item in ops]
    assert "/spec/template/spec/affinity" in paths
    assert "/spec/template/spec/nodeSelector" in paths
    assert not any("tolerations" in path for path in paths)
    assert not any("ephemeral-storage" in path for path in paths)
    cpu = next(item for item in ops if item["path"].endswith("requests/cpu"))
    assert cpu["value"] == SCHEDULE_CPU_REQUEST

    probe_ops = build_json_patch(
        container,
        0,
        {
            "op": "fix_probe",
            "container": "app",
            "path": PROBE_PATH,
            "port": PROBE_PORT,
            "probes": ["readinessProbe", "livenessProbe"],
        },
    )
    probe_paths = [item["path"] for item in probe_ops]
    assert probe_paths == [
        "/spec/template/spec/containers/0/readinessProbe/httpGet/path",
        "/spec/template/spec/containers/0/readinessProbe/httpGet/port",
        "/spec/template/spec/containers/0/livenessProbe/httpGet/path",
        "/spec/template/spec/containers/0/livenessProbe/httpGet/port",
    ]
    assert all(item["op"] == "replace" for item in probe_ops)

    manifest = sanitized_pod_manifest(
        MOCK_PODS["pending"],
        {
            "op": "relax_scheduling",
            "namespace": "default",
            "pod": "demo-pending",
            "container": "app",
            "cpu_request": SCHEDULE_CPU_REQUEST,
            "memory_request": SCHEDULE_MEMORY_REQUEST,
            "cpu_limit": SCHEDULE_CPU_LIMIT,
            "memory_limit": SCHEDULE_MEMORY_LIMIT,
            "remove_affinity": True,
            "remove_node_selector": True,
        },
    )
    assert "affinity" not in manifest["spec"]
    assert "nodeSelector" not in manifest["spec"]
    assert manifest["spec"]["tolerations"]
    assert manifest["spec"]["containers"][0]["resources"]["requests"]["cpu"] == SCHEDULE_CPU_REQUEST


def test_configmap_patch_does_not_overwrite_and_selector_replaces_map():
    ops = build_configmap_key_patch(
        {"LOG_LEVEL": "debug", "owner": "keep"},
        {"LOG_LEVEL": "info", "config.yaml": "mode: demo\n"},
    )
    assert ops == [
        {"op": "add", "path": "/data/config.yaml", "value": "mode: demo\n"},
    ]
    assert build_configmap_key_patch(
        {"LOG_LEVEL": "info", "config.yaml": "mode: demo\n"},
        dict(CONFIGMAP_DATA),
    ) == []
    assert build_configmap_key_patch(None, dict(CONFIGMAP_DATA)) == [
        {"op": "add", "path": "/data", "value": dict(CONFIGMAP_DATA)},
    ]

    selector_ops = build_selector_patch({"app": "frontend", "tier": "old"}, dict(SERVICE_SELECTOR))
    assert selector_ops == [
        {"op": "replace", "path": "/spec/selector", "value": {"app": "demo"}},
    ]
    assert "ports" not in selector_ops[0]["path"]
    assert pod_references_configmap(MOCK_PODS["configmount"], CONFIGMAP_NAME)
    assert not pod_references_configmap(MOCK_PODS["configmount"], "other")
    assert pod_labels_match(MOCK_PODS["connectivity"], SERVICE_SELECTOR)
    assert not pod_labels_match(MOCK_PODS["connectivity"], {"app": "frontend"})


@pytest.mark.parametrize(
    ("scenario", "needle"),
    [
        ("pending", "pending"),
        ("configmount", "configmount"),
        ("probe", "probe"),
        ("connectivity", "endpoint"),
    ],
)
def test_runbook_retrieved_for_oncall_faults(scenario, needle):
    result, _actions = _remediation(scenario)
    ids = " ".join(item.get("id", "") for item in result["runbooks"])
    assert needle in ids
    assert get_mock_state() == {}


def test_remediation_package_does_not_shell_out():
    from pathlib import Path

    blob = "\n".join(path.read_text(encoding="utf-8") for path in Path("src/remediation").glob("*.py"))
    assert "shell=True" not in blob
    assert "os.system" not in blob
