#!/usr/bin/env python3
"""MCP facade for Subscription Triad."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Set

import triad_core


STRING = {"type": "string", "maxLength": triad_core.MAX_TEXT_CHARS}


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
            "title": "Check subscription-only provider readiness",
            "description": "Check official Claude, Grok Build, and agmsg readiness without making a model call.",
            "inputSchema": {
                "type": "object",
                "properties": {"project_root": {**STRING, "description": "Optional project root for Grok discovery."}},
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=True, open_world=True),
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
            "name": "review_plan",
            "title": "Review the current plan with Claude Fable 5",
            "description": "Run one no-tools Fable review and bind approval to the exact plan hash.",
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
            "annotations": _annotations(read_only=False, open_world=True),
        },
        {
            "name": "dispatch_grok",
            "title": "Dispatch the approved plan to Grok Build",
            "description": "Start a detached official Grok Build OAuth worker after exact-hash approval.",
            "inputSchema": {
                "type": "object",
                "properties": {"run_dir": {**STRING, "description": "Approved run directory."}},
                "required": ["run_dir"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=False, open_world=True),
        },
        {
            "name": "continue_grok",
            "title": "Continue the same Grok Build session",
            "description": "Send bounded verification fixes to the existing feature session to preserve context reuse.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_dir": {**STRING, "description": "Run directory."},
                    "instructions": {**STRING, "description": "Corrections that stay inside the approved scope."},
                },
                "required": ["run_dir", "instructions"],
                "additionalProperties": False,
            },
            "annotations": _annotations(read_only=False, open_world=True),
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
        raise triad_core.TriadError("Tool arguments must be an object.")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise triad_core.TriadError("Unexpected tool argument(s): %s." % ", ".join(unexpected))
    return value


def call_tool(name: str, arguments: Any) -> Dict[str, Any]:
    if name == "doctor":
        args = _arguments(arguments, {"project_root"})
        return triad_core.doctor(args.get("project_root"))
    if name == "create_run":
        args = _arguments(arguments, {"project_root", "task", "acceptance_criteria", "context"})
        return triad_core.create_run(
            args.get("project_root"),
            args.get("task"),
            args.get("acceptance_criteria"),
            args.get("context", "No additional context supplied."),
        )
    if name == "record_plan":
        args = _arguments(arguments, {"run_dir", "plan"})
        return triad_core.record_plan(args.get("run_dir"), args.get("plan"))
    if name == "review_plan":
        args = _arguments(arguments, {"run_dir", "effort"})
        return triad_core.review_plan(args.get("run_dir"), effort=args.get("effort", "high"))
    if name == "dispatch_grok":
        args = _arguments(arguments, {"run_dir"})
        return triad_core.dispatch_grok(args.get("run_dir"))
    if name == "continue_grok":
        args = _arguments(arguments, {"run_dir", "instructions"})
        return triad_core.continue_grok(args.get("run_dir"), args.get("instructions"))
    if name == "run_status":
        args = _arguments(arguments, {"run_dir"})
        return triad_core.run_status(args.get("run_dir"))
    if name == "record_verification":
        args = _arguments(arguments, {"run_dir", "verdict", "report"})
        return triad_core.record_verification(args.get("run_dir"), args.get("verdict"), args.get("report"))
    raise triad_core.TriadError("Unknown tool: %r." % name)


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request_id = request.get("id")
    if request_id is None:
        return None
    method = request.get("method")
    if method == "initialize":
        result: Dict[str, Any] = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "subscription-triad", "version": "0.1.0"},
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
                raise triad_core.TriadError("Tool name must be a string.")
            result = _tool_result(call_tool(name, arguments))
        except (triad_core.TriadError, OSError, UnicodeDecodeError) as exc:
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
