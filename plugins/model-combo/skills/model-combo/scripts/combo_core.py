#!/usr/bin/env python3
"""Core state machine for Model Combo.

The module intentionally uses only the Python standard library. Provider calls
go through the vendors' official CLIs with subscription authentication; API key
and endpoint override variables are removed from every provider subprocess.

The fail-closed Claude CLI authentication and no-tools review approach is
adapted from Cjbuilds/Codex-Orchestration under the MIT License. See the
repository's THIRD_PARTY_NOTICES.md for attribution.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
import uuid


SCHEMA_VERSION = 1
FABLE_MODEL = "claude-fable-5"
FABLE_HELPER_MODELS = frozenset({"claude-haiku-4-5-20251001"})
GROK_MODEL_PREFERENCES = ("grok-4.5", "grok-build")
GROK_MODEL = GROK_MODEL_PREFERENCES[0]
DEFAULT_EFFORT = "high"
MAX_REVIEWS = 5
MAX_TEXT_CHARS = 200_000
CLAUDE_TIMEOUT_SECONDS = 600
GROK_TIMEOUT_SECONDS = 7_200
AUTH_TIMEOUT_SECONDS = 30
EMBEDDED_TRANSPORT_DB = "messages.sqlite3"

SENSITIVE_PROVIDER_ENV = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "XAI_API_KEY",
        "XAI_API_BASE_URL",
        "GROK_WS_URL",
        "GROK_WS_ORIGIN",
        "CLI_CHAT_PROXY_BASE_URL",
    }
)

ALL_API_KEY_ENV = frozenset(set(SENSITIVE_PROVIDER_ENV) | {"OPENAI_API_KEY"})

FABLE_REVIEW_SYSTEM_PROMPT = """You are Claude Fable 5 acting only as an independent plan reviewer for Codex's root orchestrator.
Review the supplied self-contained packet for material correctness, missing requirements, unsafe sequencing, ownership conflicts, compatibility risks, and verification gaps. Do not edit files, call tools, spawn agents, contact the executor, or implement anything.

Your first non-empty line must be exactly PLAN_APPROVED or PLAN_REVISE.
Use PLAN_APPROVED only when no material gap remains. Use PLAN_REVISE when correction is required. For PLAN_REVISE, assign each material finding a stable ID such as F-001 and give a concrete correction. Ignore style-only preferences. Report only to the root orchestrator."""

RUN_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class ComboError(RuntimeError):
    """Fail-closed error for an invalid transition or provider operation."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_text(name: str, value: Any, *, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ComboError("%s must be a non-empty string." % name)
    if len(value) > limit:
        raise ComboError("%s exceeds the %d-character limit." % (name, limit))
    return value.strip() + "\n"


def sanitized_provider_environment(source: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(source if source is not None else os.environ)
    for name in SENSITIVE_PROVIDER_ENV:
        env.pop(name, None)
    # Grok Build exposes this documented login-policy environment knob through
    # `grok inspect --json`. Force it for every child, even when the parent had
    # an explicit false value, so API-key authentication is rejected by the CLI.
    env["GROK_DISABLE_API_KEY_AUTH"] = "1"
    return env


def present_api_environment(source: Optional[Dict[str, str]] = None) -> List[str]:
    env = source if source is not None else os.environ
    return sorted(name for name in ALL_API_KEY_ENV if env.get(name))


def _resolve_binary(name: str, candidates: Iterable[Path]) -> Path:
    found = shutil.which(name)
    if found:
        return Path(found).expanduser().resolve()
    for candidate in candidates:
        path = candidate.expanduser()
        if path.is_file() and os.access(str(path), os.X_OK):
            return path.resolve()
    raise ComboError("%s CLI is not installed or is not on PATH." % name)


def resolve_claude() -> Path:
    return _resolve_binary(
        "claude",
        (
            Path("~/.local/bin/claude"),
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
        ),
    )


def resolve_grok() -> Path:
    return _resolve_binary(
        "grok",
        (
            Path("~/.grok/bin/grok"),
            Path("/usr/local/bin/grok"),
            Path("/opt/homebrew/bin/grok"),
        ),
    )


def _run(
    command: Sequence[str],
    *,
    timeout: int,
    cwd: Optional[Path] = None,
    input_text: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd) if cwd else None,
            env=env,
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ComboError("Command timed out: %s" % Path(command[0]).name) from exc
    except OSError as exc:
        raise ComboError("Could not start command: %s" % Path(command[0]).name) from exc


def check_claude_subscription() -> Dict[str, Any]:
    claude = resolve_claude()
    result = _run(
        [str(claude), "auth", "status"],
        timeout=AUTH_TIMEOUT_SECONDS,
        env=sanitized_provider_environment(),
    )
    if result.returncode != 0:
        raise ComboError("Claude authentication check failed; run `claude auth login`.")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ComboError("Claude authentication status was not valid JSON.") from exc
    subscription = payload.get("subscriptionType") if isinstance(payload, dict) else None
    if not (
        isinstance(payload, dict)
        and payload.get("loggedIn") is True
        and payload.get("authMethod") == "claude.ai"
        and payload.get("apiProvider") == "firstParty"
        and subscription in {"pro", "max"}
    ):
        raise ComboError(
            "Claude must use a first-party Pro or Max subscription login; run `claude auth login`."
        )
    return {
        "available": True,
        "binary": str(claude),
        "auth_method": "claude.ai",
        "api_provider": "firstParty",
        "subscription": subscription,
    }


_GROK_AUTH_FAILURES = (
    "you are not authenticated",
    "no auth credentials",
    "re-authentication required",
    "token expired",
)

_GROK_NETWORK_FAILURES = (
    "failed to fetch models",
    "settings fetch network error",
    "settings fetch failed after",
    "tcp connect error",
)


def check_grok_subscription(project_root: Optional[Path] = None) -> Dict[str, Any]:
    grok = resolve_grok()
    env = sanitized_provider_environment()
    try:
        inspection = _run(
            [str(grok), "inspect", "--json"],
            timeout=AUTH_TIMEOUT_SECONDS,
            cwd=project_root,
            env=env,
        )
    except ComboError as exc:
        raise ComboError("Grok Build configuration inspection failed: %s" % exc) from exc
    try:
        inspection_payload = json.loads(inspection.stdout)
    except json.JSONDecodeError as exc:
        raise ComboError("Grok Build login policy could not be inspected.") from exc
    login_policy = inspection_payload.get("loginPolicy") if isinstance(inspection_payload, dict) else None
    if not isinstance(login_policy, dict) or login_policy.get("apiKeyAuthDisabled") is not True:
        raise ComboError("Grok Build did not confirm that API-key authentication is disabled.")
    try:
        result = _run(
            [str(grok), "--oauth", "models"],
            timeout=AUTH_TIMEOUT_SECONDS,
            cwd=Path(__file__).resolve().parent,
            env=env,
        )
    except ComboError as exc:
        raise ComboError("Grok Build model availability check failed: %s" % exc) from exc
    combined = (result.stdout + "\n" + result.stderr).lower()
    if any(marker in combined for marker in _GROK_AUTH_FAILURES):
        raise ComboError("Grok Build OAuth is unavailable; run `grok login --oauth`.")
    if any(marker in combined for marker in _GROK_NETWORK_FAILURES):
        raise ComboError(
            "Grok Build could not refresh models from the approved provider session. Check the host "
            "proxy or connectivity for `cli-chat-proxy.grok.com` and `auth.x.ai`; do not enable "
            "persistent Codex network settings."
        )
    if result.returncode != 0:
        raise ComboError("Grok Build OAuth model check failed.")
    selected_model = next((model for model in GROK_MODEL_PREFERENCES if model in combined), None)
    if selected_model is None:
        raise ComboError(
            "Grok Build did not advertise a supported model (%s)."
            % ", ".join(GROK_MODEL_PREFERENCES)
        )
    return {
        "available": True,
        "binary": str(grok),
        "auth_mode": "oauth-forced",
        "api_key_auth_disabled": True,
        "model": selected_model,
    }


def _valid_agmsg_root(root: Path) -> bool:
    return all((root / "scripts" / name).is_file() for name in ("api.sh", "join.sh", "send.sh"))


def find_agmsg_root(source: Optional[Dict[str, str]] = None) -> Optional[Path]:
    env = source if source is not None else os.environ
    candidates: List[Path] = []
    explicit = env.get("AGMSG_SKILL_DIR")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(Path.home() / ".agents" / "skills" / "agmsg")
    skills_root = Path.home() / ".agents" / "skills"
    if skills_root.is_dir():
        candidates.extend(sorted(skills_root.glob("*/")))
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if _valid_agmsg_root(resolved):
            return resolved
    return None


def doctor(project_root: Optional[str] = None) -> Dict[str, Any]:
    project = Path(project_root).expanduser().resolve() if project_root else None
    report: Dict[str, Any] = {
        "subscription_only": True,
        "provider_subprocesses_strip_api_environment": True,
        "api_environment_present_in_parent": present_api_environment(),
    }
    try:
        report["claude"] = check_claude_subscription()
    except ComboError as exc:
        report["claude"] = {"available": False, "error": str(exc)}
    try:
        report["grok"] = check_grok_subscription(project)
    except ComboError as exc:
        report["grok"] = {"available": False, "error": str(exc)}
    agmsg = find_agmsg_root()
    report["agmsg"] = (
        {"available": True, "mode": "external", "root": str(agmsg)}
        if agmsg
        else {
            "available": True,
            "mode": "embedded",
            "root": None,
            "detail": "Using the built-in local lifecycle transport; no separate agmsg install is required.",
        }
    )
    report["ready"] = all(report[name].get("available") is True for name in ("claude", "grok", "agmsg"))
    return report


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(path))
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def _directory_lock(run_dir: Path, timeout: float = 10.0) -> Iterator[None]:
    lock_dir = run_dir / ".state.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_dir.mkdir(mode=0o700)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ComboError("Timed out waiting for the run state lock.")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock_dir.rmdir()


def _read_regular_json(path: Path) -> Dict[str, Any]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ComboError("Run state must be a single regular file.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ComboError("Run state does not exist: %s" % path) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComboError("Run state is unreadable or malformed.") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ComboError("Run state schema is unsupported.")
    return payload


def _ensure_within(child: Path, parent: Path) -> None:
    try:
        child.relative_to(parent)
    except ValueError as exc:
        raise ComboError("Resolved run path escapes the project root.") from exc


class RunStore:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir.expanduser().resolve()
        self.state_path = self.run_dir / "state.json"
        state = _read_regular_json(self.state_path)
        project_root = Path(state.get("project_root", "")).expanduser().resolve()
        run_id = state.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise ComboError("Run id is invalid.")
        _ensure_within(self.run_dir, project_root)
        expected = (project_root / ".model-combo" / "runs" / run_id).resolve()
        if expected != self.run_dir:
            raise ComboError("Run directory does not match its recorded project and id.")

    def read(self) -> Dict[str, Any]:
        return _read_regular_json(self.state_path)

    def mutate(self, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
        with _directory_lock(self.run_dir):
            state = self.read()
            change(state)
            state["updated_at"] = utc_now()
            _atomic_write_json(self.state_path, state)
            return state

    def event(self, state: Dict[str, Any], name: str, **fields: Any) -> None:
        events = state.setdefault("events", [])
        if not isinstance(events, list):
            events = []
            state["events"] = events
        item = {"at": utc_now(), "event": name}
        item.update(fields)
        events.append(item)
        del events[:-100]


def create_run(
    project_root: str,
    task: str,
    acceptance_criteria: str,
    context: str = "No additional context supplied.",
    *,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    project = Path(project_root).expanduser().resolve()
    if not project.is_dir():
        raise ComboError("Project root must be an existing directory.")
    task_text = require_text("task", task)
    acceptance_text = require_text("acceptance_criteria", acceptance_criteria)
    context_text = require_text("context", context)
    selected_id = run_id or str(uuid.uuid4())
    if not RUN_ID_RE.match(selected_id):
        raise ComboError("run_id must be a canonical UUID.")
    runs_root = project / ".model-combo" / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    resolved_runs_root = runs_root.resolve()
    _ensure_within(resolved_runs_root, project)
    run_dir = resolved_runs_root / selected_id
    try:
        run_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ComboError("Run already exists: %s" % selected_id) from exc

    created = utc_now()
    project_hash = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:12]
    state: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": selected_id,
        "project_root": str(project),
        "state": "created",
        "plan_version": 0,
        "plan_sha256": None,
        "approved_plan_sha256": None,
        "review_count": 0,
        "max_reviews": MAX_REVIEWS,
        "last_review_decision": None,
        "team": "model-combo-%s" % project_hash,
        "orchestrator_role": "codex-orchestrator",
        "executor_role": "grok-%s" % selected_id.split("-")[0],
        "grok_session_id": str(uuid.uuid4()),
        "execution_round": 0,
        "worker_pid": None,
        "created_at": created,
        "updated_at": created,
        "events": [{"at": created, "event": "run_created"}],
    }
    _atomic_write_text(run_dir / "task.md", "# Task\n\n" + task_text)
    _atomic_write_text(run_dir / "acceptance.md", "# Acceptance criteria\n\n" + acceptance_text)
    _atomic_write_text(run_dir / "context.md", "# Repository context\n\n" + context_text)
    _atomic_write_json(run_dir / "state.json", state)
    return {"run_dir": str(run_dir), "state": state}


def record_plan(run_dir: str, plan: str) -> Dict[str, Any]:
    store = RunStore(Path(run_dir))
    plan_text = require_text("plan", plan)
    plan_hash = sha256_text(plan_text)

    def change(state: Dict[str, Any]) -> None:
        if state.get("state") in {"dispatched", "executing", "executed", "verification_failed", "complete", "execution_failed"}:
            raise ComboError("The plan cannot change after Grok execution starts; create a new run for new scope.")
        if state.get("plan_sha256") == plan_hash:
            return
        version = int(state.get("plan_version", 0)) + 1
        _atomic_write_text(store.run_dir / ("plan-v%d.md" % version), plan_text)
        _atomic_write_text(store.run_dir / "plan.md", plan_text)
        state["plan_version"] = version
        state["plan_sha256"] = plan_hash
        state["approved_plan_sha256"] = None
        state["last_review_decision"] = None
        state["state"] = "planned"
        store.event(state, "plan_recorded", version=version, plan_sha256=plan_hash)

    state = store.mutate(change)
    return {"run_dir": str(store.run_dir), "state": state}


def _first_non_empty_line(value: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), "")


def build_review_packet(store: RunStore, state: Dict[str, Any]) -> str:
    parts = [
        "# ORIGINAL_TASK\n" + (store.run_dir / "task.md").read_text(encoding="utf-8"),
        "# ACCEPTANCE_CRITERIA\n" + (store.run_dir / "acceptance.md").read_text(encoding="utf-8"),
        "# REPOSITORY_CONTEXT\n" + (store.run_dir / "context.md").read_text(encoding="utf-8"),
        "# CANONICAL_PLAN\nPlan version: %s\nPlan SHA-256: %s\n\n%s"
        % (
            state.get("plan_version"),
            state.get("plan_sha256"),
            (store.run_dir / "plan.md").read_text(encoding="utf-8"),
        ),
    ]
    packet = "\n\n".join(parts)
    if len(packet) > MAX_TEXT_CHARS:
        raise ComboError("Combined Fable review packet is too large.")
    return packet


def invoke_fable_review(packet: str, effort: str = DEFAULT_EFFORT) -> Dict[str, Any]:
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ComboError("Unsupported Fable effort: %s" % effort)
    auth = check_claude_subscription()
    claude = Path(auth["binary"])
    command = [
        str(claude),
        "-p",
        "--model",
        FABLE_MODEL,
        "--effort",
        effort,
        "--safe-mode",
        "--tools",
        "",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--prompt-suggestions",
        "false",
        "--output-format",
        "json",
        "--system-prompt",
        FABLE_REVIEW_SYSTEM_PROMPT,
    ]
    result = _run(
        command,
        timeout=CLAUDE_TIMEOUT_SECONDS,
        input_text=packet,
        env=sanitized_provider_environment(),
    )
    if result.returncode != 0:
        raise ComboError("Claude Fable review failed with exit code %d; output withheld." % result.returncode)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ComboError("Claude Fable returned malformed JSON.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), str):
        raise ComboError("Claude Fable returned an unexpected result.")
    usage = payload.get("modelUsage")
    used_models = sorted(usage) if isinstance(usage, dict) and all(isinstance(key, str) for key in usage) else []
    if FABLE_MODEL not in used_models:
        raise ComboError("Runtime metadata did not confirm the pinned Claude Fable 5 model.")
    unknown = set(used_models) - {FABLE_MODEL} - set(FABLE_HELPER_MODELS)
    if unknown:
        raise ComboError("Runtime metadata reported an unapproved helper model.")
    review = payload["result"].strip()
    decision = _first_non_empty_line(review)
    if decision not in {"PLAN_APPROVED", "PLAN_REVISE"}:
        raise ComboError("Claude Fable omitted the required plan decision.")
    return {
        "decision": decision,
        "review": review + "\n",
        "model": FABLE_MODEL,
        "effort": effort,
        "used_models": used_models,
        "auth_method": auth["auth_method"],
    }


def review_plan(
    run_dir: str,
    *,
    effort: str = DEFAULT_EFFORT,
    invoker: Callable[[str, str], Dict[str, Any]] = invoke_fable_review,
) -> Dict[str, Any]:
    store = RunStore(Path(run_dir))
    before = store.read()
    if before.get("state") != "planned":
        raise ComboError("A current, unreviewed plan is required before Fable review.")
    if int(before.get("review_count", 0)) >= int(before.get("max_reviews", MAX_REVIEWS)):
        raise ComboError("The five-review safety limit has been reached; execution remains blocked.")
    plan_hash = before.get("plan_sha256")
    review_number = int(before.get("review_count", 0)) + 1
    packet = build_review_packet(store, before)
    result = invoker(packet, effort)
    decision = result.get("decision")
    review_text = require_text("review", result.get("review"))
    if decision not in {"PLAN_APPROVED", "PLAN_REVISE"}:
        raise ComboError("Reviewer returned an invalid decision.")

    def change(state: Dict[str, Any]) -> None:
        if state.get("state") != "planned" or state.get("plan_sha256") != plan_hash:
            raise ComboError("The plan changed while Fable was reviewing it; review the new version.")
        if int(state.get("review_count", 0)) + 1 != review_number:
            raise ComboError("Another review completed concurrently; reload the run state.")
        _atomic_write_text(store.run_dir / ("review-v%d.md" % review_number), review_text)
        _atomic_write_text(store.run_dir / "review.md", review_text)
        state["review_count"] = review_number
        state["last_review_decision"] = decision
        state["review_model"] = result.get("model", FABLE_MODEL)
        state["review_effort"] = result.get("effort", effort)
        if decision == "PLAN_APPROVED":
            state["approved_plan_sha256"] = plan_hash
            state["state"] = "approved"
        else:
            state["approved_plan_sha256"] = None
            state["state"] = "review_revise"
        store.event(state, "plan_reviewed", decision=decision, review_number=review_number, plan_sha256=plan_hash)

    state = store.mutate(change)
    return {"run_dir": str(store.run_dir), "decision": decision, "review": review_text, "state": state}


def _run_agmsg(script_root: Path, name: str, args: Sequence[str], *, timeout: int = 30) -> subprocess.CompletedProcess:
    script = script_root / "scripts" / name
    result = _run(
        ["bash", str(script)] + list(args),
        timeout=timeout,
        env=sanitized_provider_environment(),
    )
    if result.returncode != 0:
        raise ComboError("agmsg %s failed with exit code %d." % (name, result.returncode))
    return result


def register_agmsg_roles(state: Dict[str, Any], agmsg_root: Path) -> None:
    project = state["project_root"]
    team = state["team"]
    _run_agmsg(agmsg_root, "join.sh", [team, state["orchestrator_role"], "codex", project])
    _run_agmsg(agmsg_root, "join.sh", [team, state["executor_role"], "grok-build", project])


def _embedded_transport_path(state: Dict[str, Any]) -> Path:
    project = Path(state["project_root"]).expanduser().resolve()
    path = project / ".model-combo" / "transport" / EMBEDDED_TRANSPORT_DB
    _ensure_within(path, project)
    return path


@contextlib.contextmanager
def _embedded_transport(state: Dict[str, Any]) -> Iterator[sqlite3.Connection]:
    path = _embedded_transport_path(state)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(str(path), timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS lifecycle_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team TEXT NOT NULL,
                from_agent TEXT NOT NULL,
                to_agent TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        yield connection
        connection.commit()
    except sqlite3.Error as exc:
        raise ComboError("Embedded lifecycle transport failed.") from exc
    finally:
        if connection is not None:
            connection.close()


def _embedded_transport_send(state: Dict[str, Any], body: str) -> str:
    with _embedded_transport(state) as connection:
        cursor = connection.execute(
            """
            INSERT INTO lifecycle_messages(team, from_agent, to_agent, body, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                state["team"],
                state["executor_role"],
                state["orchestrator_role"],
                body,
                utc_now(),
            ),
        )
        return str(cursor.lastrowid)


def _embedded_transport_messages(state: Dict[str, Any], limit: int = 20) -> List[Dict[str, Any]]:
    path = _embedded_transport_path(state)
    if not path.is_file():
        return []
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)
        rows = connection.execute(
            """
            SELECT id, team, from_agent, to_agent, body, created_at
            FROM lifecycle_messages
            WHERE team = ? AND (from_agent = ? OR to_agent = ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (state["team"], state["orchestrator_role"], state["orchestrator_role"], limit),
        ).fetchall()
    except sqlite3.Error as exc:
        raise ComboError("Embedded lifecycle transport could not be read.") from exc
    finally:
        with contextlib.suppress(UnboundLocalError):
            connection.close()
    return [
        {
            "type": "message_sent",
            "id": str(row[0]),
            "team": row[1],
            "from": row[2],
            "to": row[3],
            "body": row[4],
            "at": row[5],
        }
        for row in reversed(rows)
    ]


def build_handoff(store: RunStore, state: Dict[str, Any]) -> str:
    result_path = store.run_dir / "executor-response.json"
    return """# Model Combo execution handoff

Run ID: {run_id}
Approved plan SHA-256: {plan_hash}
Project root: {project_root}

Read and obey the complete task, context, acceptance criteria, and approved plan below. Work only inside the approved scope. Preserve unrelated user changes. Implement the feature, run the most relevant tests, and report exact files changed, checks run, failures, and remaining risks. Do not spawn subagents. Do not use or request API keys. Do not alter this run's plan or approval artifacts.

Write your final structured response to stdout; Model Combo stores it at:
{result_path}

## Task

{task}

## Acceptance criteria

{acceptance}

## Repository context

{context}

## Approved plan

{plan}
""".format(
        run_id=state["run_id"],
        plan_hash=state["approved_plan_sha256"],
        project_root=state["project_root"],
        result_path=result_path,
        task=(store.run_dir / "task.md").read_text(encoding="utf-8"),
        acceptance=(store.run_dir / "acceptance.md").read_text(encoding="utf-8"),
        context=(store.run_dir / "context.md").read_text(encoding="utf-8"),
        plan=(store.run_dir / "plan.md").read_text(encoding="utf-8"),
    )


def _start_worker(store: RunStore, agmsg_root: Optional[Path], mode: str) -> int:
    worker = Path(__file__).with_name("combo_worker.py")
    log_path = store.run_dir / ("worker-%s.log" % mode)
    command = [sys.executable, str(worker), "--run", str(store.run_dir), "--mode", mode]
    if agmsg_root is not None:
        command.extend(["--agmsg-root", str(agmsg_root)])
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                command,
                cwd=str(store.run_dir),
                env=sanitized_provider_environment(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as exc:
        raise ComboError("Could not start the detached Grok worker.") from exc
    return process.pid


def dispatch_grok(run_dir: str) -> Dict[str, Any]:
    store = RunStore(Path(run_dir))
    state = store.read()
    if state.get("state") != "approved":
        raise ComboError("Grok dispatch requires an explicitly approved plan.")
    if state.get("approved_plan_sha256") != state.get("plan_sha256"):
        raise ComboError("The approved plan hash is stale; review the current plan again.")
    grok_status = check_grok_subscription(Path(state["project_root"]))
    agmsg_root = find_agmsg_root()
    if agmsg_root:
        register_agmsg_roles(state, agmsg_root)
    handoff = build_handoff(store, state)
    _atomic_write_text(store.run_dir / "handoff.md", handoff)

    def mark_dispatched(current: Dict[str, Any]) -> None:
        if current.get("state") != "approved" or current.get("approved_plan_sha256") != current.get("plan_sha256"):
            raise ComboError("Approval changed before dispatch.")
        current["state"] = "dispatched"
        current["execution_round"] = int(current.get("execution_round", 0)) + 1
        current["grok_model"] = grok_status["model"]
        current["worker_pid"] = None
        store.event(current, "grok_dispatched", execution_round=current["execution_round"])

    store.mutate(mark_dispatched)
    try:
        pid = _start_worker(store, agmsg_root, "initial")
    except ComboError:
        def rollback(current: Dict[str, Any]) -> None:
            if current.get("state") == "dispatched" and current.get("worker_pid") is None:
                current["state"] = "approved"
                store.event(current, "grok_dispatch_start_failed")
        store.mutate(rollback)
        raise

    def record_pid(current: Dict[str, Any]) -> None:
        if current.get("state") in {"dispatched", "executing"}:
            current["worker_pid"] = pid
            store.event(current, "grok_worker_started", pid=pid)

    final_state = store.mutate(record_pid)
    return {"run_dir": str(store.run_dir), "worker_pid": pid, "state": final_state}


def continue_grok(run_dir: str, instructions: str) -> Dict[str, Any]:
    store = RunStore(Path(run_dir))
    instruction_text = require_text("instructions", instructions)
    state = store.read()
    if state.get("state") not in {"executed", "verification_failed", "execution_failed"}:
        raise ComboError("Grok continuation is allowed only after an execution result or failed verification.")
    grok_status = check_grok_subscription(Path(state["project_root"]))
    agmsg_root = find_agmsg_root()
    if agmsg_root:
        register_agmsg_roles(state, agmsg_root)
    round_number = int(state.get("execution_round", 0)) + 1
    followup_path = store.run_dir / ("followup-v%d.md" % round_number)
    _atomic_write_text(
        followup_path,
        "# Grok execution follow-up\n\nRun ID: %s\nApproved plan SHA-256: %s\n\n%s"
        % (state["run_id"], state["approved_plan_sha256"], instruction_text),
    )

    def mark_dispatched(current: Dict[str, Any]) -> None:
        if current.get("state") not in {"executed", "verification_failed", "execution_failed"}:
            raise ComboError("Execution state changed before continuation.")
        current["state"] = "dispatched"
        current["execution_round"] = round_number
        current["grok_model"] = grok_status["model"]
        current["active_followup"] = str(followup_path)
        current["worker_pid"] = None
        store.event(current, "grok_continued", execution_round=round_number)

    store.mutate(mark_dispatched)
    try:
        pid = _start_worker(store, agmsg_root, "continue")
    except ComboError:
        def rollback(current: Dict[str, Any]) -> None:
            if current.get("state") == "dispatched" and current.get("worker_pid") is None:
                current["state"] = "verification_failed"
                store.event(current, "grok_continue_start_failed")
        store.mutate(rollback)
        raise

    def record_pid(current: Dict[str, Any]) -> None:
        if current.get("state") in {"dispatched", "executing"}:
            current["worker_pid"] = pid
            store.event(current, "grok_worker_started", pid=pid)

    final_state = store.mutate(record_pid)
    return {"run_dir": str(store.run_dir), "worker_pid": pid, "state": final_state}


def prepare_continuation_request(run_dir: str, instructions: str) -> Dict[str, str]:
    """Write one hash-bound continuation payload for the active provider session."""
    store = RunStore(Path(run_dir))
    state = store.read()
    if state.get("state") not in {"executed", "verification_failed", "execution_failed"}:
        raise ComboError("Grok continuation is allowed only after an execution result or failed verification.")
    instruction_text = require_text("instructions", instructions)
    request_dir = store.run_dir / ".provider-requests"
    request_dir.mkdir(mode=0o700, exist_ok=True)
    request_path = request_dir / ("continue-%s.md" % uuid.uuid4())
    _atomic_write_text(request_path, instruction_text)
    request_path.chmod(0o600)
    return {
        "path": str(request_path),
        "sha256": sha256_text(instruction_text),
    }


def build_grok_command(state: Dict[str, Any], store: RunStore, mode: str) -> List[str]:
    grok = resolve_grok()
    model = state.get("grok_model")
    if model not in GROK_MODEL_PREFERENCES:
        model = GROK_MODEL
    base = [
        str(grok),
        "--oauth",
        "--cwd",
        state["project_root"],
        "--sandbox",
        "workspace",
        "--model",
        model,
        "--reasoning-effort",
        DEFAULT_EFFORT,
        "--permission-mode",
        "auto",
        "--output-format",
        "json",
        "--check",
        "--no-subagents",
        "--no-memory",
    ]
    if mode == "initial":
        return base + ["--session-id", state["grok_session_id"], "--prompt-file", str(store.run_dir / "handoff.md")]
    if mode == "continue":
        followup = state.get("active_followup")
        if not isinstance(followup, str):
            raise ComboError("Continuation prompt is missing.")
        return base + ["--resume", state["grok_session_id"], "--prompt-file", followup]
    raise ComboError("Unknown worker mode: %s" % mode)


def _agmsg_send(agmsg_root: Optional[Path], state: Dict[str, Any], body: str) -> Optional[str]:
    if agmsg_root is None:
        try:
            return _embedded_transport_send(state, body)
        except ComboError as exc:
            return str(exc)
    try:
        result = _run_agmsg(
            agmsg_root,
            "send.sh",
            [state["team"], state["executor_role"], state["orchestrator_role"], body],
        )
        return result.stdout.strip()
    except ComboError as exc:
        return str(exc)


def run_grok_worker(
    run_dir: str,
    agmsg_root: Optional[str],
    mode: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = _run,
) -> Dict[str, Any]:
    store = RunStore(Path(run_dir))
    root = Path(agmsg_root).expanduser().resolve() if agmsg_root else None
    if root is not None and not _valid_agmsg_root(root):
        raise ComboError("Worker received an invalid agmsg root.")

    def mark_executing(state: Dict[str, Any]) -> None:
        if state.get("state") != "dispatched":
            raise ComboError("Worker can start only from dispatched state.")
        state["state"] = "executing"
        store.event(state, "grok_execution_started", mode=mode)

    state = store.mutate(mark_executing)
    round_number = int(state.get("execution_round", 1))
    try:
        command = build_grok_command(state, store, mode)
        result = runner(
            command,
            timeout=GROK_TIMEOUT_SECONDS,
            cwd=Path(state["project_root"]),
            env=sanitized_provider_environment(),
        )
    except ComboError as exc:
        failure_path = store.run_dir / ("executor-stderr-v%d.log" % round_number)
        _atomic_write_text(failure_path, str(exc) + "\n")

        def fail_to_start(current: Dict[str, Any]) -> None:
            current["state"] = "execution_failed"
            current["worker_pid"] = None
            current["executor_exit_code"] = None
            current["executor_error"] = str(exc)
            store.event(current, "grok_execution_failed", exit_code=None, execution_round=round_number)

        failed_state = store.mutate(fail_to_start)
        message = "COMBO_EXECUTION_FAILED %s %s" % (state["run_id"], failure_path)
        agmsg_result = _agmsg_send(root, failed_state, message)
        return {
            "succeeded": False,
            "exit_code": None,
            "response_path": None,
            "agmsg": agmsg_result,
            "state": failed_state,
        }
    response_path = store.run_dir / ("executor-response-v%d.json" % round_number)
    _atomic_write_text(response_path, result.stdout if result.stdout.endswith("\n") else result.stdout + "\n")
    _atomic_write_text(store.run_dir / "executor-response.json", result.stdout if result.stdout.endswith("\n") else result.stdout + "\n")
    if result.stderr:
        _atomic_write_text(store.run_dir / ("executor-stderr-v%d.log" % round_number), result.stderr)
    succeeded = result.returncode == 0

    def finish(current: Dict[str, Any]) -> None:
        current["state"] = "executed" if succeeded else "execution_failed"
        current["worker_pid"] = None
        current["executor_exit_code"] = result.returncode
        current["executor_response"] = str(response_path)
        store.event(
            current,
            "grok_execution_finished" if succeeded else "grok_execution_failed",
            exit_code=result.returncode,
            execution_round=round_number,
        )

    final_state = store.mutate(finish)
    signal = "COMBO_EXECUTION_DONE" if succeeded else "COMBO_EXECUTION_FAILED"
    message = "%s %s %s" % (signal, state["run_id"], response_path)
    agmsg_result = _agmsg_send(root, final_state, message)
    return {
        "succeeded": succeeded,
        "exit_code": result.returncode,
        "response_path": str(response_path),
        "agmsg": agmsg_result,
        "state": final_state,
    }


def _read_agmsg_messages(agmsg_root: Optional[Path], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    if agmsg_root is None:
        try:
            return _embedded_transport_messages(state)
        except ComboError:
            return []
    try:
        result = _run_agmsg(
            agmsg_root,
            "api.sh",
            ["get", "teams", state["team"], "messages", "--agent", state["orchestrator_role"], "--limit", "20"],
        )
    except ComboError:
        return []
    messages: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            messages.append(item)
    return messages


def run_status(run_dir: str) -> Dict[str, Any]:
    store = RunStore(Path(run_dir))
    state = store.read()
    agmsg_root = find_agmsg_root()
    messages = _read_agmsg_messages(agmsg_root, state)
    artifacts = sorted(path.name for path in store.run_dir.iterdir() if path.is_file() and not path.name.startswith("."))
    return {"run_dir": str(store.run_dir), "state": state, "messages": messages, "artifacts": artifacts}


def record_verification(run_dir: str, verdict: str, report: str) -> Dict[str, Any]:
    store = RunStore(Path(run_dir))
    if verdict not in {"pass", "fail"}:
        raise ComboError("Verification verdict must be `pass` or `fail`.")
    report_text = require_text("report", report)
    before = store.read()
    if before.get("state") not in {"executed", "execution_failed"}:
        raise ComboError("Verification requires a completed Grok execution round.")
    if verdict == "pass" and before.get("state") != "executed":
        raise ComboError("A failed Grok process cannot receive a passing verification verdict.")
    number = int(before.get("verification_count", 0)) + 1
    _atomic_write_text(store.run_dir / ("verification-v%d.md" % number), report_text)
    _atomic_write_text(store.run_dir / "verification.md", report_text)

    def change(state: Dict[str, Any]) -> None:
        if state.get("state") not in {"executed", "execution_failed"}:
            raise ComboError("Execution state changed before verification was recorded.")
        state["verification_count"] = number
        state["verification_verdict"] = verdict
        state["state"] = "complete" if verdict == "pass" else "verification_failed"
        store.event(state, "verification_recorded", verdict=verdict, verification_number=number)

    state = store.mutate(change)
    return {"run_dir": str(store.run_dir), "verdict": verdict, "state": state}
