"""项目配置：从环境变量与 .env 加载。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 场景目录的单一来源。config 再导出，CLI 和修复目录仍写 config.SCENARIO_*。
from src.scenarios import (
    SCENARIO_ALIASES,
    SCENARIO_CHOICES,
    SCENARIO_LABELS,
    normalize_scenario,
)

__all__ = [
    "SCENARIO_ALIASES",
    "SCENARIO_CHOICES",
    "SCENARIO_LABELS",
    "normalize_scenario",
]

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


def has_llm() -> bool:
    """是否配置了可用的 LLM API Key。"""
    return bool(OPENAI_API_KEY and OPENAI_API_KEY.strip())
