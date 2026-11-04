"""命令行入口：python -m src.cli --scenario imagepull

诊断默认只提案。执行修复必须：
  python -m src.cli --apply --confirm YES --action <id>
或在交互提示「输入 CONFIRM 后执行」时输入 CONFIRM。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from src import config
from src.agent.graph import run_diagnose
from src.rag.ingest import build_index
from src.remediation.apply import apply_action
from src.remediation.catalog import resolve_action

logger = logging.getLogger("src.remediation")


class _CurrentStderrHandler(logging.Handler):
    """写到当前 sys.stderr。pytest 会替换 stderr，不能在创建时绑死流。"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            self.handleError(record)


def _configure_logging() -> None:
    """让 apply 审计日志出现在 stderr，且不抬高其他库的日志级别。"""
    logger.setLevel(logging.INFO)
    if any(isinstance(handler, _CurrentStderrHandler) for handler in logger.handlers):
        return
    handler = _CurrentStderrHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="K8s SRE Agent：故障诊断与人工确认后的修复（默认不改集群）"
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
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行修复动作。未确认时拒绝，诊断本身不会自动带上本标志",
    )
    parser.add_argument(
        "--confirm",
        default=None,
        help="非交互确认，只接受大写 YES。不提供则在交互终端提示输入 CONFIRM",
    )
    parser.add_argument(
        "--action",
        default=None,
        help="修复动作 id，例如 fix-image、bump-memory、fix-command",
    )
    return parser


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _prompt_confirm(action: dict) -> str | None:
    print(f"即将执行修复动作 {action['id']}（风险: {action['risk_level']}）。")
    print(f"说明: {action['description']}")
    print("命令:")
    print(action["command"])
    print("未输入 CONFIRM 将取消，不会改动集群。")
    try:
        return input("输入 CONFIRM 后执行: ").strip()
    except EOFError:
        return None


def _run_apply(args: argparse.Namespace) -> int:
    namespace = _blank(args.namespace)
    pod_name = _blank(args.pod)
    if not _blank(args.action):
        print("已拒绝执行，集群未被修改。\n原因: --apply 必须同时指定 --action <id>。")
        logger.info(
            "apply_audit action=- scenario=%s namespace=%s pod=%s mock=%s confirmed=no decision=refused_missing_action",
            args.scenario or "-",
            namespace or "-",
            pod_name or "-",
            "1" if config.MOCK else "0",
        )
        return 2

    action, error = resolve_action(
        args.action,
        scenario=args.scenario,
        namespace=namespace,
        pod_name=pod_name,
    )
    confirm = args.confirm.strip() if isinstance(args.confirm, str) else None
    if error or action is None:
        confirm_value = confirm
    elif confirm is None and sys.stdin.isatty():
        confirm_value = _prompt_confirm(action)
    else:
        confirm_value = confirm

    result = apply_action(
        args.action,
        confirm=confirm_value,
        scenario=args.scenario,
        namespace=namespace,
        pod_name=pod_name,
    )
    print(result.message)
    if result.ok:
        return 0
    if result.refused:
        return 2
    return 1


def _run_diagnose(args: argparse.Namespace) -> int:
    if args.confirm is not None or args.action:
        print(
            "[提示] 未加 --apply，本次只做诊断，不会改动集群。",
            file=sys.stderr,
        )
    build_index()
    print(
        f"[K8s SRE Agent] MOCK={config.MOCK} LLM={'on' if config.has_llm() else 'off(规则回退)'}"
    )
    result = run_diagnose(
        scenario=args.scenario,
        alert_text=args.alert,
        namespace=_blank(args.namespace),
        pod_name=_blank(args.pod),
    )
    print(result.get("report_markdown") or "(空报告)")
    actions = (result.get("report_json") or {}).get("remediation") or []
    if actions:
        ids = "、".join(item["id"] for item in actions)
        print("\n[提示] 以上为提案，尚未执行。确认后可运行：")
        print("  python -m src.cli --apply --confirm YES --action <id> --scenario <scenario>")
        print(f"  本次可选动作: {ids}")
    if args.json:
        print("\n--- report_json ---")
        print(json.dumps(result.get("report_json") or {}, ensure_ascii=False, indent=2))
    if result.get("errors"):
        print("\n[警告] 流水线错误:", result["errors"], file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_parser().parse_args(argv)

    if args.ingest:
        path = build_index()
        print(f"RAG 索引已更新: {path}")
        return 0

    if args.apply:
        return _run_apply(args)
    return _run_diagnose(args)


if __name__ == "__main__":
    raise SystemExit(main())
