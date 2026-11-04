"""演示故障的 Mock 证据。无集群时诊断、检索和确认后的模拟都读这里。"""

from __future__ import annotations

from typing import Any

# 场景 → 默认 Pod 名
SCENARIO_PODS = {
    "imagepull": "demo-imagepull",
    "oom": "demo-oom",
    "crashloop": "demo-crashloop",
    "pending": "demo-pending",
    "configmount": "demo-configmount",
    "probe": "demo-probe",
    "connectivity": "demo-connectivity",
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
    "pending": {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "demo-pending",
            "namespace": "default",
            "labels": {"app": "demo", "fault": "pending"},
        },
        "status": {
            "phase": "Pending",
            "conditions": [
                {
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": (
                        "0/3 nodes are available: 1 Insufficient cpu, "
                        "1 Insufficient memory, 2 node(s) didn't match "
                        "Pod's node affinity/selector."
                    ),
                }
            ],
        },
        "spec": {
            "nodeSelector": {"demo.sre/pool": "oncall-pool-missing"},
            "affinity": {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [
                            {
                                "matchExpressions": [
                                    {
                                        "key": "kubernetes.io/hostname",
                                        "operator": "In",
                                        "values": ["node-that-does-not-exist"],
                                    }
                                ]
                            }
                        ]
                    }
                }
            },
            "tolerations": [
                {
                    "key": "node.kubernetes.io/not-ready",
                    "operator": "Exists",
                    "effect": "NoExecute",
                    "tolerationSeconds": 300,
                }
            ],
            "containers": [
                {
                    "name": "app",
                    "image": "nginx:1.27",
                    "resources": {
                        "requests": {"cpu": "8", "memory": "16Gi"},
                        "limits": {"cpu": "8", "memory": "16Gi"},
                    },
                }
            ],
        },
    },
    "configmount": {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "demo-configmount",
            "namespace": "default",
            "labels": {"app": "demo", "fault": "configmount"},
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
                            "reason": "CreateContainerConfigError",
                            "message": 'configmap "app-config" not found',
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
                    "command": ["sh", "-c", "echo started; sleep 3600"],
                    "env": [
                        {
                            "name": "LOG_LEVEL",
                            "valueFrom": {
                                "configMapKeyRef": {
                                    "name": "app-config",
                                    "key": "LOG_LEVEL",
                                }
                            },
                        }
                    ],
                    "volumeMounts": [
                        {"name": "config", "mountPath": "/etc/app", "readOnly": True}
                    ],
                }
            ],
            "volumes": [
                {"name": "config", "configMap": {"name": "app-config"}}
            ],
        },
    },
    "probe": {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "demo-probe",
            "namespace": "default",
            "labels": {"app": "demo", "fault": "probe"},
        },
        "status": {
            "phase": "Running",
            "conditions": [
                {
                    "type": "Ready",
                    "status": "False",
                    "reason": "ContainersNotReady",
                    "message": "containers with unready status: [app]",
                }
            ],
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": False,
                    "restartCount": 4,
                    "state": {"running": {"startedAt": "2026-11-04T08:10:00Z"}},
                    "lastState": {
                        "terminated": {
                            "exitCode": 1,
                            "reason": "Error",
                            "message": "Liveness probe failed",
                        }
                    },
                }
            ],
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "nginx:1.27",
                    "ports": [{"containerPort": 80, "name": "http"}],
                    "readinessProbe": {
                        "httpGet": {"path": "/healthz", "port": 8080},
                        "periodSeconds": 5,
                        "failureThreshold": 3,
                    },
                    "livenessProbe": {
                        "httpGet": {"path": "/healthz", "port": 8080},
                        "periodSeconds": 5,
                        "failureThreshold": 3,
                    },
                }
            ]
        },
    },
    "connectivity": {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "demo-connectivity",
            "namespace": "default",
            "labels": {"app": "demo", "fault": "connectivity"},
        },
        "status": {
            "phase": "Running",
            "conditions": [
                {"type": "Ready", "status": "True"},
            ],
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": True,
                    "restartCount": 0,
                    "state": {"running": {"startedAt": "2026-11-04T08:20:00Z"}},
                }
            ],
        },
        "spec": {
            "containers": [
                {
                    "name": "app",
                    "image": "nginx:1.27",
                    "ports": [{"containerPort": 80, "name": "http"}],
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
    "pending": [
        {
            "type": "Warning",
            "reason": "FailedScheduling",
            "message": (
                "0/3 nodes are available: 1 Insufficient cpu, "
                "1 Insufficient memory, 2 node(s) didn't match "
                "Pod's node affinity/selector."
            ),
            "count": 42,
            "involvedObject": {"kind": "Pod", "name": "demo-pending"},
        },
    ],
    "configmount": [
        {
            "type": "Warning",
            "reason": "Failed",
            "message": 'Error: configmap "app-config" not found',
            "count": 6,
            "involvedObject": {"kind": "Pod", "name": "demo-configmount"},
        },
        {
            "type": "Warning",
            "reason": "FailedMount",
            "message": (
                'MountVolume.SetUp failed for volume "config" : '
                'configmap "app-config" not found'
            ),
            "count": 6,
            "involvedObject": {"kind": "Pod", "name": "demo-configmount"},
        },
    ],
    "probe": [
        {
            "type": "Warning",
            "reason": "Unhealthy",
            "message": (
                'Readiness probe failed: Get "http://10.0.0.15:8080/healthz": '
                "dial tcp 10.0.0.15:8080: connect: connection refused"
            ),
            "count": 18,
            "involvedObject": {"kind": "Pod", "name": "demo-probe"},
        },
        {
            "type": "Warning",
            "reason": "Unhealthy",
            "message": "Liveness probe failed: HTTP probe failed with statuscode: 404",
            "count": 4,
            "involvedObject": {"kind": "Pod", "name": "demo-probe"},
        },
    ],
    "connectivity": [
        {
            "type": "Warning",
            "reason": "FailedToUpdateEndpoint",
            "message": (
                "Service default/demo-web has no ready addresses: "
                "selector app=frontend matches 0 pods; Endpoints is empty. "
                "Pod demo-connectivity labels are app=demo."
            ),
            "count": 15,
            "involvedObject": {"kind": "Service", "name": "demo-web"},
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
    "pending": (
        "# Pod 尚未调度，容器没有启动。\n"
        "# 见 Events 中的 FailedScheduling / Unschedulable。\n"
    ),
    "configmount": (
        "# 容器尚未创建。kubelet: CreateContainerConfigError, "
        'configmap "app-config" not found。\n'
        "# 卷 config 同时 FailedMount，缺的是同一个 ConfigMap。\n"
    ),
    "probe": (
        "nginx: listening on 0.0.0.0:80\n"
        "no listener on port 8080\n"
        "GET /healthz -> 404 (this server only serves / on port 80)\n"
    ),
    "connectivity": (
        "nslookup demo-web.default.svc.cluster.local -> 10.96.12.20\n"
        "curl: (7) Failed to connect to demo-web port 80 after DNS resolved\n"
        "Endpoints demo-web: <none>\n"
        "Pod app=demo is Ready; Service selector is still app=frontend\n"
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
    "pending": """Name:             demo-pending
Namespace:        default
Node:             <none>
Status:           Pending
Node-Selectors:   demo.sre/pool=oncall-pool-missing
Containers:
  app:
    Image:      nginx:1.27
    Requests:   cpu: 8, memory: 16Gi
    Limits:     cpu: 8, memory: 16Gi
Conditions:
  Type           Status
  PodScheduled   False
    Reason:      Unschedulable
    Message:     0/3 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 2 node(s) didn't match Pod's node affinity/selector.
Events:
  Warning  FailedScheduling  0/3 nodes are available: Insufficient cpu, Insufficient memory, node affinity/selector
""",
    "configmount": """Name:             demo-configmount
Namespace:        default
Status:           Pending
Containers:
  app:
    Image:          busybox:1.36
    State:          Waiting
      Reason:       CreateContainerConfigError
      Message:      configmap "app-config" not found
    Environment:
      LOG_LEVEL:    <set to the key 'LOG_LEVEL' of config map 'app-config'>
    Mounts:
      /etc/app from config (ro)
Volumes:
  config:
    Type:      ConfigMap (a volume populated by a ConfigMap)
    Name:      app-config
    Optional:  false
Events:
  Warning  Failed       Error: configmap "app-config" not found
  Warning  FailedMount  MountVolume.SetUp failed for volume "config" : configmap "app-config" not found
""",
    "probe": """Name:             demo-probe
Namespace:        default
Status:           Running
Containers:
  app:
    Image:          nginx:1.27
    Port:           80/TCP
    State:          Running
    Last State:     Terminated
      Reason:       Error
      Message:      Liveness probe failed
      Exit Code:    1
    Ready:          False
    Restart Count:  4
    Readiness:      http-get http://:8080/healthz delay=0s timeout=1s period=5s #success=1 #failure=3
    Liveness:       http-get http://:8080/healthz delay=0s timeout=1s period=5s #success=1 #failure=3
Conditions:
  Type     Status
  Ready    False
Events:
  Warning  Unhealthy  Readiness probe failed: Get "http://10.0.0.15:8080/healthz": dial tcp 10.0.0.15:8080: connect: connection refused
  Warning  Unhealthy  Liveness probe failed: HTTP probe failed with statuscode: 404
""",
    "connectivity": """Name:             demo-connectivity
Namespace:        default
Status:           Running
Labels:           app=demo,fault=connectivity
Containers:
  app:
    Image:          nginx:1.27
    Port:           80/TCP
    State:          Running
    Ready:          True
    Restart Count:  0
Service:
  Name:       demo-web
  Namespace:  default
  Selector:   app=frontend
  Port:       80/TCP
  Endpoints:  <none>
NetworkPolicy:
  本演示命名空间没有拒绝 80 端口的 NetworkPolicy。
  Endpoints 非空但仍不通时，再查 NetworkPolicy 与 CoreDNS。
Events:
  Warning  FailedToUpdateEndpoint  selector app=frontend matches 0 pods; Endpoints is empty
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
