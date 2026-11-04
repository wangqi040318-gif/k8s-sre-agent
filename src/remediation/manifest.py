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


def build_json_patch(container: dict[str, Any], index: int, api_op: dict[str, Any]) -> list[dict[str, Any]]:
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

    raise ManifestError(f"不支持的 API 操作: {op}")


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
    raise ManifestError(f"不支持的 API 操作: {op}")


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
    containers = spec.get("containers") or []
    if not containers:
        raise ManifestError("Pod 没有容器，无法生成修复清单")
    index = container_index(containers, api_op["container"])
    mutate_container_dict(containers[index], api_op)
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
