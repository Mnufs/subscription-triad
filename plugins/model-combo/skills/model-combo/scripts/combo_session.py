#!/usr/bin/env python3
"""Run-scoped lease helpers for the Model Combo provider session."""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import time
from typing import Any, Dict, Optional, Tuple

import combo_core


SESSION_IDLE_TIMEOUT_SECONDS = 1_800
SESSION_HARD_TIMEOUT_SECONDS = 14_400
LEASE_TTL_SECONDS = 15
LEASE_HEARTBEAT_SECONDS = 3
MAX_SESSION_LINE_CHARS = 16_384
LEASE_FILE_NAME = ".provider-session.json"
LEASE_PATH_ENV = "COMBO_PROVIDER_SESSION_LEASE"
LEASE_TOKEN_ENV = "COMBO_PROVIDER_SESSION_TOKEN"


def pid_is_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def validate_run_binding(
    run_dir: str,
) -> Tuple[combo_core.RunStore, Dict[str, Any], Path]:
    store = combo_core.RunStore(Path(run_dir))
    state = store.read()
    project_value = state.get("project_root")
    if not isinstance(project_value, str):
        raise combo_core.ComboError("Run state is missing its target project.")
    project = Path(project_value).expanduser().resolve()
    if not project.is_dir():
        raise combo_core.ComboError("The run's target project is unavailable.")
    expected_runs_root = (project / ".model-combo" / "runs").resolve()
    if store.run_dir.parent != expected_runs_root:
        raise combo_core.ComboError("Provider session run is outside its target project.")
    run_id = state.get("run_id")
    if not isinstance(run_id, str) or run_id != store.run_dir.name or not combo_core.RUN_ID_RE.match(run_id):
        raise combo_core.ComboError("Provider session run identity is invalid.")
    return store, state, project


def recover_orphaned_worker(
    store: combo_core.RunStore,
    state: Dict[str, Any],
) -> Dict[str, Any]:
    if state.get("state") not in {"dispatched", "executing"}:
        return state
    if pid_is_alive(state.get("worker_pid")):
        return state

    def recover(current: Dict[str, Any]) -> None:
        if current.get("state") not in {"dispatched", "executing"}:
            return
        if pid_is_alive(current.get("worker_pid")):
            return
        current["state"] = "execution_failed"
        current["worker_pid"] = None
        current["executor_error"] = "The previous provider session ended before Grok completed."
        store.event(current, "orphaned_grok_worker_recovered")

    return store.mutate(recover)


def _read_lease(path: Path) -> Dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise combo_core.ComboError("Provider session lease is unavailable.") from exc
    mode = stat.S_IMODE(info.st_mode)
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or mode & 0o077
        or info.st_size > 4_096
    ):
        raise combo_core.ComboError("Provider session lease is not a private regular file.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise combo_core.ComboError("Provider session lease is unreadable.") from exc
    if not isinstance(payload, dict):
        raise combo_core.ComboError("Provider session lease has an invalid format.")
    return payload


def lease_is_current(path: Path, token: str, *, now: Optional[float] = None) -> bool:
    try:
        payload = _read_lease(path)
    except combo_core.ComboError:
        return False
    stored_token = payload.get("token")
    if not isinstance(stored_token, str) or not hmac.compare_digest(stored_token, token):
        return False
    try:
        heartbeat_at = path.stat().st_mtime
    except OSError:
        return False
    current_time = time.time() if now is None else now
    return heartbeat_at + LEASE_TTL_SECONDS > current_time and pid_is_alive(payload.get("pid"))


def _existing_lease_is_active(path: Path, *, now: Optional[float] = None) -> bool:
    payload = _read_lease(path)
    try:
        heartbeat_at = path.stat().st_mtime
    except OSError:
        return False
    current_time = time.time() if now is None else now
    return heartbeat_at + LEASE_TTL_SECONDS > current_time and pid_is_alive(payload.get("pid"))


class ProviderLease:
    """Exclusive private lease that bounds one provider process to one run."""

    def __init__(self, store: combo_core.RunStore):
        self.store = store
        self.path = store.run_dir / LEASE_FILE_NAME
        self.token = secrets.token_hex(32)
        self._fd: Optional[int] = None

    def acquire(self) -> "ProviderLease":
        for _attempt in range(2):
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                if _existing_lease_is_active(self.path):
                    raise combo_core.ComboError("A provider session is already active for this feature.")
                try:
                    self.path.unlink()
                except OSError as exc:
                    raise combo_core.ComboError("A stale provider session lease could not be removed.") from exc
                continue
            except OSError as exc:
                raise combo_core.ComboError("Provider session lease could not be created.") from exc
            try:
                self._write_initial_record()
                self.heartbeat()
            except combo_core.ComboError:
                self.close()
                raise
            return self
        raise combo_core.ComboError("Provider session lease could not be acquired.")

    def _assert_owned(self) -> None:
        if self._fd is None:
            raise combo_core.ComboError("Provider session lease is not active.")
        try:
            path_info = self.path.lstat()
            fd_info = os.fstat(self._fd)
        except OSError as exc:
            raise combo_core.ComboError("Provider session lease ownership could not be verified.") from exc
        if (
            not stat.S_ISREG(path_info.st_mode)
            or stat.S_ISLNK(path_info.st_mode)
            or path_info.st_nlink != 1
            or path_info.st_dev != fd_info.st_dev
            or path_info.st_ino != fd_info.st_ino
        ):
            raise combo_core.ComboError("Provider session lease changed unexpectedly.")

    def _write_initial_record(self) -> None:
        self._assert_owned()
        assert self._fd is not None
        payload = {
            "schema_version": 1,
            "run_id": self.store.run_dir.name,
            "pid": os.getpid(),
            "token": self.token,
        }
        data = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.ftruncate(self._fd, 0)
            written = 0
            while written < len(data):
                count = os.write(self._fd, data[written:])
                if count <= 0:
                    raise OSError("short lease write")
                written += count
            os.fchmod(self._fd, 0o600)
            os.fsync(self._fd)
        except OSError as exc:
            raise combo_core.ComboError("Provider session lease could not be initialized.") from exc

    def heartbeat(self) -> None:
        self._assert_owned()
        assert self._fd is not None
        try:
            os.utime(self._fd, None)
        except OSError as exc:
            raise combo_core.ComboError("Provider session lease heartbeat failed.") from exc

    def environment(self) -> Dict[str, str]:
        return {
            LEASE_PATH_ENV: str(self.path),
            LEASE_TOKEN_ENV: self.token,
        }

    def close(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            try:
                path_info = self.path.lstat()
                fd_info = os.fstat(fd)
                same_file = path_info.st_dev == fd_info.st_dev and path_info.st_ino == fd_info.st_ino
            except OSError:
                same_file = False
            if same_file:
                try:
                    payload = _read_lease(self.path)
                except combo_core.ComboError:
                    payload = {}
                stored = payload.get("token")
                if isinstance(stored, str) and hmac.compare_digest(stored, self.token):
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
        finally:
            os.close(fd)
