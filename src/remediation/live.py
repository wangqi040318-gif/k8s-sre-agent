"""MOCK=0 时的真实集群执行。优先官方客户端，kubectl 只作为确认后的退路。

不会读取 action["command"] 去开 shell。参数全部来自结构化 api_op，并且名称已在门闩侧校验。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from typing import Any

from src.remediation.apply import ApplyResult
from src.remediation.manifest import (
    ManifestError,
    build_json_patch,
    container_index,
    sanitized_pod_manifest,
)

logger = logging.getLogger("src.remediation.live")

_ALLOWED_OPS = {"fix_image", "bump_memory", "fix_command"}
_NAME_RE_SOURCE = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"


def _fail(action: dict[str, Any], reason: str) -> ApplyResult:
    return ApplyResult(
        ok=False,
        refused=False,
        simulated=False,
        action_id=action["id"],
        message=f"执行失败，集群变更未完成。\n原因: {reason}",
        command=action["command"],
        detail={"mode": "live"},
    )


def _ok(action: dict[str, Any], message: str, *, via: str) -> ApplyResult:
    return ApplyResult(
        ok=True,
        refused=False,
        simulated=False,
        action_id=action["id"],
        message=message,
        command=action["command"],
        detail={"mode": "live", "via": via},
    )


def _check_op(action: dict[str, Any]) -> str | None:
    import re

    api_op = action.get("api_op") or {}
    if api_op.get("op") not in _ALLOWED_OPS:
        return "API 操作不在允许列表内"
    name_re = re.compile(_NAME_RE_SOURCE)
    for key in ("namespace", "pod", "container"):
        value = api_op.get(key) or ""
        if not name_re.fullmatch(value) or len(value) > 63:
            return f"{key} 不合法，已停止执行"
    return None


def _run_kubectl(argv: list[str], input_text: str | None = None) -> tuple[int, str, str]:
    """以参数数组调用 kubectl。shell 固定为 False。"""
    if not argv or argv[0] != "kubectl":
        raise RuntimeError("仅允许调用 kubectl")
    for arg in argv:
        if not isinstance(arg, str) or "\n" in arg or "\x00" in arg:
            raise RuntimeError("kubectl 参数非法")
    proc = subprocess.run(  # noqa: S603
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
        shell=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _clip(text: str) -> str:
    text = (text or "").strip()
    if len(text) > 400:
        return text[:400] + "..."
    return text


def _owner_deployment_from_dict(pod: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    """返回 (deployment 名, ReplicaSet 名, 是否受控制器管理)。"""
    refs = (pod.get("metadata") or {}).get("ownerReferences") or []
    controlled = False
    replicaset = None
    for ref in refs:
        if ref.get("controller"):
            controlled = True
        kind = ref.get("kind")
        if kind == "Deployment" and ref.get("name"):
            return ref["name"], None, True
        if kind == "ReplicaSet" and ref.get("name"):
            controlled = True
            replicaset = ref["name"]
    return None, replicaset, controlled or bool(refs)


def _deployment_from_replicaset_dict(replica: dict[str, Any]) -> str | None:
    for ref in (replica.get("metadata") or {}).get("ownerReferences") or []:
        if ref.get("kind") == "Deployment" and ref.get("name"):
            return ref["name"]
    return None


def _patch_deployment(apps: Any, name: str, namespace: str, ops: list[dict[str, Any]]) -> None:
    """显式使用 JSON Patch，避免战略合并补丁误改相邻字段。"""
    apps.api_client.call_api(
        f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
        "PATCH",
        header_params={
            "Accept": "application/json",
            "Content-Type": "application/json-patch+json",
        },
        body=ops,
        response_type="object",
        auth_settings=["BearerToken"],
        _return_http_data_only=True,
    )


def _wait_pod_gone(core: Any, name: str, namespace: str, timeout: float = 40.0) -> None:
    from kubernetes.client.exceptions import ApiException

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            core.read_namespaced_pod(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status == 404:
                return
            raise
        time.sleep(0.4)
    raise TimeoutError(f"等待 Pod {namespace}/{name} 删除超时")


def _apply_with_client(action: dict[str, Any]) -> ApplyResult:
    from kubernetes.client.exceptions import ApiException

    from src.k8s.client import get_apps_v1, get_core_v1

    core = get_core_v1()
    apps = get_apps_v1()
    if core is None or apps is None:
        return _apply_with_kubectl(action)

    api_op = action["api_op"]
    namespace = api_op["namespace"]
    pod_name = api_op["pod"]
    try:
        pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
        pod_dict = core.api_client.sanitize_for_serialization(pod)
        deploy, replicaset, controlled = _owner_deployment_from_dict(pod_dict)
        if replicaset and not deploy:
            replica = apps.read_namespaced_replica_set(name=replicaset, namespace=namespace)
            replica_dict = apps.api_client.sanitize_for_serialization(replica)
            deploy = _deployment_from_replicaset_dict(replica_dict)
        if deploy:
            deployment = apps.read_namespaced_deployment(name=deploy, namespace=namespace)
            deployment_dict = apps.api_client.sanitize_for_serialization(deployment)
            containers = deployment_dict["spec"]["template"]["spec"]["containers"]
            index = container_index(containers, api_op["container"])
            ops = build_json_patch(containers[index], index, api_op)
            _patch_deployment(apps, deploy, namespace, ops)
            try:
                core.delete_namespaced_pod(
                    name=pod_name,
                    namespace=namespace,
                    grace_period_seconds=1,
                )
            except ApiException as exc:
                logger.warning("Deployment 已更新，但删除 Pod 失败: %s", exc.status)
                return _ok(
                    action,
                    (
                        f"已通过 Kubernetes 客户端更新 Deployment {deploy}。"
                        f"删除异常 Pod {pod_name} 未成功（HTTP {exc.status}），"
                        "控制器仍会按新模板滚动。"
                    ),
                    via="kubernetes-client",
                )
            return _ok(
                action,
                (
                    f"已通过 Kubernetes 客户端更新 Deployment {deploy}，"
                    f"并删除 Pod {pod_name} 以触发重建。\n"
                    f"动作: {action['id']}"
                ),
                via="kubernetes-client",
            )

        if controlled:
            return _fail(
                action,
                "Pod 由控制器管理，但没有定位到 Deployment。为避免误删，已停止执行。",
            )

        manifest = sanitized_pod_manifest(pod_dict, api_op)
        core.delete_namespaced_pod(
            name=pod_name,
            namespace=namespace,
            grace_period_seconds=1,
        )
        _wait_pod_gone(core, pod_name, namespace)
        core.create_namespaced_pod(namespace=namespace, body=manifest)
        return _ok(
            action,
            (
                f"已通过 Kubernetes 客户端删除并重建独立 Pod {namespace}/{pod_name}。\n"
                f"动作: {action['id']}"
            ),
            via="kubernetes-client",
        )
    except (ManifestError, TimeoutError) as exc:
        return _fail(action, str(exc))
    except ApiException as exc:
        return _fail(action, f"Kubernetes API {exc.status}: {_clip(exc.body or str(exc))}")


def _kubectl_json(argv: list[str]) -> dict[str, Any]:
    code, out, err = _run_kubectl(argv)
    if code != 0:
        raise RuntimeError(_clip(err or out) or f"kubectl 退出码 {code}")
    return json.loads(out)


def _apply_with_kubectl(action: dict[str, Any]) -> ApplyResult:
    if shutil.which("kubectl") is None:
        return _fail(
            action,
            "无法连接 Kubernetes API，且未找到 kubectl。未执行任何变更。请检查 KUBECONFIG，或改用 MOCK=1。",
        )
    api_op = action["api_op"]
    namespace = api_op["namespace"]
    pod_name = api_op["pod"]
    try:
        pod = _kubectl_json(["kubectl", "get", "pod", pod_name, "-n", namespace, "-o", "json"])
        deploy, replicaset, controlled = _owner_deployment_from_dict(pod)
        if replicaset and not deploy:
            replica = _kubectl_json(
                ["kubectl", "get", "replicaset", replicaset, "-n", namespace, "-o", "json"]
            )
            deploy = _deployment_from_replicaset_dict(replica)
        if deploy:
            deployment = _kubectl_json(
                ["kubectl", "get", "deployment", deploy, "-n", namespace, "-o", "json"]
            )
            containers = deployment["spec"]["template"]["spec"]["containers"]
            index = container_index(containers, api_op["container"])
            ops = build_json_patch(containers[index], index, api_op)
            code, _out, err = _run_kubectl(
                [
                    "kubectl",
                    "patch",
                    "deployment",
                    deploy,
                    "-n",
                    namespace,
                    "--type=json",
                    "-p",
                    json.dumps(ops, ensure_ascii=False),
                ]
            )
            if code != 0:
                return _fail(action, f"kubectl patch 失败: {_clip(err)}")
            dcode, _dout, derr = _run_kubectl(
                [
                    "kubectl",
                    "delete",
                    "pod",
                    pod_name,
                    "-n",
                    namespace,
                    "--wait=false",
                    "--grace-period=1",
                ]
            )
            extra = ""
            if dcode != 0:
                extra = f"\n删除 Pod 未成功: {_clip(derr)}。控制器仍会按新模板滚动。"
            return _ok(
                action,
                f"已通过 kubectl 更新 Deployment {deploy}，并请求删除 Pod {pod_name}。{extra}",
                via="kubectl",
            )
        if controlled:
            return _fail(
                action,
                "Pod 由控制器管理，但没有定位到 Deployment。为避免误删，已停止执行。",
            )
        manifest = sanitized_pod_manifest(pod, api_op)
        code, _out, err = _run_kubectl(
            [
                "kubectl",
                "delete",
                "pod",
                pod_name,
                "-n",
                namespace,
                "--wait=true",
                "--grace-period=1",
            ]
        )
        if code != 0:
            return _fail(action, f"kubectl delete 失败: {_clip(err)}")
        code, _out, err = _run_kubectl(
            ["kubectl", "apply", "-f", "-", "-n", namespace],
            input_text=json.dumps(manifest, ensure_ascii=False),
        )
        if code != 0:
            return _fail(
                action,
                "Pod 已删除，但 kubectl apply 重建失败: " + _clip(err),
            )
        return _ok(
            action,
            f"已通过 kubectl 删除并重建独立 Pod {namespace}/{pod_name}。",
            via="kubectl",
        )
    except (ManifestError, RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return _fail(action, str(exc))


def apply_live(action: dict[str, Any]) -> ApplyResult:
    """对已确认的动作做真实变更。调用方必须先通过确认门闩。"""
    problem = _check_op(action)
    if problem:
        return _fail(action, problem)
    from src.k8s.client import get_apps_v1, get_core_v1

    if get_core_v1() is not None and get_apps_v1() is not None:
        return _apply_with_client(action)
    return _apply_with_kubectl(action)
