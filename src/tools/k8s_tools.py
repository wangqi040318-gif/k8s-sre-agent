"""K8s 取证工具：MOCK=1 时走假数据，否则尝试真实集群。"""

from __future__ import annotations

import json
from typing import Any

from src import config
from src.tools import mock_data


def _scenario_or_infer(
    scenario: str | None, pod_name: str | None
) -> str:
    """确定场景：显式 > Pod 名推断 > 默认 imagepull。"""
    normalized = config.normalize_scenario(scenario)
    if normalized:
        return normalized
    inferred = mock_data.resolve_scenario_from_pod(pod_name)
    return inferred or "imagepull"


def get_pod(
    pod_name: str | None = None,
    namespace: str | None = None,
    scenario: str | None = None,
) -> dict[str, Any]:
    """获取 Pod 对象（dict）。"""
    ns = namespace or config.K8S_NAMESPACE
    if config.MOCK:
        sc = _scenario_or_infer(scenario, pod_name)
        bundle = mock_data.get_mock_bundle(sc, ns)
        return bundle["pod"]

    from src.k8s.client import get_core_v1

    name = pod_name or mock_data.SCENARIO_PODS.get(
        config.normalize_scenario(scenario) or "imagepull", "demo-imagepull"
    )
    api = get_core_v1()
    if api is None:
        raise RuntimeError("无法连接 Kubernetes API（请检查 KUBECONFIG 或设置 MOCK=1）")
    pod = api.read_namespaced_pod(name=name, namespace=ns)
    # 转为可 JSON 序列化的简化结构
    return json.loads(json.dumps(pod.to_dict(), default=str))


def get_events(
    pod_name: str | None = None,
    namespace: str | None = None,
    scenario: str | None = None,
) -> list[dict[str, Any]]:
    """获取与 Pod 相关的 Events。"""
    ns = namespace or config.K8S_NAMESPACE
    if config.MOCK:
        sc = _scenario_or_infer(scenario, pod_name)
        return mock_data.get_mock_bundle(sc, ns)["events"]

    from src.k8s.client import get_core_v1

    name = pod_name or mock_data.SCENARIO_PODS.get(
        config.normalize_scenario(scenario) or "imagepull", "demo-imagepull"
    )
    api = get_core_v1()
    if api is None:
        raise RuntimeError("无法连接 Kubernetes API（请检查 KUBECONFIG 或设置 MOCK=1）")
    field_selector = f"involvedObject.name={name},involvedObject.kind=Pod"
    events = api.list_namespaced_event(namespace=ns, field_selector=field_selector)
    result = []
    for e in events.items:
        result.append(
            {
                "type": e.type,
                "reason": e.reason,
                "message": e.message,
                "count": e.count,
                "involvedObject": {
                    "kind": getattr(e.involved_object, "kind", None),
                    "name": getattr(e.involved_object, "name", None),
                },
            }
        )
    return result


def get_logs(
    pod_name: str | None = None,
    namespace: str | None = None,
    scenario: str | None = None,
    tail_lines: int = 100,
) -> str:
    """获取容器日志。"""
    ns = namespace or config.K8S_NAMESPACE
    if config.MOCK:
        sc = _scenario_or_infer(scenario, pod_name)
        return mock_data.get_mock_bundle(sc, ns)["logs"]

    from src.k8s.client import get_core_v1

    name = pod_name or mock_data.SCENARIO_PODS.get(
        config.normalize_scenario(scenario) or "imagepull", "demo-imagepull"
    )
    api = get_core_v1()
    if api is None:
        raise RuntimeError("无法连接 Kubernetes API（请检查 KUBECONFIG 或设置 MOCK=1）")
    try:
        return api.read_namespaced_pod_log(
            name=name, namespace=ns, tail_lines=tail_lines
        )
    except Exception as exc:  # noqa: BLE001
        return f"# 无法读取日志: {exc}\n"


def describe_pod(
    pod_name: str | None = None,
    namespace: str | None = None,
    scenario: str | None = None,
) -> str:
    """返回类似 kubectl describe 的文本摘要。"""
    ns = namespace or config.K8S_NAMESPACE
    if config.MOCK:
        sc = _scenario_or_infer(scenario, pod_name)
        return mock_data.get_mock_bundle(sc, ns)["describe"]

    pod = get_pod(pod_name=pod_name, namespace=ns, scenario=scenario)
    events = get_events(pod_name=pod_name, namespace=ns, scenario=scenario)
    lines = [
        f"Name:             {pod.get('metadata', {}).get('name')}",
        f"Namespace:        {pod.get('metadata', {}).get('namespace', ns)}",
        f"Status:           {pod.get('status', {}).get('phase')}",
        "Containers:",
    ]
    for cs in pod.get("status", {}).get("container_statuses") or pod.get(
        "status", {}
    ).get("containerStatuses") or []:
        lines.append(f"  {cs.get('name')}:")
        lines.append(f"    Ready:          {cs.get('ready')}")
        lines.append(f"    Restart Count:  {cs.get('restart_count', cs.get('restartCount'))}")
        state = cs.get("state") or {}
        lines.append(f"    State:          {json.dumps(state, ensure_ascii=False)}")
    lines.append("Events:")
    for e in events[:10]:
        lines.append(f"  {e.get('type')}  {e.get('reason')}  {e.get('message')}")
    return "\n".join(lines) + "\n"


def collect_evidence(
    scenario: str | None = None,
    pod_name: str | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    """一次收集 Pod / Events / Logs / Describe。"""
    ns = namespace or config.K8S_NAMESPACE
    sc = _scenario_or_infer(scenario, pod_name) if config.MOCK else (
        config.normalize_scenario(scenario) or scenario
    )
    name = pod_name
    if config.MOCK and not name:
        name = mock_data.SCENARIO_PODS.get(sc or "imagepull")

    return {
        "scenario": sc,
        "namespace": ns,
        "pod_name": name,
        "mode": "mock" if config.MOCK else "live",
        "pod": get_pod(pod_name=name, namespace=ns, scenario=sc),
        "events": get_events(pod_name=name, namespace=ns, scenario=sc),
        "logs": get_logs(pod_name=name, namespace=ns, scenario=sc),
        "describe": describe_pod(pod_name=name, namespace=ns, scenario=sc),
    }
