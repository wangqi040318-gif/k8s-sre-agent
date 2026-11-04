"""三类演示故障的修复提案。命令只用于展示，执行走结构化 api_op。"""

from __future__ import annotations

import re
from typing import Any

from src import config
from src.tools import mock_data

# 稳定动作 id。CLI / UI / 测试都引用这些值。
ACTION_SCENARIO: dict[str, str] = {
    "fix-image": "imagepull",
    "bump-memory": "oom",
    "fix-command": "crashloop",
}

FIX_IMAGE = "nginx:1.27"
MEMORY_LIMIT = "256Mi"
HEALTHY_COMMAND = ["sh", "-c", "echo healthy; sleep 3600"]
CONTAINER_NAME = "app"

RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}

_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_ACTION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def known_action_ids() -> list[str]:
    """按固定顺序返回允许的动作 id。"""
    return list(ACTION_SCENARIO)


def _valid_k8s_name(value: str) -> bool:
    return bool(value) and len(value) <= 63 and bool(_NAME_RE.fullmatch(value))


def _command_fix_image(namespace: str, pod: str, container: str, image: str) -> str:
    return "\n".join(
        [
            f"# 由程序按 API 执行，不会把本段文本丢进 shell",
            f"# Deployment：更新模板镜像并删除异常 Pod，让控制器重建",
            f"kubectl set image deployment/{pod} {container}={image} -n {namespace}",
            f"kubectl delete pod {pod} -n {namespace} --wait=false",
            f"# 独立 Pod（demo/manifests 即是）：删除后按镜像 {image} 重建同名 Pod",
            f"kubectl delete pod {pod} -n {namespace} --wait=true",
        ]
    )


def _command_bump_memory(namespace: str, pod: str, container: str, memory: str) -> str:
    return "\n".join(
        [
            f"# 由程序按 API 执行，不会把本段文本丢进 shell",
            f"kubectl set resources deployment/{pod} -n {namespace} -c {container} --limits=memory={memory}",
            f"# 独立 Pod：删除后重建，并将 resources.limits.memory 设为 {memory}",
            f"kubectl delete pod {pod} -n {namespace} --wait=true",
        ]
    )


def _command_fix_crashloop(namespace: str, pod: str, container: str) -> str:
    return "\n".join(
        [
            f"# 由程序按 API 执行，不会把本段文本丢进 shell",
            f"# 将容器 {container} 的 command 改为健康休眠，并移除 liveness/readiness/startup 探针",
            "kubectl patch deployment/"
            + pod
            + " -n "
            + namespace
            + " --type=json -p "
            + "'[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/command\","
            + "\"value\":[\"sh\",\"-c\",\"echo healthy; sleep 3600\"]}]'",
            f"# 独立 Pod：删除后以修正后的 command 重建",
            f"kubectl delete pod {pod} -n {namespace} --wait=true",
        ]
    )


def _action(
    *,
    action_id: str,
    scenario: str,
    description: str,
    command: str,
    api_op: dict[str, Any],
    risk_level: str,
    reversible: bool,
) -> dict[str, Any]:
    return {
        "id": action_id,
        "scenario": scenario,
        "description": description,
        "command": command,
        "api_op": api_op,
        "risk_level": risk_level,
        "reversible": reversible,
    }


def build_remediation(
    fault: str | None,
    namespace: str | None = None,
    pod_name: str | None = None,
) -> list[dict[str, Any]]:
    """根据故障类型生成提案列表。未知类型返回空列表。"""
    scenario = config.normalize_scenario(fault) or (fault or "").strip().lower()
    if scenario not in {"imagepull", "oom", "crashloop"}:
        return []
    ns = (namespace or config.K8S_NAMESPACE or "default").strip()
    if not _valid_k8s_name(ns):
        ns = "default"
    pod = (pod_name or "").strip()
    if not _valid_k8s_name(pod):
        pod = mock_data.SCENARIO_PODS[scenario]
    if scenario == "imagepull":
        return [
            _action(
                action_id="fix-image",
                scenario=scenario,
                description=(
                    f"将容器 {CONTAINER_NAME} 的错误镜像更正为 {FIX_IMAGE}。"
                    "若 Pod 属于 Deployment，则更新模板并删除异常 Pod 以触发重建；"
                    "若为独立 Pod，则按新镜像重建同名 Pod。"
                ),
                command=_command_fix_image(ns, pod, CONTAINER_NAME, FIX_IMAGE),
                api_op={
                    "op": "fix_image",
                    "namespace": ns,
                    "pod": pod,
                    "container": CONTAINER_NAME,
                    "image": FIX_IMAGE,
                },
                risk_level="medium",
                reversible=True,
            )
        ]
    if scenario == "oom":
        return [
            _action(
                action_id="bump-memory",
                scenario=scenario,
                description=(
                    f"将容器 {CONTAINER_NAME} 的 memory limit 上调到 {MEMORY_LIMIT}，"
                    "缓解 OOMKilled。上调 limit 不能代替内存泄漏排查。"
                ),
                command=_command_bump_memory(ns, pod, CONTAINER_NAME, MEMORY_LIMIT),
                api_op={
                    "op": "bump_memory",
                    "namespace": ns,
                    "pod": pod,
                    "container": CONTAINER_NAME,
                    "memory_limit": MEMORY_LIMIT,
                },
                risk_level="medium",
                reversible=True,
            )
        ]
    return [
        _action(
            action_id="fix-command",
            scenario=scenario,
            description=(
                f"将容器 {CONTAINER_NAME} 的启动命令改为 "
                "`echo healthy; sleep 3600`，并移除 liveness/readiness/startup 探针"
                "（若存在），以打断 CrashLoopBackOff。"
            ),
            command=_command_fix_crashloop(ns, pod, CONTAINER_NAME),
            api_op={
                "op": "fix_command",
                "namespace": ns,
                "pod": pod,
                "container": CONTAINER_NAME,
                "command": list(HEALTHY_COMMAND),
                "remove_probes": True,
            },
            risk_level="medium",
            reversible=True,
        )
    ]


def resolve_action(
    action_id: str | None,
    *,
    scenario: str | None = None,
    namespace: str | None = None,
    pod_name: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """按 id 取出提案。失败时返回 (None, 中文原因)，不产生副作用。"""
    raw_id = (action_id or "").strip()
    if not _ACTION_RE.fullmatch(raw_id) or raw_id not in ACTION_SCENARIO:
        joined = "、".join(known_action_ids())
        shown = raw_id if _ACTION_RE.fullmatch(raw_id) else "(非法或为空)"
        return None, f"未知动作 id「{shown}」。可选：{joined}。"

    owner = ACTION_SCENARIO[raw_id]
    if scenario:
        norm = config.normalize_scenario(scenario)
        if norm is None:
            return None, "未知场景。可选：imagepull、oom、crashloop。"
        if norm != owner:
            return None, (
                f"动作 {raw_id} 属于场景 {owner}，与指定场景 {norm} 不一致。"
            )

    ns = (namespace or config.K8S_NAMESPACE or "default").strip()
    if not _valid_k8s_name(ns):
        return None, "命名空间名称不合法，已拒绝执行。"
    pod = (pod_name or mock_data.SCENARIO_PODS[owner]).strip()
    if not _valid_k8s_name(pod):
        return None, "Pod 名称不合法，已拒绝执行。"

    for action in build_remediation(owner, ns, pod):
        if action["id"] == raw_id:
            return action, None
    return None, f"未找到动作「{raw_id}」。"


def render_remediation_markdown(actions: list[dict[str, Any]]) -> str:
    """渲染报告中的「修复方案」章节。"""
    lines = [
        "## 修复方案（需人工确认）",
        "",
        "以下为**提案**。诊断不会执行任何写操作；只有显式确认后才会 apply。",
        "",
    ]
    if not actions:
        lines.append("当前证据没有可执行的修复动作。")
        lines.append("")
        return "\n".join(lines)

    for index, action in enumerate(actions, 1):
        risk = RISK_LABELS.get(action["risk_level"], action["risk_level"])
        reversible = "是" if action["reversible"] else "否"
        api_op = action.get("api_op") or {}
        lines.extend(
            [
                f"### {index}. `{action['id']}`",
                "",
                f"- 说明：{action['description']}",
                f"- 风险等级：{risk}（`{action['risk_level']}`）",
                f"- 可回滚：{reversible}",
                f"- API 操作：`{api_op.get('op', '')}`",
                "- 命令（仅供阅读，程序不会整段送进 shell）：",
                "",
                "```bash",
                action["command"],
                "```",
                "",
                "确认后执行：",
                "",
                "```bash",
                "python -m src.cli --apply --confirm YES "
                f"--action {action['id']} --scenario {action['scenario']}",
                "# 或交互式，看到「输入 CONFIRM 后执行」后原样输入 CONFIRM",
                "python -m src.cli --apply "
                f"--action {action['id']} --scenario {action['scenario']}",
                "```",
                "",
            ]
        )
    return "\n".join(lines)
