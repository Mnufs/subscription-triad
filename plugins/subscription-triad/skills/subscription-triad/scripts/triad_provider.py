#!/usr/bin/env python3
"""Narrow host bridge for provider-dependent Subscription Triad actions.

This entry point is intentionally separate from the general debugging CLI. It
accepts only the four actions that need provider authentication or network
access, so Codex can request a one-command host approval without changing the
workspace or global network configuration.
"""

from __future__ import annotations

import argparse
import hmac
import json
from pathlib import Path
import stat
import sys

import triad_core


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="triad-provider",
        description="Scoped provider bridge for Subscription Triad",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--project", required=True)

    review = sub.add_parser("review")
    review.add_argument("--run", required=True)
    review.add_argument(
        "--effort",
        default="high",
        choices=("low", "medium", "high", "xhigh", "max"),
    )

    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--run", required=True)

    followup = sub.add_parser("continue")
    followup.add_argument("--run", required=True)
    followup.add_argument("--instructions-file", required=True)
    followup.add_argument("--instructions-sha256", required=True)
    return parser


def read_bound_instructions(run_dir: str, file_name: str, expected_sha256: str) -> str:
    store = triad_core.RunStore(Path(run_dir))
    request_root = (store.run_dir / ".provider-requests").resolve()
    supplied = Path(file_name).expanduser()
    try:
        info = supplied.lstat()
    except OSError as exc:
        raise triad_core.TriadError("Continuation request is unavailable.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise triad_core.TriadError("Continuation request must be a single regular file.")
    path = supplied.resolve()
    triad_core._ensure_within(path, request_root)
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise triad_core.TriadError("Continuation request is unreadable.") from exc
    actual_sha256 = triad_core.sha256_text(value)
    if not hmac.compare_digest(actual_sha256, expected_sha256.lower()):
        raise triad_core.TriadError("Continuation request changed after host approval was prepared.")
    try:
        path.unlink()
    except OSError as exc:
        raise triad_core.TriadError("Continuation request could not be consumed safely.") from exc
    return value


def execute(args: argparse.Namespace):
    if args.command == "doctor":
        return triad_core.doctor(args.project)
    if args.command == "review":
        return triad_core.review_plan(args.run, effort=args.effort)
    if args.command == "dispatch":
        return triad_core.dispatch_grok(args.run)
    if args.command == "continue":
        instructions = read_bound_instructions(
            args.run,
            args.instructions_file,
            args.instructions_sha256,
        )
        return triad_core.continue_grok(args.run, instructions)
    raise triad_core.TriadError("Unknown provider action.")


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = execute(args)
    except (triad_core.TriadError, OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
