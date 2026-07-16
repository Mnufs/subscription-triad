#!/usr/bin/env python3
"""One-approval, run-scoped host bridge for Subscription Triad providers.

The process is bound to one canonical run and accepts a small JSON-lines
protocol over its existing stdin. It never executes arbitrary shell text,
changes Codex configuration, or grants a reusable command permission.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import queue
import signal
import stat
import sys
import threading
import time
from typing import Any, Dict, IO, Optional, Set, Tuple

import triad_core
import triad_session


ALLOWED_START_STATES = frozenset(
    {"planned", "approved", "executed", "verification_failed", "execution_failed"}
)
REVIEW_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
MAX_PROTOCOL_ERRORS = 3
_EOF = object()
_LINE_TOO_LONG = object()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="triad-provider",
        description="One-approval provider session for one Subscription Triad feature",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    session = sub.add_parser("session")
    session.add_argument("--run", required=True)
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
        raise triad_core.TriadError("Continuation request changed after its session input was prepared.")
    try:
        path.unlink()
    except OSError as exc:
        raise triad_core.TriadError("Continuation request could not be consumed safely.") from exc
    return value


def _request_fields(payload: Any) -> Tuple[str, str]:
    if not isinstance(payload, dict):
        raise triad_core.TriadError("Provider session input must be a JSON object.")
    action = payload.get("action")
    request_id = payload.get("request_id")
    if not isinstance(action, str):
        raise triad_core.TriadError("Provider session action must be a string.")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        raise triad_core.TriadError("Provider session request_id is invalid.")
    allowed = {
        "doctor": {"action", "request_id"},
        "review": {"action", "request_id", "effort"},
        "dispatch": {"action", "request_id"},
        "continue": {
            "action",
            "request_id",
            "instructions_file",
            "instructions_sha256",
        },
        "close": {"action", "request_id"},
    }
    if action not in allowed:
        raise triad_core.TriadError("Unsupported provider session action: %s." % action)
    unexpected = sorted(set(payload) - allowed[action])
    if unexpected:
        raise triad_core.TriadError("Unexpected provider session field(s): %s." % ", ".join(unexpected))
    return action, request_id


def _send(output: IO[str], payload: Dict[str, Any]) -> None:
    output.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    output.flush()


def _signal_worker_group(pid: int, selected_signal: int) -> None:
    try:
        if os.name == "posix":
            os.killpg(pid, selected_signal)
        else:
            os.kill(pid, selected_signal)
    except ProcessLookupError:
        return
    except OSError:
        return


class ProviderSession:
    """Strict action dispatcher for one run-scoped provider process."""

    def __init__(self, store: triad_core.RunStore, project: Path):
        self.store = store
        self.project = project
        self.doctor_report: Optional[Dict[str, Any]] = None
        self.worker_pids: Set[int] = set()

    @property
    def doctor_ready(self) -> bool:
        return isinstance(self.doctor_report, dict) and self.doctor_report.get("ready") is True

    def _require_ready(self) -> None:
        if not self.doctor_ready:
            raise triad_core.TriadError("Run doctor successfully in this provider session first.")

    def handle(self, payload: Any) -> Tuple[Dict[str, Any], bool]:
        action, request_id = _request_fields(payload)
        try:
            result, should_close = self._execute(action, payload)
        except (triad_core.TriadError, OSError, UnicodeDecodeError) as exc:
            return {
                "ok": False,
                "event": "action_result",
                "action": action,
                "request_id": request_id,
                "error": str(exc),
            }, False
        return {
            "ok": True,
            "event": "action_result",
            "action": action,
            "request_id": request_id,
            "result": result,
        }, should_close

    def _execute(self, action: str, payload: Dict[str, Any]) -> Tuple[Any, bool]:
        if action == "close":
            return {"closed": True, "reason": "requested"}, True
        if action == "doctor":
            if self.doctor_report is None:
                self.doctor_report = triad_core.doctor(str(self.project))
            return self.doctor_report, False

        self._require_ready()
        if action == "review":
            effort = payload.get("effort", "high")
            if effort not in REVIEW_EFFORTS:
                raise triad_core.TriadError("Unsupported Fable effort: %s" % effort)
            return triad_core.review_plan(str(self.store.run_dir), effort=effort), False
        if action == "dispatch":
            result = triad_core.dispatch_grok(str(self.store.run_dir))
            self._remember_worker(result)
            return result, False
        if action == "continue":
            file_name = payload.get("instructions_file")
            expected_sha256 = payload.get("instructions_sha256")
            if not isinstance(file_name, str) or not isinstance(expected_sha256, str):
                raise triad_core.TriadError("Continuation session input is incomplete.")
            instructions = read_bound_instructions(
                str(self.store.run_dir),
                file_name,
                expected_sha256,
            )
            result = triad_core.continue_grok(str(self.store.run_dir), instructions)
            self._remember_worker(result)
            return result, False
        raise triad_core.TriadError("Unknown provider session action.")

    def _remember_worker(self, result: Any) -> None:
        pid = result.get("worker_pid") if isinstance(result, dict) else None
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise triad_core.TriadError("Grok dispatch did not return a valid worker process.")
        self.worker_pids.add(pid)

    def active_worker_pids(self) -> Set[int]:
        try:
            current = self.store.read().get("worker_pid")
        except triad_core.TriadError:
            current = None
        active = {
            pid
            for pid in self.worker_pids
            if pid == current and triad_session.pid_is_alive(pid)
        }
        self.worker_pids.intersection_update(active)
        return active

    def terminate_active_workers(self) -> None:
        active = self.active_worker_pids()
        for pid in active:
            _signal_worker_group(pid, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while active and time.monotonic() < deadline:
            time.sleep(0.05)
            active = {pid for pid in active if triad_session.pid_is_alive(pid)}
        if hasattr(signal, "SIGKILL"):
            for pid in active:
                _signal_worker_group(pid, signal.SIGKILL)
        self.worker_pids.clear()


def _read_session_input(source: IO[str], messages: "queue.Queue[Any]") -> None:
    try:
        while True:
            line = source.readline(triad_session.MAX_SESSION_LINE_CHARS + 2)
            if line == "":
                break
            if len(line) > triad_session.MAX_SESSION_LINE_CHARS:
                while line and not line.endswith("\n"):
                    line = source.readline(triad_session.MAX_SESSION_LINE_CHARS + 2)
                messages.put(_LINE_TOO_LONG)
            else:
                messages.put(line)
    finally:
        messages.put(_EOF)


def _restore_environment(previous: Dict[str, Optional[str]]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def serve_session(
    run_dir: str,
    *,
    input_stream: IO[str] = sys.stdin,
    output_stream: IO[str] = sys.stdout,
    idle_timeout_seconds: int = triad_session.SESSION_IDLE_TIMEOUT_SECONDS,
    hard_timeout_seconds: int = triad_session.SESSION_HARD_TIMEOUT_SECONDS,
) -> int:
    store, state, project = triad_session.validate_run_binding(run_dir)
    state = triad_session.recover_orphaned_worker(store, state)
    if state.get("state") not in ALLOWED_START_STATES:
        raise triad_core.TriadError("Record a canonical plan before starting the provider session.")
    if idle_timeout_seconds <= 0 or hard_timeout_seconds <= idle_timeout_seconds:
        raise triad_core.TriadError("Provider session timeout configuration is invalid.")

    lease = triad_session.ProviderLease(store).acquire()
    controller = ProviderSession(store, project)
    session_environment = lease.environment()
    previous_environment = {name: os.environ.get(name) for name in session_environment}
    os.environ.update(session_environment)
    messages: "queue.Queue[Any]" = queue.Queue()
    heartbeat_errors: "queue.Queue[triad_core.TriadError]" = queue.Queue(maxsize=1)
    heartbeat_stop = threading.Event()

    def keep_lease_current() -> None:
        while not heartbeat_stop.wait(triad_session.LEASE_HEARTBEAT_SECONDS):
            try:
                lease.heartbeat()
            except triad_core.TriadError as exc:
                try:
                    heartbeat_errors.put_nowait(exc)
                except queue.Full:
                    pass
                return

    heartbeat = threading.Thread(
        target=keep_lease_current,
        name="triad-provider-lease-heartbeat",
        daemon=True,
    )
    heartbeat.start()
    reader = threading.Thread(
        target=_read_session_input,
        args=(input_stream, messages),
        name="triad-provider-input",
        daemon=True,
    )
    reader.start()

    started = time.monotonic()
    hard_deadline = started + hard_timeout_seconds
    idle_deadline = started + idle_timeout_seconds
    protocol_errors = 0
    try:
        _send(
            output_stream,
            {
                "ok": True,
                "event": "session_ready",
                "run_id": state["run_id"],
                "project_root": str(project),
                "idle_timeout_seconds": idle_timeout_seconds,
                "hard_timeout_seconds": hard_timeout_seconds,
            },
        )
        while True:
            now = time.monotonic()
            try:
                heartbeat_error = heartbeat_errors.get_nowait()
            except queue.Empty:
                heartbeat_error = None
            if heartbeat_error is not None:
                _send(output_stream, {"ok": False, "event": "session_failed", "error": str(heartbeat_error)})
                break
            if now >= hard_deadline:
                _send(output_stream, {"ok": True, "event": "session_closed", "reason": "hard_timeout"})
                break
            if controller.active_worker_pids():
                idle_deadline = now + idle_timeout_seconds
            elif now >= idle_deadline:
                _send(output_stream, {"ok": True, "event": "session_closed", "reason": "idle_timeout"})
                break
            wait_for = min(1.0, max(0.01, hard_deadline - now), max(0.01, idle_deadline - now))
            try:
                item = messages.get(timeout=wait_for)
            except queue.Empty:
                continue
            if item is _EOF:
                _send(output_stream, {"ok": True, "event": "session_closed", "reason": "stdin_closed"})
                break
            if item is _LINE_TOO_LONG:
                protocol_errors += 1
                _send(output_stream, {"ok": False, "event": "protocol_error", "error": "Session input is too large."})
            else:
                try:
                    payload = json.loads(item)
                    response, should_close = controller.handle(payload)
                except (json.JSONDecodeError, triad_core.TriadError) as exc:
                    protocol_errors += 1
                    _send(output_stream, {"ok": False, "event": "protocol_error", "error": str(exc)})
                else:
                    protocol_errors = 0
                    idle_deadline = time.monotonic() + idle_timeout_seconds
                    _send(output_stream, response)
                    if should_close:
                        break
            if protocol_errors >= MAX_PROTOCOL_ERRORS:
                _send(output_stream, {"ok": True, "event": "session_closed", "reason": "protocol_limit"})
                break
    finally:
        controller.terminate_active_workers()
        heartbeat_stop.set()
        heartbeat.join(timeout=triad_session.LEASE_HEARTBEAT_SECONDS + 1)
        lease.close()
        _restore_environment(previous_environment)
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return serve_session(args.run)
    except (triad_core.TriadError, OSError, UnicodeDecodeError) as exc:
        _send(sys.stdout, {"ok": False, "event": "session_failed", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
