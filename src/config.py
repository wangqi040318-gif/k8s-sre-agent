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

# 场景别名
SCENARIO_ALIASES = {
    "imagepull": "imagepull",
    "imagepullbackoff": "imagepull",
    "oom": "oom",
    "oomkilled": "oom",
    "crashloop": "crashloop",
    "crashloopbackoff": "crashloop",
}


def normalize_scenario(name: str | None) -> str | None:
    """将用户输入归一化为 imagepull / oom / crashloop。"""
    if not name:
        return None
    key = name.strip().lower().replace("-", "").replace("_", "")
    return SCENARIO_ALIASES.get(key)


def has_llm() -> bool:
    """是否配置了可用的 LLM API Key。"""
    return bool(OPENAI_API_KEY and OPENAI_API_KEY.strip())
