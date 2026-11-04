"""命令行入口：python -m src.cli --scenario imagepull"""

from __future__ import annotations

import argparse
import json
import sys

from src import config
from src.agent.graph import run_diagnose
from src.rag.ingest import build_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="K8s SRE Agent：故障诊断（Mock/真实集群）"
    )
    parser.add_argument(
        "--scenario",
        choices=["imagepull", "oom", "crashloop"],
        default=None,
        help="预设故障场景",
    )
    parser.add_argument(
        "--alert",
        default="",
        help="自由告警文本（可与 scenario 联用）",
    )
    parser.add_argument("--namespace", default=None, help="命名空间")
    parser.add_argument("--pod", default=None, help="Pod 名称")
    parser.add_argument(
        "--json",
        action="store_true",
        help="额外打印 report_json",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="仅重建 RAG 索引后退出",
    )
    args = parser.parse_args(argv)

    if args.ingest:
        path = build_index()
        print(f"RAG 索引已更新: {path}")
        return 0

    # 确保索引存在
    build_index()

    print(
        f"[K8s SRE Agent] MOCK={config.MOCK} LLM={'on' if config.has_llm() else 'off(规则回退)'}"
    )
    result = run_diagnose(
        scenario=args.scenario,
        alert_text=args.alert,
        namespace=args.namespace,
        pod_name=args.pod,
    )
    print(result.get("report_markdown") or "(空报告)")
    if args.json:
        print("\n--- report_json ---")
        print(json.dumps(result.get("report_json") or {}, ensure_ascii=False, indent=2))
    if result.get("errors"):
        print("\n[警告] 流水线错误:", result["errors"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
