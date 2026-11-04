"""MOCK=0 时的真实集群执行。优先官方客户端，kubectl 只作为确认后的退路。

不会读取 action["command"] 去开 shell。参数全部来自结构化 api_op，并且名称已在门闩侧校验。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from typing import Any

from src.remediation.apply import ApplyResult
from src.remediation.manifest import (
    ManifestError,
    build_configmap_key_patch,
    build_json_patch,
    build_selector_patch,
    container_index,
    pod_labels_match,
    pod_references_configmap,
    sanitized_pod_manifest,
)

logger = logging.getLogger("src.remediation.live")

_ALLOWED_OPS = {
    "fix_image",
    "bump_memory",
    "fix_command",
    "relax_scheduling",
    "fix_probe",
    "create_configmap",
    "fix_selector",
}
_POD_CONTAINER_OPS = {
    "fix_image",
    "bump_memory",
    "fix_command",
    "relax_scheduling",
    "fix_probe",
}
_NAME_RE_SOURCE = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
_CPU_RE = re.compile(r"^[1-9][0-9]{0,3}m$")
_MEM_RE = re.compile(r"^[1-9][0-9]{0,4}Mi$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,80}$")
_CM_KEY_RE = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9_.]{0,62}$")
_LABEL_RE = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$")
_NO_CLUSTER = "无法连接 Kubernetes API，且未找到 kubectl。未执行任何变更。请检查 KUBECONFIG，或改用 MOCK=1。"


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


def _valid_name(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 63 and bool(re.fullmatch(_NAME_RE_SOURCE, value))


def _check_op(action: dict[str, Any]) -> str | None:
    api_op = action.get("api_op") or {}
    op = api_op.get("op")
    if op not in _ALLOWED_OPS:
        return "API 操作不在允许列表内"
    if not _valid_name(api_op.get("namespace") or ""):
        return "namespace 不合法，已停止执行"
    if op in _POD_CONTAINER_OPS or op in {"create_configmap", "fix_selector"}:
        if not _valid_name(api_op.get("pod") or ""):
            return "pod 不合法，已停止执行"
    if op in _POD_CONTAINER_OPS and not _valid_name(api_op.get("container") or ""):
        return "container 不合法，已停止执行"
    if op == "relax_scheduling":
        for key in ("cpu_request", "cpu_limit"):
            if not _CPU_RE.fullmatch(str(api_op.get(key) or "")):
                return f"{key} 不合法，已停止执行"
            if int(str(api_op[key])[:-1]) > 500:
                return f"{key} 超出演示允许范围，已停止执行"
        for key in ("memory_request", "memory_limit"):
            if not _MEM_RE.fullmatch(str(api_op.get(key) or "")):
                return f"{key} 不合法，已停止执行"
            if int(str(api_op[key])[:-2]) > 512:
                return f"{key} 超出演示允许范围，已停止执行"
    if op == "fix_probe":
        path = api_op.get("path") or ""
        if not isinstance(path, str) or not _PATH_RE.fullmatch(path) or ".." in path:
            return "探针 path 不合法，已停止执行"
        port = api_op.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            return "探针 port 不合法，已停止执行"
        if api_op.get("probes") != ["readinessProbe", "livenessProbe"]:
            return "探针列表不在允许范围内，已停止执行"
    if op == "create_configmap":
        if not _valid_name(api_op.get("name") or ""):
            return "configmap 名称不合法，已停止执行"
        data = api_op.get("data")
        if not isinstance(data, dict) or not data or len(data) > 8:
            return "ConfigMap data 不合法，已停止执行"
        for key, value in data.items():
            if not isinstance(key, str) or not _CM_KEY_RE.fullmatch(key) or ".." in key:
                return "ConfigMap 键不合法，已停止执行"
            if not isinstance(value, str) or "\x00" in value or len(value) > 4096:
                return "ConfigMap 值不合法，已停止执行"
    if op == "fix_selector":
        if not _valid_name(api_op.get("service") or ""):
            return "service 名称不合法，已停止执行"
        selector = api_op.get("selector")
        if not isinstance(selector, dict) or not selector or len(selector) > 4:
            return "selector 不合法，已停止执行"
        for key, value in selector.items():
            if not isinstance(key, str) or not _LABEL_RE.fullmatch(key) or len(key) > 63:
                return "selector 键不合法，已停止执行"
            if not isinstance(value, str) or not _LABEL_RE.fullmatch(value) or len(value) > 63:
                return "selector 值不合法，已停止执行"
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
            template_spec = deployment_dict["spec"]["template"]["spec"]
            containers = template_spec["containers"]
            index = container_index(containers, api_op["container"])
            ops = build_json_patch(containers[index], index, api_op, pod_spec=template_spec)
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
        return _fail(action, _NO_CLUSTER)
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
            template_spec = deployment["spec"]["template"]["spec"]
            containers = template_spec["containers"]
            index = container_index(containers, api_op["container"])
            ops = build_json_patch(containers[index], index, api_op, pod_spec=template_spec)
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


def _configmap_body(name: str, namespace: str, data: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "demo", "managed-by": "k8s-sre-agent"},
        },
        "data": dict(data),
    }


def _json_patch(api_client: Any, path: str, ops: list[dict[str, Any]]) -> None:
    api_client.call_api(
        path,
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


def _not_found(text: str) -> bool:
    lowered = (text or "").lower()
    return "notfound" in lowered.replace(" ", "") or "not found" in lowered


def _apply_configmap_kubectl(action: dict[str, Any]) -> ApplyResult:
    if shutil.which("kubectl") is None:
        return _fail(action, _NO_CLUSTER)
    api_op = action["api_op"]
    namespace = api_op["namespace"]
    name = api_op["name"]
    data = dict(api_op["data"])
    try:
        pod = _kubectl_json(["kubectl", "get", "pod", api_op["pod"], "-n", namespace, "-o", "json"])
        if not pod_references_configmap(pod, name):
            return _fail(
                action,
                f"Pod {namespace}/{api_op['pod']} 没有引用 ConfigMap {name}，已停止执行",
            )
        code, out, err = _run_kubectl(
            ["kubectl", "get", "configmap", name, "-n", namespace, "-o", "json"]
        )
        if code != 0:
            if not _not_found(err + out):
                return _fail(action, f"读取 ConfigMap 失败: {_clip(err or out)}")
            body = _configmap_body(name, namespace, data)
            applied, _stdout, apply_err = _run_kubectl(
                ["kubectl", "apply", "-f", "-", "-n", namespace],
                input_text=json.dumps(body, ensure_ascii=False),
            )
            if applied != 0:
                return _fail(action, f"创建 ConfigMap 失败: {_clip(apply_err)}")
            return _ok(
                action,
                (
                    f"已通过 kubectl 创建 ConfigMap {namespace}/{name}。"
                    "未写入 Secret，也未删除 Pod；kubelet 会重试挂载。"
                ),
                via="kubectl",
            )
        existing = json.loads(out)
        ops = build_configmap_key_patch(existing.get("data"), data)
        if not ops:
            return _ok(
                action,
                f"ConfigMap {namespace}/{name} 已包含所需的键，未覆盖已有数据。",
                via="kubectl",
            )
        patched, _stdout, patch_err = _run_kubectl(
            [
                "kubectl",
                "patch",
                "configmap",
                name,
                "-n",
                namespace,
                "--type=json",
                "-p",
                json.dumps(ops, ensure_ascii=False),
            ]
        )
        if patched != 0:
            return _fail(action, f"kubectl patch configmap 失败: {_clip(patch_err)}")
        return _ok(
            action,
            (
                f"已通过 kubectl 补齐 ConfigMap {namespace}/{name} 中缺失的键，未覆盖已有值。"
                "未删除 Pod。"
            ),
            via="kubectl",
        )
    except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return _fail(action, str(exc))


def _apply_configmap(action: dict[str, Any]) -> ApplyResult:
    from kubernetes.client.exceptions import ApiException

    from src.k8s.client import get_core_v1

    core = get_core_v1()
    if core is None:
        return _apply_configmap_kubectl(action)
    api_op = action["api_op"]
    namespace = api_op["namespace"]
    name = api_op["name"]
    data = dict(api_op["data"])
    try:
        pod = core.read_namespaced_pod(name=api_op["pod"], namespace=namespace)
        pod_dict = core.api_client.sanitize_for_serialization(pod)
        if not pod_references_configmap(pod_dict, name):
            return _fail(
                action,
                f"Pod {namespace}/{api_op['pod']} 没有引用 ConfigMap {name}，已停止执行",
            )
        try:
            existing = core.read_namespaced_config_map(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                return _fail(action, f"Kubernetes API {exc.status}: {_clip(exc.body or str(exc))}")
            core.create_namespaced_config_map(
                namespace=namespace,
                body=_configmap_body(name, namespace, data),
            )
            return _ok(
                action,
                (
                    f"已创建 ConfigMap {namespace}/{name}。"
                    "未写入 Secret，也未删除 Pod；kubelet 会重试挂载。"
                ),
                via="kubernetes-client",
            )
        existing_dict = core.api_client.sanitize_for_serialization(existing)
        ops = build_configmap_key_patch((existing_dict or {}).get("data"), data)
        if not ops:
            return _ok(
                action,
                f"ConfigMap {namespace}/{name} 已包含所需的键，未覆盖已有数据。",
                via="kubernetes-client",
            )
        _json_patch(core.api_client, f"/api/v1/namespaces/{namespace}/configmaps/{name}", ops)
        return _ok(
            action,
            (
                f"已补齐 ConfigMap {namespace}/{name} 中缺失的键，未覆盖已有值。"
                "未删除 Pod；kubelet 会重试挂载。"
            ),
            via="kubernetes-client",
        )
    except ApiException as exc:
        return _fail(action, f"Kubernetes API {exc.status}: {_clip(exc.body or str(exc))}")


def _apply_service_kubectl(action: dict[str, Any]) -> ApplyResult:
    if shutil.which("kubectl") is None:
        return _fail(action, _NO_CLUSTER)
    api_op = action["api_op"]
    namespace = api_op["namespace"]
    service = api_op["service"]
    selector = dict(api_op["selector"])
    try:
        pod = _kubectl_json(["kubectl", "get", "pod", api_op["pod"], "-n", namespace, "-o", "json"])
        if not pod_labels_match(pod, selector):
            return _fail(
                action,
                f"Pod {namespace}/{api_op['pod']} 的标签不包含目标 selector，已停止执行",
            )
        current = _kubectl_json(["kubectl", "get", "service", service, "-n", namespace, "-o", "json"])
        ops = build_selector_patch((current.get("spec") or {}).get("selector"), selector)
        code, _out, err = _run_kubectl(
            [
                "kubectl",
                "patch",
                "service",
                service,
                "-n",
                namespace,
                "--type=json",
                "-p",
                json.dumps(ops, ensure_ascii=False),
            ]
        )
        if code != 0:
            return _fail(action, f"kubectl patch service 失败: {_clip(err)}")
        return _ok(
            action,
            (
                f"已通过 kubectl 将 Service {namespace}/{service} 的 selector 替换为目标标签。"
                "未改端口，也未改 NetworkPolicy。"
            ),
            via="kubectl",
        )
    except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return _fail(action, str(exc))


def _apply_service_selector(action: dict[str, Any]) -> ApplyResult:
    from kubernetes.client.exceptions import ApiException

    from src.k8s.client import get_core_v1

    core = get_core_v1()
    if core is None:
        return _apply_service_kubectl(action)
    api_op = action["api_op"]
    namespace = api_op["namespace"]
    service = api_op["service"]
    selector = dict(api_op["selector"])
    try:
        pod = core.read_namespaced_pod(name=api_op["pod"], namespace=namespace)
        pod_dict = core.api_client.sanitize_for_serialization(pod)
        if not pod_labels_match(pod_dict, selector):
            return _fail(
                action,
                f"Pod {namespace}/{api_op['pod']} 的标签不包含目标 selector，已停止执行",
            )
        current = core.read_namespaced_service(name=service, namespace=namespace)
        current_dict = core.api_client.sanitize_for_serialization(current)
        ops = build_selector_patch((current_dict.get("spec") or {}).get("selector"), selector)
        _json_patch(core.api_client, f"/api/v1/namespaces/{namespace}/services/{service}", ops)
        return _ok(
            action,
            (
                f"已将 Service {namespace}/{service} 的 selector 替换为目标标签。"
                "未改端口，也未改 NetworkPolicy。"
            ),
            via="kubernetes-client",
        )
    except ApiException as exc:
        return _fail(action, f"Kubernetes API {exc.status}: {_clip(exc.body or str(exc))}")


def apply_live(action: dict[str, Any]) -> ApplyResult:
    """对已确认的动作做真实变更。调用方必须先通过确认门闩。"""
    problem = _check_op(action)
    if problem:
        return _fail(action, problem)
    op = (action.get("api_op") or {}).get("op")
    if op == "create_configmap":
        return _apply_configmap(action)
    if op == "fix_selector":
        return _apply_service_selector(action)
    from src.k8s.client import get_apps_v1, get_core_v1

    if get_core_v1() is not None and get_apps_v1() is not None:
        return _apply_with_client(action)
    return _apply_with_kubectl(action)
