#!/usr/bin/env python3
"""Detached Grok Build worker used by Subscription Triad."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading

import triad_core
import triad_session


def _provider_session_ended(_signum, _frame) -> None:
    raise triad_core.TriadError("The approved provider session ended before Grok completed.")


def _terminate_worker_group() -> None:
    try:
        if os.name == "posix" and os.getpgrp() == os.getpid():
            os.killpg(os.getpgrp(), signal.SIGTERM)
        else:
            os.kill(os.getpid(), signal.SIGTERM)
    except OSError:
        return


def _start_lease_watchdog() -> threading.Event:
    stop = threading.Event()
    lease_value = os.environ.get(triad_session.LEASE_PATH_ENV)
    token = os.environ.get(triad_session.LEASE_TOKEN_ENV)
    if lease_value is None and token is None:
        return stop
    if not lease_value or not token:
        raise triad_core.TriadError("Provider session lease environment is incomplete.")
    lease_path = Path(lease_value).expanduser()
    if not triad_session.lease_is_current(lease_path, token):
        raise triad_core.TriadError("Provider session lease is not current.")

    def watch() -> None:
        while not stop.wait(triad_session.LEASE_HEARTBEAT_SECONDS):
            if not triad_session.lease_is_current(lease_path, token):
                _terminate_worker_group()
                return

    threading.Thread(
        target=watch,
        name="triad-provider-lease-watchdog",
        daemon=True,
    ).start()
    return stop


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one detached Subscription Triad Grok round.")
    parser.add_argument("--run", required=True)
    parser.add_argument("--agmsg-root")
    parser.add_argument("--mode", choices=("initial", "continue"), required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _provider_session_ended)
    watchdog: threading.Event
    try:
        watchdog = _start_lease_watchdog()
        result = triad_core.run_grok_worker(args.run, args.agmsg_root, args.mode)
    except triad_core.TriadError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), flush=True)
        return 1
    finally:
        if "watchdog" in locals():
            watchdog.set()
    print(json.dumps({"ok": True, "result": result}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
