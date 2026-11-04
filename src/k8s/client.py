"""真实 Kubernetes 客户端封装；无集群时返回 None，不抛致命错误。"""

from __future__ import annotations

import logging
from typing import Any

from src import config

logger = logging.getLogger(__name__)

_core_v1: Any = None
_apps_v1: Any = None
_loaded = False


def _load_clients() -> None:
    """懒加载 CoreV1 / AppsV1；失败时两者都保持 None。"""
    global _core_v1, _apps_v1, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from kubernetes import client, config as k8s_config

        if config.KUBECONFIG:
            k8s_config.load_kube_config(config_file=config.KUBECONFIG)
        else:
            try:
                k8s_config.load_incluster_config()
            except Exception:  # noqa: BLE001
                k8s_config.load_kube_config()
        _core_v1 = client.CoreV1Api()
        _apps_v1 = client.AppsV1Api()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kubernetes 客户端不可用: %s", exc)
        _core_v1 = None
        _apps_v1 = None


def get_core_v1():
    """懒加载 CoreV1Api；失败时返回 None。"""
    _load_clients()
    return _core_v1


def get_apps_v1():
    """懒加载 AppsV1Api；失败时返回 None。"""
    _load_clients()
    return _apps_v1


def cluster_available() -> bool:
    """探测集群是否可访问。"""
    api = get_core_v1()
    if api is None:
        return False
    try:
        api.list_namespace(limit=1)
        return True
    except Exception:  # noqa: BLE001
        return False
