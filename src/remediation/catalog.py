"""演示故障的修复提案。命令只用于展示，执行走结构化 api_op。"""

from __future__ import annotations

import json
import re
from typing import Any

from src import config
from src.tools import mock_data

# 稳定动作 id。CLI / UI / 测试都引用这些值。一个场景一个动作。
ACTION_SCENARIO: dict[str, str] = {
    "fix-image": "imagepull",
    "bump-memory": "oom",
    "fix-command": "crashloop",
    "relax-scheduling": "pending",
    "create-config": "configmount",
    "fix-probe": "probe",
    "fix-selector": "connectivity",
}

FIX_IMAGE = "nginx:1.27"
MEMORY_LIMIT = "256Mi"
HEALTHY_COMMAND = ["sh", "-c", "echo healthy; sleep 3600"]
CONTAINER_NAME = "app"

# 演示 Pod 的 requests 是 8 CPU / 16Gi，且亲和性指向不存在的节点。
# 下调到笔记本 / kind 节点放得下的值，不把 limit 再抬高。
SCHEDULE_CPU_REQUEST = "100m"
SCHEDULE_MEMORY_REQUEST = "64Mi"
SCHEDULE_CPU_LIMIT = "200m"
SCHEDULE_MEMORY_LIMIT = "128Mi"

CONFIGMAP_NAME = "app-config"
CONFIGMAP_DATA = {
    "LOG_LEVEL": "info",
    "config.yaml": "mode: demo\n",
}

PROBE_PATH = "/"
PROBE_PORT = 80
PROBE_NAMES = ["readinessProbe", "livenessProbe"]

SERVICE_NAME = "demo-web"
SERVICE_SELECTOR = {"app": "demo"}

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


def _command_relax_scheduling(namespace: str, pod: str, container: str) -> str:
    return "\n".join(
        [
            "# 由程序按 API 执行，不会把本段文本丢进 shell",
            f"# 将容器 {container} 的 requests/limits 降到可调度值，并移除 nodeSelector 与 affinity",
            f"# 不改 tolerations，也不对节点做 cordon / drain",
            (
                f"kubectl set resources deployment/{pod} -n {namespace} -c {container} "
                f"--requests=cpu={SCHEDULE_CPU_REQUEST},memory={SCHEDULE_MEMORY_REQUEST} "
                f"--limits=cpu={SCHEDULE_CPU_LIMIT},memory={SCHEDULE_MEMORY_LIMIT}"
            ),
            f"# 独立 Pod：删除后按上述资源重建，不再带错误的 nodeSelector / affinity",
            f"kubectl delete pod {pod} -n {namespace} --wait=true",
        ]
    )


def _command_create_config(namespace: str) -> str:
    return "\n".join(
        [
            "# 由程序按 API 执行，不会把本段文本丢进 shell",
            f"# 创建缺失的 ConfigMap {CONFIGMAP_NAME}。已存在时只补缺失的键，不覆盖已有值",
            "# 不创建 Secret，不把密钥写进 data，也不删除 Pod（kubelet 会重试挂载）",
            (
                f"kubectl create configmap {CONFIGMAP_NAME} -n {namespace} "
                "--from-literal=LOG_LEVEL=info"
            ),
        ]
    )


def _command_fix_probe(namespace: str, pod: str, container: str) -> str:
    return "\n".join(
        [
            "# 由程序按 API 执行，不会把本段文本丢进 shell",
            (
                f"# 将容器 {container} 的 readiness/liveness httpGet 改为 "
                f"path={PROBE_PATH} port={PROBE_PORT}"
            ),
            "# 保留 periodSeconds / failureThreshold，不删除探针",
            f"kubectl patch deployment/{pod} -n {namespace} --type=json -p '<只改探针 path 与 port>'",
            f"# 独立 Pod：删除后按修正后的探针重建",
            f"kubectl delete pod {pod} -n {namespace} --wait=true",
        ]
    )


def _command_fix_selector(namespace: str) -> str:
    selector = json_selector()
    return "\n".join(
        [
            "# 由程序按 API 执行，不会把本段文本丢进 shell",
            f"# 只替换 Service {SERVICE_NAME} 的 selector，不改端口、类型或 NetworkPolicy",
            (
                f"kubectl patch service {SERVICE_NAME} -n {namespace} --type=json -p "
                f"'[{{\"op\":\"replace\",\"path\":\"/spec/selector\",\"value\":{selector}}}]'"
            ),
        ]
    )


def json_selector() -> str:
    """给展示用命令一个稳定的 selector JSON。"""
    return json.dumps(SERVICE_SELECTOR, ensure_ascii=False, separators=(",", ":"))


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
    if scenario not in set(config.SCENARIO_CHOICES):
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
    if scenario == "crashloop":
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
    if scenario == "pending":
        return [
            _action(
                action_id="relax-scheduling",
                scenario=scenario,
                description=(
                    f"将容器 {CONTAINER_NAME} 的 requests 降为 "
                    f"cpu={SCHEDULE_CPU_REQUEST}、memory={SCHEDULE_MEMORY_REQUEST}，"
                    f"limits 降为 cpu={SCHEDULE_CPU_LIMIT}、memory={SCHEDULE_MEMORY_LIMIT}，"
                    "并移除对不上节点的 nodeSelector 与 nodeAffinity。"
                    "不修改 tolerations，也不对节点做 cordon 或 drain。"
                ),
                command=_command_relax_scheduling(ns, pod, CONTAINER_NAME),
                api_op={
                    "op": "relax_scheduling",
                    "namespace": ns,
                    "pod": pod,
                    "container": CONTAINER_NAME,
                    "cpu_request": SCHEDULE_CPU_REQUEST,
                    "memory_request": SCHEDULE_MEMORY_REQUEST,
                    "cpu_limit": SCHEDULE_CPU_LIMIT,
                    "memory_limit": SCHEDULE_MEMORY_LIMIT,
                    "remove_affinity": True,
                    "remove_node_selector": True,
                },
                risk_level="medium",
                reversible=True,
            )
        ]
    if scenario == "configmount":
        keys = "、".join(CONFIGMAP_DATA)
        return [
            _action(
                action_id="create-config",
                scenario=scenario,
                description=(
                    f"创建缺失的 ConfigMap {CONFIGMAP_NAME}（键：{keys}）。"
                    "对象已存在时只补缺失的键，不覆盖已有值。"
                    "不创建 Secret，不写入密钥，也不删除 Pod；kubelet 会重试挂载。"
                ),
                command=_command_create_config(ns),
                api_op={
                    "op": "create_configmap",
                    "namespace": ns,
                    "pod": pod,
                    "name": CONFIGMAP_NAME,
                    "data": dict(CONFIGMAP_DATA),
                },
                risk_level="low",
                reversible=True,
            )
        ]
    if scenario == "probe":
        return [
            _action(
                action_id="fix-probe",
                scenario=scenario,
                description=(
                    f"将容器 {CONTAINER_NAME} 的 readiness 与 liveness 探针改为 "
                    f"httpGet path={PROBE_PATH} port={PROBE_PORT}，与容器实际监听的 80 端口对齐。"
                    "保留 periodSeconds 和 failureThreshold，不删除探针。"
                ),
                command=_command_fix_probe(ns, pod, CONTAINER_NAME),
                api_op={
                    "op": "fix_probe",
                    "namespace": ns,
                    "pod": pod,
                    "container": CONTAINER_NAME,
                    "path": PROBE_PATH,
                    "port": PROBE_PORT,
                    "probes": list(PROBE_NAMES),
                },
                risk_level="medium",
                reversible=True,
            )
        ]
    if scenario == "connectivity":
        return [
            _action(
                action_id="fix-selector",
                scenario=scenario,
                description=(
                    f"将 Service {SERVICE_NAME} 的 selector 改为 app=demo，"
                    "使 Endpoints 对上已 Ready 的 Pod。"
                    "只改 selector，不改端口和 Service 类型，也不放宽 NetworkPolicy。"
                    "Endpoints 已经非空但仍不通时，应改查策略和 DNS，而不是重复执行这个动作。"
                ),
                command=_command_fix_selector(ns),
                api_op={
                    "op": "fix_selector",
                    "namespace": ns,
                    "pod": pod,
                    "service": SERVICE_NAME,
                    "selector": dict(SERVICE_SELECTOR),
                },
                risk_level="medium",
                reversible=True,
            )
        ]
    return []


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
            joined = "、".join(config.SCENARIO_CHOICES)
            return None, f"未知场景。可选：{joined}。"
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
