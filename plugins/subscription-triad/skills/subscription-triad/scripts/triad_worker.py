#!/usr/bin/env python3
"""Detached Grok Build worker used by Subscription Triad."""

from __future__ import annotations

import argparse
import json
import sys

import triad_core


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one detached Subscription Triad Grok round.")
    parser.add_argument("--run", required=True)
    parser.add_argument("--agmsg-root")
    parser.add_argument("--mode", choices=("initial", "continue"), required=True)
    args = parser.parse_args()
    try:
        result = triad_core.run_grok_worker(args.run, args.agmsg_root, args.mode)
    except triad_core.TriadError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), flush=True)
        return 1
    print(json.dumps({"ok": True, "result": result}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
