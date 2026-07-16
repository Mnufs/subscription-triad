#!/usr/bin/env python3
"""Manual CLI for Model Combo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import combo_core


def read_file(path: str) -> str:
    return Path(path).expanduser().read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="model-combo",
        description="Subscription-native Codex/Fable/Grok orchestration",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--project")

    create = sub.add_parser("create")
    create.add_argument("--project", required=True)
    create.add_argument("--task-file", required=True)
    create.add_argument("--acceptance-file", required=True)
    create.add_argument("--context-file")

    plan = sub.add_parser("plan")
    plan.add_argument("--run", required=True)
    plan.add_argument("--plan-file", required=True)

    review = sub.add_parser("review")
    review.add_argument("--run", required=True)
    review.add_argument("--effort", default="high", choices=("low", "medium", "high", "xhigh", "max"))

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--run", required=True)

    status = sub.add_parser("status")
    status.add_argument("--run", required=True)

    followup = sub.add_parser("continue")
    followup.add_argument("--run", required=True)
    followup.add_argument("--instructions-file", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--run", required=True)
    verify.add_argument("--verdict", choices=("pass", "fail"), required=True)
    verify.add_argument("--report-file", required=True)
    return parser


def execute(args: argparse.Namespace):
    if args.command == "doctor":
        return combo_core.doctor(args.project)
    if args.command == "create":
        context = read_file(args.context_file) if args.context_file else "No additional context supplied."
        return combo_core.create_run(args.project, read_file(args.task_file), read_file(args.acceptance_file), context)
    if args.command == "plan":
        return combo_core.record_plan(args.run, read_file(args.plan_file))
    if args.command == "review":
        return combo_core.review_plan(args.run, effort=args.effort)
    if args.command == "dispatch":
        return combo_core.dispatch_grok(args.run)
    if args.command == "status":
        return combo_core.run_status(args.run)
    if args.command == "continue":
        return combo_core.continue_grok(args.run, read_file(args.instructions_file))
    if args.command == "verify":
        return combo_core.record_verification(args.run, args.verdict, read_file(args.report_file))
    raise combo_core.ComboError("Unknown command.")


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = execute(args)
    except (combo_core.ComboError, OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
