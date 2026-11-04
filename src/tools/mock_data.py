"""三类故障的 Mock 证据数据，保证 DEMO 无集群可复现。"""

from __future__ import annotations

from typing import Any

# 场景 → 默认 Pod 名
SCENARIO_PODS = {
    "imagepull": "demo-imagepull",
    "oom": "demo-oom",
    "crashloop": "demo-crashloop",
}


def _base_meta(scenario: str, namespace: str = "default") -> dict[str, Any]:
    return {
        "scenario": scenario,
        "namespace": namespace,
        "pod": SCENARIO_PODS[scenario],
    }


MOCK_PODS: dict[str, dict[str, Any]] = {
    "imagepull": {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "demo-imagepull",
            "namespace": "default",
            "labels": {"app": "demo", "fault": "imagepull"},
        },
        "status": {
            "phase": "Pending",
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restartCount": 0,
                    "state": {
                        "waiting": {
                            "reason": "ImagePullBackOff",
                            "message": (
                                'Back-off pulling image "nginx:this-tag-does-not-exist-xyz"'
                            ),
                        }
                    },
                }
            ],
            "conditions": [
                {
                    "type": "PodScheduled",
                    "status": "True",
                },
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": "ContainersNotReady",
                },
            ],
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "nginx:this-tag-does-not-exist-xyz",
                    "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}},
                }
            ]
        },
    },
    "oom": {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "demo-oom",
            "namespace": "default",
            "labels": {"app": "demo", "fault": "oom"},
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restartCount": 5,
                    "lastState": {
                        "terminated": {
                            "exitCode": 137,
                            "reason": "OOMKilled",
                            "message": "Memory limit exceeded",
                        }
                    },
                    "state": {
                        "running": {"startedAt": "2026-11-04T08:00:00Z"},
                    },
                }
            ],
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "polinux/stress",
                    "resources": {
                        "limits": {"memory": "64Mi"},
                        "requests": {"memory": "32Mi"},
                    },
                }
            ]
        },
    },
    "crashloop": {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "demo-crashloop",
            "namespace": "default",
            "labels": {"app": "demo", "fault": "crashloop"},
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restartCount": 12,
                    "state": {
                        "waiting": {
                            "reason": "CrashLoopBackOff",
                            "message": (
                                "back-off 5m0s restarting failed container=app "
                                "pod=demo-crashloop_default"
                            ),
                        }
                    },
                    "lastState": {
                        "terminated": {
                            "exitCode": 1,
                            "reason": "Error",
                            "message": "Config file /etc/app/config.yaml not found",
                        }
                    },
                }
            ],
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "busybox:1.36",
                    "command": ["sh", "-c", "echo missing config; exit 1"],
                }
            ]
        },
    },
}

MOCK_EVENTS: dict[str, list[dict[str, Any]]] = {
    "imagepull": [
        {
            "type": "Warning",
            "reason": "Failed",
            "message": (
                'Failed to pull image "nginx:this-tag-does-not-exist-xyz": '
                "rpc error: code = NotFound desc = failed to pull and unpack image"
            ),
            "count": 8,
            "involvedObject": {"kind": "Pod", "name": "demo-imagepull"},
        },
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": (
                'Back-off pulling image "nginx:this-tag-does-not-exist-xyz"'
            ),
            "count": 15,
            "involvedObject": {"kind": "Pod", "name": "demo-imagepull"},
        },
    ],
    "oom": [
        {
            "type": "Warning",
            "reason": "OOMKilling",
            "message": "Memory cgroup out of memory: Kill process 1 (stress) score 0",
            "count": 5,
            "involvedObject": {"kind": "Pod", "name": "demo-oom"},
        },
        {
            "type": "Normal",
            "reason": "Created",
            "message": "Created container app",
            "count": 6,
            "involvedObject": {"kind": "Pod", "name": "demo-oom"},
        },
    ],
    "crashloop": [
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container app in pod demo-crashloop",
            "count": 20,
            "involvedObject": {"kind": "Pod", "name": "demo-crashloop"},
        },
        {
            "type": "Normal",
            "reason": "Pulled",
            "message": 'Successfully pulled image "busybox:1.36"',
            "count": 12,
            "involvedObject": {"kind": "Pod", "name": "demo-crashloop"},
        },
    ],
}

MOCK_LOGS: dict[str, str] = {
    "imagepull": (
        "# 容器尚未启动，kubelet 无法获取应用日志。\n"
        "# 请结合 Events / describe 中的 ImagePullBackOff 信息排查镜像与凭证。\n"
    ),
    "oom": (
        "stress: info: [1] dispatching hogs: 0 cpu, 0 io, 0 vm, 1 hdd\n"
        "stress: FAIL: [1] (415) <-- worker 7 got signal 9\n"
        "stress: WARN: [1] (417) now reaping child worker processes\n"
        "Killed\n"
    ),
    "crashloop": (
        "starting app...\n"
        "ERROR: Config file /etc/app/config.yaml not found\n"
        "FATAL: cannot continue without configuration\n"
    ),
}

MOCK_DESCRIBE: dict[str, str] = {
    "imagepull": """Name:             demo-imagepull
Namespace:        default
Status:           Pending
Containers:
  app:
    Image:          nginx:this-tag-does-not-exist-xyz
    State:          Waiting
      Reason:       ImagePullBackOff
    Ready:          False
    Restart Count:  0
Events:
  Warning  Failed   Failed to pull image "nginx:this-tag-does-not-exist-xyz"
  Warning  BackOff  Back-off pulling image "nginx:this-tag-does-not-exist-xyz"
""",
    "oom": """Name:             demo-oom
Namespace:        default
Status:           Running
Containers:
  app:
    Image:          polinux/stress
    Limits:
      memory:  64Mi
    State:          Running
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
    Ready:          False
    Restart Count:  5
Events:
  Warning  OOMKilling  Memory cgroup out of memory
""",
    "crashloop": """Name:             demo-crashloop
Namespace:        default
Status:           Running
Containers:
  app:
    Image:          busybox:1.36
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       Error
      Exit Code:    1
    Ready:          False
    Restart Count:  12
Events:
  Warning  BackOff  Back-off restarting failed container
""",
}


def resolve_scenario_from_pod(pod_name: str | None) -> str | None:
    """根据 Pod 名反查场景。"""
    if not pod_name:
        return None
    for scenario, name in SCENARIO_PODS.items():
        if pod_name == name or scenario in pod_name.lower():
            return scenario
    return None


def get_mock_bundle(
    scenario: str, namespace: str = "default"
) -> dict[str, Any]:
    """返回某场景的完整 Mock 证据包。"""
    if scenario not in MOCK_PODS:
        raise KeyError(f"未知 Mock 场景: {scenario}")
    return {
        **_base_meta(scenario, namespace),
        "pod": MOCK_PODS[scenario],
        "events": MOCK_EVENTS[scenario],
        "logs": MOCK_LOGS[scenario],
        "describe": MOCK_DESCRIBE[scenario],
    }
