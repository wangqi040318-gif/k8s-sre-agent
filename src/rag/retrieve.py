"""基于本地 TF-IDF 索引的 Runbook 检索。"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from src import config
from src.rag.ingest import build_index, _tokenize


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _ensure_index() -> Path:
    index_file = Path(config.RAG_INDEX_DIR) / "tfidf_index.json"
    if not index_file.exists():
        build_index()
    return index_file


def retrieve_runbooks(query: str, top_k: int = 2) -> list[dict[str, str]]:
    """检索与 query 最相关的 runbook 片段。"""
    index_file = _ensure_index()
    payload = json.loads(index_file.read_text(encoding="utf-8"))
    idf: dict[str, float] = payload.get("idf", {})
    docs = payload.get("docs", [])

    tokens = _tokenize(query)
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    total = max(sum(tf.values()), 1)
    qvec = {t: (c / total) * idf.get(t, 1.0) for t, c in tf.items()}

    scored = []
    for d in docs:
        score = _cosine(qvec, d.get("vector", {}))
        # 关键词兜底加权
        content_l = d.get("content", "").lower()
        for t in tokens:
            if t in content_l or t in d.get("id", "").lower():
                score += 0.05
        scored.append((score, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, d in scored[:top_k]:
        content = d.get("content", "")
        # 截断过长内容
        snippet = content if len(content) <= 2500 else content[:2500] + "\n...\n"
        results.append(
            {
                "id": d.get("id", ""),
                "title": d.get("title", ""),
                "path": d.get("path", ""),
                "score": f"{score:.4f}",
                "content": snippet,
            }
        )
    return results


def build_query_from_evidence(evidence: dict, alert_text: str = "") -> str:
    """从证据拼检索 query。"""
    parts = [alert_text or ""]
    parts.append(str(evidence.get("scenario") or ""))
    parts.append(evidence.get("describe") or "")
    parts.append(evidence.get("logs") or "")
    for e in evidence.get("events") or []:
        parts.append(str(e.get("reason", "")))
        parts.append(str(e.get("message", "")))
    # 压缩空白
    text = re.sub(r"\s+", " ", " ".join(parts))
    return text[:4000]
