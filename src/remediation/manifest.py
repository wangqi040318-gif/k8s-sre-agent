"""把结构化 api_op 变成 JSON Patch 或可重建的 Pod 清单。

这些函数不访问集群，方便离线测试。
"""

from __future__ import annotations

import copy
from typing import Any


class ManifestError(ValueError):
    """提案无法落到合法的清单变更上。"""


def container_index(containers: list[dict[str, Any]], name: str) -> int:
    """按容器名找下标；只有一个容器时允许退回 0。"""
    for index, container in enumerate(containers):
        if container.get("name") == name:
            return index
    if len(containers) == 1:
        return 0
    raise ManifestError(f"未找到容器 {name}")


def _probe_keys(container: dict[str, Any]) -> list[str]:
    return [
        key
        for key in ("livenessProbe", "readinessProbe", "startupProbe")
        if container.get(key)
    ]


def _pointer_token(token: str) -> str:
    """JSON Pointer 转义。`~` 与 `/` 之外的字符（包括 `.`）原样保留。"""
    return token.replace("~", "~0").replace("/", "~1")


def _quantity_ops(
    container: dict[str, Any],
    base: str,
    *,
    cpu_request: str,
    memory_request: str,
    cpu_limit: str,
    memory_limit: str,
) -> list[dict[str, Any]]:
    """只改 requests/limits 的 cpu 与 memory，保留同级的其他资源键。"""
    resources = container.get("resources")
    if not resources:
        return [
            {
                "op": "add",
                "path": f"{base}/resources",
                "value": {
                    "requests": {"cpu": cpu_request, "memory": memory_request},
                    "limits": {"cpu": cpu_limit, "memory": memory_limit},
                },
            }
        ]
    ops: list[dict[str, Any]] = []
    requests = resources.get("requests")
    if not requests:
        ops.append(
            {
                "op": "add",
                "path": f"{base}/resources/requests",
                "value": {"cpu": cpu_request, "memory": memory_request},
            }
        )
    else:
        for key, value in (("cpu", cpu_request), ("memory", memory_request)):
            verb = "replace" if key in requests else "add"
            ops.append({"op": verb, "path": f"{base}/resources/requests/{key}", "value": value})
    limits = resources.get("limits")
    if not limits:
        ops.append(
            {
                "op": "add",
                "path": f"{base}/resources/limits",
                "value": {"cpu": cpu_limit, "memory": memory_limit},
            }
        )
    else:
        for key, value in (("cpu", cpu_limit), ("memory", memory_limit)):
            verb = "replace" if key in limits else "add"
            ops.append({"op": verb, "path": f"{base}/resources/limits/{key}", "value": value})
    return ops


def build_json_patch(
    container: dict[str, Any],
    index: int,
    api_op: dict[str, Any],
    pod_spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """为 Deployment 模板中的单个容器生成 JSON Patch。只改目标字段。"""
    base = f"/spec/template/spec/containers/{index}"
    op = api_op.get("op")
    ops: list[dict[str, Any]] = []

    if op == "fix_image":
        image = api_op["image"]
        verb = "replace" if container.get("image") else "add"
        ops.append({"op": verb, "path": f"{base}/image", "value": image})
        return ops

    if op == "bump_memory":
        memory = api_op["memory_limit"]
        resources = container.get("resources")
        if not resources:
            ops.append(
                {
                    "op": "add",
                    "path": f"{base}/resources",
                    "value": {"limits": {"memory": memory}},
                }
            )
            return ops
        limits = resources.get("limits")
        if not limits:
            ops.append(
                {
                    "op": "add",
                    "path": f"{base}/resources/limits",
                    "value": {"memory": memory},
                }
            )
            return ops
        verb = "replace" if "memory" in limits else "add"
        ops.append({"op": verb, "path": f"{base}/resources/limits/memory", "value": memory})
        return ops

    if op == "fix_command":
        command = list(api_op["command"])
        verb = "replace" if container.get("command") else "add"
        ops.append({"op": verb, "path": f"{base}/command", "value": command})
        if container.get("args"):
            ops.append({"op": "remove", "path": f"{base}/args"})
        if api_op.get("remove_probes", True):
            for key in _probe_keys(container):
                ops.append({"op": "remove", "path": f"{base}/{key}"})
        return ops

    if op == "relax_scheduling":
        ops.extend(
            _quantity_ops(
                container,
                base,
                cpu_request=api_op["cpu_request"],
                memory_request=api_op["memory_request"],
                cpu_limit=api_op["cpu_limit"],
                memory_limit=api_op["memory_limit"],
            )
        )
        spec = pod_spec or {}
        if api_op.get("remove_affinity", True) and spec.get("affinity"):
            ops.append({"op": "remove", "path": "/spec/template/spec/affinity"})
        if api_op.get("remove_node_selector", True) and spec.get("nodeSelector"):
            ops.append({"op": "remove", "path": "/spec/template/spec/nodeSelector"})
        return ops

    if op == "fix_probe":
        path = api_op["path"]
        port = api_op["port"]
        for probe_name in api_op.get("probes") or []:
            probe = container.get(probe_name)
            if not isinstance(probe, dict) or not isinstance(probe.get("httpGet"), dict):
                raise ManifestError(f"{probe_name} 不是 httpGet 探针，已停止执行")
            http = probe["httpGet"]
            path_verb = "replace" if "path" in http else "add"
            port_verb = "replace" if "port" in http else "add"
            ops.append(
                {"op": path_verb, "path": f"{base}/{probe_name}/httpGet/path", "value": path}
            )
            ops.append(
                {"op": port_verb, "path": f"{base}/{probe_name}/httpGet/port", "value": port}
            )
        if not ops:
            raise ManifestError("没有可修改的探针")
        return ops

    raise ManifestError(f"不支持的 API 操作: {op}")


def build_configmap_key_patch(
    existing_data: dict[str, Any] | None,
    desired: dict[str, str],
) -> list[dict[str, Any]]:
    """只补缺失的键。已有键一律不替换。`existing_data is None` 表示对象还没有 data 字段。"""
    if existing_data is None:
        return [{"op": "add", "path": "/data", "value": dict(desired)}]
    ops: list[dict[str, Any]] = []
    for key, value in desired.items():
        if key in existing_data:
            continue
        ops.append({"op": "add", "path": "/data/" + _pointer_token(key), "value": value})
    return ops


def build_selector_patch(
    current: dict[str, Any] | None,
    desired: dict[str, str],
) -> list[dict[str, Any]]:
    """整段替换 selector。合并补丁会把旧的 app=frontend 留在 map 里。"""
    verb = "replace" if current else "add"
    return [{"op": verb, "path": "/spec/selector", "value": dict(desired)}]


def _mapping(obj: Any, *keys: str) -> Any:
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    return None


def pod_references_configmap(pod: dict[str, Any], name: str) -> bool:
    """Pod 的卷或环境变量是否引用这个 ConfigMap。同时认 API 的驼峰和客户端的下划线字段。"""
    spec = _mapping(pod, "spec") or {}
    for volume in spec.get("volumes") or []:
        configmap = _mapping(volume, "configMap", "config_map") or {}
        if configmap.get("name") == name:
            return True
    containers = list(spec.get("containers") or [])
    containers.extend(spec.get("initContainers") or spec.get("init_containers") or [])
    for container in containers:
        for env in container.get("env") or []:
            value_from = _mapping(env, "valueFrom", "value_from") or {}
            ref = _mapping(value_from, "configMapKeyRef", "config_map_key_ref") or {}
            if ref.get("name") == name:
                return True
        for env_from in container.get("envFrom") or container.get("env_from") or []:
            ref = _mapping(env_from, "configMapRef", "config_map_ref") or {}
            if ref.get("name") == name:
                return True
    return False


def pod_labels_match(pod: dict[str, Any], selector: dict[str, str]) -> bool:
    """Pod 标签是否包含 selector 的每一项。多出来的标签不影响。"""
    labels = ((pod.get("metadata") or {}).get("labels") or {})
    return all(labels.get(key) == value for key, value in selector.items())


def _strip_service_account_token(spec: dict[str, Any]) -> None:
    """去掉 API 自动注入的 token 卷，避免重建时携带只读字段。"""
    volumes = spec.get("volumes") or []
    dropped: set[str] = set()
    kept = []
    for volume in volumes:
        name = volume.get("name") or ""
        if name.startswith("kube-api-access-"):
            dropped.add(name)
            continue
        kept.append(volume)
    if kept:
        spec["volumes"] = kept
    else:
        spec.pop("volumes", None)
    for container in spec.get("containers") or []:
        mounts = [
            mount
            for mount in (container.get("volumeMounts") or [])
            if mount.get("name") not in dropped
        ]
        if mounts:
            container["volumeMounts"] = mounts
        else:
            container.pop("volumeMounts", None)


def mutate_container_dict(container: dict[str, Any], api_op: dict[str, Any]) -> None:
    """就地修改容器字典，供独立 Pod 重建使用。"""
    op = api_op.get("op")
    if op == "fix_image":
        container["image"] = api_op["image"]
        return
    if op == "bump_memory":
        resources = container.setdefault("resources", {})
        limits = resources.setdefault("limits", {})
        limits["memory"] = api_op["memory_limit"]
        return
    if op == "fix_command":
        container["command"] = list(api_op["command"])
        container.pop("args", None)
        if api_op.get("remove_probes", True):
            for key in ("livenessProbe", "readinessProbe", "startupProbe"):
                container.pop(key, None)
        return
    if op == "relax_scheduling":
        resources = container.setdefault("resources", {})
        requests = resources.setdefault("requests", {})
        requests["cpu"] = api_op["cpu_request"]
        requests["memory"] = api_op["memory_request"]
        limits = resources.setdefault("limits", {})
        limits["cpu"] = api_op["cpu_limit"]
        limits["memory"] = api_op["memory_limit"]
        return
    if op == "fix_probe":
        for probe_name in api_op.get("probes") or []:
            probe = container.get(probe_name)
            if not isinstance(probe, dict) or not isinstance(probe.get("httpGet"), dict):
                raise ManifestError(f"{probe_name} 不是 httpGet 探针，已停止执行")
            probe["httpGet"]["path"] = api_op["path"]
            probe["httpGet"]["port"] = api_op["port"]
        return
    raise ManifestError(f"不支持的 API 操作: {op}")


def mutate_pod_spec(spec: dict[str, Any], api_op: dict[str, Any]) -> None:
    """就地修改 Pod spec：容器字段，以及调度约束。不改 tolerations。"""
    containers = spec.get("containers") or []
    if not containers:
        raise ManifestError("Pod 没有容器，无法生成修复清单")
    index = container_index(containers, api_op["container"])
    mutate_container_dict(containers[index], api_op)
    if api_op.get("op") != "relax_scheduling":
        return
    if api_op.get("remove_affinity", True):
        spec.pop("affinity", None)
    if api_op.get("remove_node_selector", True):
        spec.pop("nodeSelector", None)


def _safe_annotations(annotations: dict[str, Any] | None) -> dict[str, Any]:
    if not annotations:
        return {}
    blocked = ("kubectl.kubernetes.io/", "kubernetes.io/", "k8s.io/")
    return {
        key: value
        for key, value in annotations.items()
        if not str(key).startswith(blocked)
    }


def sanitized_pod_manifest(pod: dict[str, Any], api_op: dict[str, Any]) -> dict[str, Any]:
    """由现有 Pod 生成可 create/apply 的清单，并套用修复。"""
    metadata = pod.get("metadata") or {}
    spec = copy.deepcopy(pod.get("spec") or {})
    mutate_pod_spec(spec, api_op)
    spec.pop("nodeName", None)
    _strip_service_account_token(spec)
    annotations = _safe_annotations(metadata.get("annotations"))
    manifest_meta: dict[str, Any] = {
        "name": api_op["pod"],
        "namespace": api_op["namespace"],
        "labels": dict(metadata.get("labels") or {}),
    }
    if annotations:
        manifest_meta["annotations"] = annotations
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": manifest_meta,
        "spec": spec,
    }
