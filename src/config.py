"""项目配置：从环境变量与 .env 加载。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

MOCK: bool = os.getenv("MOCK", "1").strip() not in {"0", "false", "False", "no", "NO"}
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY") or None
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
KUBECONFIG: str | None = os.getenv("KUBECONFIG") or None
K8S_NAMESPACE: str = os.getenv("K8S_NAMESPACE", "default")
RAG_INDEX_DIR: Path = Path(os.getenv("RAG_INDEX_DIR", str(ROOT_DIR / ".rag_index")))
RUNBOOKS_DIR: Path = ROOT_DIR / "runbooks"
PROMPTS_DIR: Path = ROOT_DIR / "prompts"

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


def normalize_scenario(name: str | None) -> str | None:
    """将用户输入归一化为目录里的场景 id。"""
    if not name:
        return None
    key = name.strip().lower().replace("-", "").replace("_", "")
    return SCENARIO_ALIASES.get(key)


def has_llm() -> bool:
    """是否配置了可用的 LLM API Key。"""
    return bool(OPENAI_API_KEY and OPENAI_API_KEY.strip())
