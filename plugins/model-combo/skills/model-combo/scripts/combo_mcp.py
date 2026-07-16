#!/usr/bin/env python3
"""MCP facade for Model Combo."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set
import uuid

import combo_core
import combo_session


STRING = {"type": "string", "maxLength": combo_core.MAX_TEXT_CHARS}
PROVIDER_BRIDGE = Path(__file__).with_name("combo_provider.py").resolve()


def _provider_session_request(
    store: combo_core.RunStore,
    state: Dict[str, Any],
    project: Path,
) -> Dict[str, Any]:
    return {
        "action_required": "scoped_host_session",
        "provider_action": "session",
        "argv": [sys.executable, str(PROVIDER_BRIDGE), "session", "--run", str(store.run_dir)],
        "cwd": str(project),
        "approval_reason": (
            "Allow one temporary Model Combo feature session for run %s in this target "
            "repository. The process can only check official subscription CLIs, review this run's "
            "plans with Fable, and dispatch or resume its approved Grok execution."
            % state["run_id"]
        ),
        "approval_scope": "single_feature_session",
        "allow_persistent_rule": False,
        "changes_codex_network_defaults": False,
        "config_files_to_modify": [],
        "retain_process_session": True,
        "stdin_protocol": "utf-8-json-lines",
        "expected_ready_event": "session_ready",
        "session_idle_timeout_seconds": combo_session.SESSION_IDLE_TIMEOUT_SECONDS,
        "session_hard_timeout_seconds": combo_session.SESSION_HARD_TIMEOUT_SECONDS,
        "run_dir": str(store.run_dir),
    }


def _provider_session_input(
    action: str,
    run_dir: Path,
    values: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())
    payload: Dict[str, str] = {"action": action, "request_id": request_id}
    if values:
        payload.update(values)
    return {
        "action_required": "provider_session_input",
        "provider_action": action,
        "run_dir": str(run_dir),
        "request_id": request_id,
        "stdin": json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        "stdin_protocol": "utf-8-json-lines",
        "requires_new_host_approval": False,
        "changes_codex_network_defaults": False,
        "config_files_to_modify": [],
    }


def _run_context(run_dir: str) -> tuple[combo_core.RunStore, Dict[str, Any], Path]:
    store = combo_core.RunStore(Path(run_dir))
    state = store.read()
    project = Path(state["project_root"]).expanduser().resolve()
    return store, state, project


def _annotations(*, read_only: bool, open_world: bool = False) -> Dict[str, bool]:
    return {
        "readOnlyHint": read_only,
        "destructiveHint": False,
        "idempotentHint": read_only,
        "openWorldHint": open_world,
    }


def tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": "doctor",
            "title": "Check providers in the approved feature session",
            "description": "Prepare one JSON line that checks official Claude and Grok readiness inside the already-approved run session without a new host approval or model call.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_dir": {**STRING, "description": "Run directory returned by create_run."}},
                "required": ["run_dir"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=True),
        },
        {
            "name": "create_run",
            "title": "Create a gated orchestration run",
            "description": "Create local run state and canonical task artifacts inside the target project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_root": {**STRING, "description": "Existing project directory."},
                    "task": {**STRING, "description": "User intent and requested feature."},
                    "acceptance_criteria": {**STRING, "description": "Observable completion criteria."},
                    "context": {**STRING, "description": "Verified repository facts and constraints."},
                },
                "required": ["project_root", "task", "acceptance_criteria"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=False),
        },
        {
            "name": "record_plan",
            "title": "Record the canonical Codex plan",
            "description": "Version and hash the root Codex plan; changing it invalidates any earlier approval.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_dir": {**STRING, "description": "Run directory returned by create_run."},
                    "plan": {**STRING, "description": "Complete implementation and verification plan."},
                },
                "required": ["run_dir", "plan"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=False),
        },
        {
            "name": "start_provider_session",
            "title": "Start one bounded provider session for this feature",
            "description": "Prepare the only host-approved command for this feature. Keep its process session open and send later provider inputs through the same stdin channel.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_dir": {**STRING, "description": "Planned run directory."}},
                "required": ["run_dir"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=True),
        },
        {
            "name": "review_plan",
            "title": "Review the plan in the approved feature session",
            "description": "Prepare one JSON line for a no-tools Fable review in the existing provider session; Fable approval remains bound to the exact plan hash.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_dir": {**STRING, "description": "Run directory returned by create_run."},
                    "effort": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "xhigh", "max"],
                        "description": "Claude reasoning effort; defaults to high.",
                    },
                },
                "required": ["run_dir"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=True),
        },
        {
            "name": "dispatch_grok",
            "title": "Dispatch Grok in the approved feature session",
            "description": "Prepare one JSON line that starts the official Grok Build OAuth worker from the existing provider session after exact-hash approval.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_dir": {**STRING, "description": "Approved run directory."}},
                "required": ["run_dir"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=True),
        },
        {
            "name": "continue_grok",
            "title": "Continue Grok in the approved feature session",
            "description": "Store a one-time hash-bound correction packet and prepare one JSON line that resumes the feature's Grok session without another host approval.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_dir": {**STRING, "description": "Run directory."},
                    "instructions": {**STRING, "description": "Corrections that stay inside the approved scope."},
                },
                "required": ["run_dir", "instructions"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=False),
        },
        {
            "name": "close_provider_session",
            "title": "Close the feature provider session",
            "description": "Prepare the final JSON line that closes the temporary provider process and removes its private lease.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_dir": {**STRING, "description": "Run directory."}},
                "required": ["run_dir"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=True),
        },
        {
            "name": "run_status",
            "title": "Read run state and handoff messages",
            "description": "Read canonical state, artifacts, and recent agmsg messages without changing the run.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_dir": {**STRING, "description": "Run directory."}},
                "required": ["run_dir"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=True),
        },
        {
            "name": "record_verification",
            "title": "Record root Codex verification",
            "description": "Record independent checks; only a passing root verdict completes the run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_dir": {**STRING, "description": "Run directory."},
                    "verdict": {"type": "string", "enum": ["pass", "fail"]},
                    "report": {**STRING, "description": "Commands, results, diff review, and remaining risks."},
                },
                "required": ["run_dir", "verdict", "report"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=False),
        },
    ]


def _tool_result(payload: Dict[str, Any], *, is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}],
        "isError": is_error,
    }


def _arguments(value: Any, allowed: Set[str]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise combo_core.ComboError("Tool arguments must be an object.")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise combo_core.ComboError("Unexpected tool argument(s): %s." % ", ".join(unexpected))
    return value


def call_tool(name: str, arguments: Any) -> Dict[str, Any]:
    if name == "doctor":
        args = _arguments(arguments, {"run_dir"})
        store, _state, _project = _run_context(args.get("run_dir"))
        return _provider_session_input("doctor", store.run_dir)
    if name == "create_run":
        args = _arguments(arguments, {"project_root", "task", "acceptance_criteria", "context"})
        return combo_core.create_run(
            args.get("project_root"),
            args.get("task"),
            args.get("acceptance_criteria"),
            args.get("context", "No additional context supplied."),
        )
    if name == "record_plan":
        args = _arguments(arguments, {"run_dir", "plan"})
        return combo_core.record_plan(args.get("run_dir"), args.get("plan"))
    if name == "start_provider_session":
        args = _arguments(arguments, {"run_dir"})
        store, state, project = _run_context(args.get("run_dir"))
        state = combo_session.recover_orphaned_worker(store, state)
        if state.get("state") not in {"planned", "approved", "executed", "verification_failed", "execution_failed"}:
            raise combo_core.ComboError("Record a canonical plan before starting the provider session.")
        return _provider_session_request(store, state, project)
    if name == "review_plan":
        args = _arguments(arguments, {"run_dir", "effort"})
        effort = args.get("effort", "high")
        if effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise combo_core.ComboError("Unsupported Fable effort: %s" % effort)
        store, _state, _project = _run_context(args.get("run_dir"))
        return _provider_session_input(
            "review",
            store.run_dir,
            {"effort": effort},
        )
    if name == "dispatch_grok":
        args = _arguments(arguments, {"run_dir"})
        store, _state, _project = _run_context(args.get("run_dir"))
        return _provider_session_input("dispatch", store.run_dir)
    if name == "continue_grok":
        args = _arguments(arguments, {"run_dir", "instructions"})
        store, _state, _project = _run_context(args.get("run_dir"))
        request = combo_core.prepare_continuation_request(str(store.run_dir), args.get("instructions"))
        return _provider_session_input(
            "continue",
            store.run_dir,
            {
                "instructions_file": request["path"],
                "instructions_sha256": request["sha256"],
            },
        )
    if name == "close_provider_session":
        args = _arguments(arguments, {"run_dir"})
        store, _state, _project = _run_context(args.get("run_dir"))
        return _provider_session_input("close", store.run_dir)
    if name == "run_status":
        args = _arguments(arguments, {"run_dir"})
        return combo_core.run_status(args.get("run_dir"))
    if name == "record_verification":
        args = _arguments(arguments, {"run_dir", "verdict", "report"})
        return combo_core.record_verification(args.get("run_dir"), args.get("verdict"), args.get("report"))
    raise combo_core.ComboError("Unknown tool: %r." % name)


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request_id = request.get("id")
    if request_id is None:
        return None
    method = request.get("method")
    if method == "initialize":
        result: Dict[str, Any] = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "model-combo", "version": "0.4.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_definitions()}
    elif method == "tools/call":
        params = request.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments", {}) if isinstance(params, dict) else {}
        try:
            if not isinstance(name, str):
                raise combo_core.ComboError("Tool name must be a string.")
            result = _tool_result(call_tool(name, arguments))
        except (combo_core.ComboError, OSError, UnicodeDecodeError) as exc:
            result = _tool_result({"available": False, "error": str(exc)}, is_error=True)
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found: %s" % method},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = handle_request(request)
        except (json.JSONDecodeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
