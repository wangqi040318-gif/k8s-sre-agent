"""将 runbooks/*.md 建成本地 TF-IDF 索引（无外网、无 chromadb 也可工作）。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from src import config


def _tokenize(text: str) -> list[str]:
    """简易分词：英文词 + 中文连续字串。"""
    text = text.lower()
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text)
    return [t for t in tokens if len(t) > 1]


def _load_runbooks(runbooks_dir: Path) -> list[dict]:
    docs = []
    for path in sorted(runbooks_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        docs.append(
            {
                "id": path.stem,
                "path": str(path),
                "title": path.stem,
                "content": content,
                "tokens": _tokenize(content),
            }
        )
    return docs


def build_index(
    runbooks_dir: Path | None = None,
    index_dir: Path | None = None,
) -> Path:
    """构建并持久化索引；返回索引目录。"""
    runbooks_dir = runbooks_dir or config.RUNBOOKS_DIR
    index_dir = Path(index_dir or config.RAG_INDEX_DIR)
    index_dir.mkdir(parents=True, exist_ok=True)

    docs = _load_runbooks(runbooks_dir)
    # 文档频率
    df: Counter[str] = Counter()
    for d in docs:
        for term in set(d["tokens"]):
            df[term] += 1

    n_docs = max(len(docs), 1)
    idf = {term: 1.0 + (n_docs / (1 + freq)) for term, freq in df.items()}

    vectors = []
    for d in docs:
        tf = Counter(d["tokens"])
        total = max(sum(tf.values()), 1)
        vec = {term: (count / total) * idf.get(term, 1.0) for term, count in tf.items()}
        vectors.append(
            {
                "id": d["id"],
                "path": d["path"],
                "title": d["title"],
                "content": d["content"],
                "vector": vec,
            }
        )

    payload = {"docs": vectors, "idf": idf}
    out = index_dir / "tfidf_index.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_dir


def main() -> None:
    path = build_index()
    print(f"RAG 索引已写入: {path / 'tfidf_index.json'}")


if __name__ == "__main__":
    main()
