"""确认门闩与 MOCK 模拟执行。未确认时不会进入真实集群路径。"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from src import config
from src.remediation.catalog import resolve_action
from src.tools import mock_data

logger = logging.getLogger("src.remediation")

# CLI `--confirm` 只接受 YES；交互输入与 Streamlit 文本框只接受 CONFIRM。
# apply_action 两者都认，调用方自己决定展示哪一种。
CONFIRM_FLAG = "YES"
CONFIRM_TYPED = "CONFIRM"

# 进程内 Mock 状态。诊断不会写这里；只有确认后的模拟执行才会更新。
_MOCK_RUNTIME: dict[str, dict[str, Any]] = {}


@dataclass
class ApplyResult:
    """一次 apply 尝试的结果。refused=True 表示门闩未通过，集群未被修改。"""

    ok: bool
    refused: bool
    simulated: bool
    action_id: str
    message: str
    command: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "refused": self.refused,
            "simulated": self.simulated,
            "action_id": self.action_id,
            "message": self.message,
            "command": self.command,
            "detail": self.detail,
        }


def is_explicit_confirm(value: str | None) -> bool:
    """是否为允许的确认令牌。大小写敏感，避免误触。"""
    if value is None:
        return False
    return value.strip() in {CONFIRM_FLAG, CONFIRM_TYPED}


def reset_mock_state() -> None:
    """清空模拟执行留下的状态。测试使用。"""
    _MOCK_RUNTIME.clear()


def get_mock_state() -> dict[str, Any]:
    """返回 Mock 状态副本。"""
    return copy.deepcopy(_MOCK_RUNTIME)


def _audit(
    *,
    action_id: str,
    scenario: str,
    namespace: str,
    pod: str,
    confirmed: bool,
    decision: str,
) -> None:
    logger.info(
        "apply_audit action=%s scenario=%s namespace=%s pod=%s mock=%s confirmed=%s decision=%s",
        action_id or "-",
        scenario or "-",
        namespace or "-",
        pod or "-",
        "1" if config.MOCK else "0",
        "yes" if confirmed else "no",
        decision,
    )


def _result(
    *,
    ok: bool,
    refused: bool,
    simulated: bool,
    action_id: str,
    message: str,
    command: str = "",
    detail: dict[str, Any] | None = None,
    scenario: str = "",
    namespace: str = "",
    pod: str = "",
    confirmed: bool = False,
    decision: str,
) -> ApplyResult:
    _audit(
        action_id=action_id,
        scenario=scenario,
        namespace=namespace,
        pod=pod,
        confirmed=confirmed,
        decision=decision,
    )
    return ApplyResult(
        ok=ok,
        refused=refused,
        simulated=simulated,
        action_id=action_id,
        message=message,
        command=command,
        detail=detail or {},
    )


def _mock_summary(entry: dict[str, Any]) -> str:
    pod = entry.get("pod") or {}
    meta = pod.get("metadata") or {}
    bits = [f"{meta.get('namespace', 'default')}/{meta.get('name', '?')}"]
    for container in (pod.get("spec") or {}).get("containers") or []:
        limits = ((container.get("resources") or {}).get("limits") or {})
        parts = [f"image={container.get('image')}"]
        if limits.get("memory"):
            parts.append(f"memory={limits['memory']}")
        if container.get("command"):
            parts.append(f"command={container['command']}")
        bits.append(f"{container.get('name')}: " + " ".join(parts))
    return "；".join(bits)


def _simulate(action: dict[str, Any]) -> dict[str, Any]:
    api_op = action["api_op"]
    scenario = action["scenario"]
    namespace = api_op["namespace"]
    pod_name = api_op["pod"]
    key = f"{namespace}/{pod_name}"
    if key not in _MOCK_RUNTIME:
        template = copy.deepcopy(mock_data.MOCK_PODS[scenario])
        template.setdefault("metadata", {})
        template["metadata"]["name"] = pod_name
        template["metadata"]["namespace"] = namespace
        _MOCK_RUNTIME[key] = {"pod": template, "applied_actions": []}

    entry = _MOCK_RUNTIME[key]
    containers = entry["pod"].setdefault("spec", {}).setdefault("containers", [])
    target = None
    for container in containers:
        if container.get("name") == api_op["container"]:
            target = container
            break
    if target is None:
        target = {"name": api_op["container"]}
        containers.append(target)

    op = api_op["op"]
    if op == "fix_image":
        target["image"] = api_op["image"]
    elif op == "bump_memory":
        target.setdefault("resources", {}).setdefault("limits", {})["memory"] = api_op[
            "memory_limit"
        ]
    elif op == "fix_command":
        target["command"] = list(api_op["command"])
        target.pop("args", None)
        for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
            target.pop(probe, None)
    else:
        raise RuntimeError(f"不支持的 API 操作: {op}")

    status = entry["pod"].setdefault("status", {})
    status["phase"] = "Running"
    for container_status in status.get("containerStatuses") or []:
        container_status["ready"] = True
        container_status["state"] = {"running": {"startedAt": "simulated"}}
    entry["applied_actions"].append(action["id"])
    entry["phase"] = "simulated-fixed"
    entry["last_command"] = action["command"]
    return copy.deepcopy(entry)


def apply_action(
    action_id: str | None,
    *,
    confirm: str | None = None,
    scenario: str | None = None,
    namespace: str | None = None,
    pod_name: str | None = None,
) -> ApplyResult:
    """执行或拒绝一个修复动作。

    默认 confirm=None，一定会拒绝。诊断代码不应调用本函数。
    """
    confirmed = is_explicit_confirm(confirm)
    action, error = resolve_action(
        action_id,
        scenario=scenario,
        namespace=namespace,
        pod_name=pod_name,
    )
    resolved_id = action["id"] if action else (action_id or "").strip()
    resolved_scenario = action["scenario"] if action else (scenario or "")
    api_op = (action or {}).get("api_op") or {}
    ns = api_op.get("namespace", namespace or "")
    pod = api_op.get("pod", pod_name or "")

    if error or action is None:
        reason = error or "未知动作。"
        return _result(
            ok=False,
            refused=True,
            simulated=False,
            action_id=resolved_id,
            message=f"已拒绝执行，集群未被修改。\n原因: {reason}",
            scenario=resolved_scenario,
            namespace=ns,
            pod=pod,
            confirmed=confirmed,
            decision="refused_unknown",
        )

    if not confirmed:
        return _result(
            ok=False,
            refused=True,
            simulated=False,
            action_id=action["id"],
            message=(
                "已拒绝执行，集群未被修改。\n"
                "原因: 未得到有效确认。请使用 `--confirm YES`，"
                "或在提示「输入 CONFIRM 后执行」时输入 CONFIRM。"
            ),
            command=action["command"],
            scenario=action["scenario"],
            namespace=ns,
            pod=pod,
            confirmed=False,
            decision="refused_unconfirmed",
        )

    if config.MOCK:
        entry = _simulate(action)
        summary = _mock_summary(entry)
        return _result(
            ok=True,
            refused=False,
            simulated=True,
            action_id=action["id"],
            message=(
                "已模拟执行（MOCK=1，未连接真实集群）。\n"
                f"动作: {action['id']}\n"
                f"说明: {action['description']}\n"
                "将执行的命令:\n"
                f"{action['command']}\n"
                f"Mock 状态已更新: {summary}"
            ),
            command=action["command"],
            detail={"mode": "mock", "mock_state": entry},
            scenario=action["scenario"],
            namespace=ns,
            pod=pod,
            confirmed=True,
            decision="simulated",
        )

    from src.remediation.live import apply_live

    try:
        live = apply_live(action)
    except Exception as exc:  # noqa: BLE001
        logger.exception("apply_audit phase=error action=%s", action["id"])
        live = ApplyResult(
            ok=False,
            refused=False,
            simulated=False,
            action_id=action["id"],
            message=f"执行失败，已停止后续变更。\n原因: {exc}",
            command=action["command"],
        )
    decision = "applied" if live.ok else "failed"
    _audit(
        action_id=action["id"],
        scenario=action["scenario"],
        namespace=ns,
        pod=pod,
        confirmed=True,
        decision=decision,
    )
    return live
