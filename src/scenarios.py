"""值班故障场景目录。

CLI、修复目录和 Streamlit 侧栏共用这一份顺序和别名。
不要从这里导入 src.config：config 会再导出这些名字，反过来会循环导入。
"""

from __future__ import annotations

# 稳定顺序：CLI choices、界面下拉和文档都用这份列表。
SCENARIO_CHOICES = [
    "imagepull",
    "oom",
    "crashloop",
    "pending",
    "configmount",
    "probe",
    "connectivity",
]

SCENARIO_LABELS = {
    "imagepull": "ImagePullBackOff",
    "oom": "OOMKilled",
    "crashloop": "CrashLoopBackOff",
    "pending": "Pending / 无法调度",
    "configmount": "ConfigMap 挂载失败",
    "probe": "探针未就绪",
    "connectivity": "Service 无 Endpoints",
}

# 场景别名。键里不要带连字符：normalize 会先去掉 - 和 _。
SCENARIO_ALIASES = {
    "imagepull": "imagepull",
    "imagepullbackoff": "imagepull",
    "errimagepull": "imagepull",
    "oom": "oom",
    "oomkilled": "oom",
    "crashloop": "crashloop",
    "crashloopbackoff": "crashloop",
    "pending": "pending",
    "unschedulable": "pending",
    "failedscheduling": "pending",
    "configmount": "configmount",
    "configerror": "configmount",
    "createcontainerconfigerror": "configmount",
    "failedmount": "configmount",
    "probe": "probe",
    "probefailure": "probe",
    "probfail": "probe",
    "readiness": "probe",
    "liveness": "probe",
    "connectivity": "connectivity",
    "endpoints": "connectivity",
    "serviceendpoints": "connectivity",
    "servicedns": "connectivity",
}


def scenario_label(scenario_id: str) -> str:
    """下拉框展示名。目录里没有的 id 原样返回，避免页面在格式化时崩溃。"""
    return SCENARIO_LABELS.get(scenario_id, scenario_id)


def normalize_scenario(name: str | None) -> str | None:
    """将用户输入归一化为目录里的场景 id。"""
    if not name:
        return None
    key = name.strip().lower().replace("-", "").replace("_", "")
    return SCENARIO_ALIASES.get(key)
