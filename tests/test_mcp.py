from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "subscription-triad" / "skills" / "subscription-triad" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import triad_mcp  # noqa: E402


class McpTests(unittest.TestCase):
    def _planned_run(self, root: Path):
        project = root / "project"
        project.mkdir()
        created = triad_mcp.triad_core.create_run(str(project), "Task", "Acceptance", "Context")
        triad_mcp.triad_core.record_plan(created["run_dir"], "Plan")
        return project, created["run_dir"]

    def test_initialize_and_tool_list(self):
        initialized = triad_mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual("subscription-triad", initialized["result"]["serverInfo"]["name"])
        self.assertEqual("0.3.0", initialized["result"]["serverInfo"]["version"])
        listed = triad_mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(
            {
                "doctor",
                "create_run",
                "record_plan",
                "start_provider_session",
                "review_plan",
                "dispatch_grok",
                "continue_grok",
                "close_provider_session",
                "run_status",
                "record_verification",
            },
            names,
        )

    def test_create_run_tool_writes_inside_project(self):
        with tempfile.TemporaryDirectory() as temp:
            result = triad_mcp.call_tool(
                "create_run",
                {
                    "project_root": temp,
                    "task": "Implement feature",
                    "acceptance_criteria": "Tests pass",
                    "context": "Python project",
                },
            )
            run_dir = Path(result["run_dir"])
            self.assertTrue((run_dir / "state.json").is_file())
            self.assertEqual(Path(temp).resolve(), run_dir.parents[2])

    def test_start_prepares_only_host_approved_feature_session(self):
        with tempfile.TemporaryDirectory() as temp:
            project, run_dir = self._planned_run(Path(temp))
            result = triad_mcp.call_tool("start_provider_session", {"run_dir": run_dir})
            self.assertEqual("scoped_host_session", result["action_required"])
            self.assertEqual("single_feature_session", result["approval_scope"])
            self.assertEqual(str(project.resolve()), result["cwd"])
            self.assertEqual("session", result["argv"][2])
            self.assertEqual(run_dir, result["argv"][-1])
            self.assertTrue(result["retain_process_session"])
            self.assertFalse(result["changes_codex_network_defaults"])
            self.assertFalse(result["allow_persistent_rule"])
            self.assertEqual([], result["config_files_to_modify"])

    def test_provider_actions_prepare_stdin_without_new_host_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            _project, run_dir = self._planned_run(Path(temp))
            with mock.patch.object(triad_mcp.triad_core, "doctor") as doctor:
                prepared = [
                    triad_mcp.call_tool("doctor", {"run_dir": run_dir}),
                    triad_mcp.call_tool("review_plan", {"run_dir": run_dir, "effort": "max"}),
                    triad_mcp.call_tool("dispatch_grok", {"run_dir": run_dir}),
                    triad_mcp.call_tool("close_provider_session", {"run_dir": run_dir}),
                ]
            doctor.assert_not_called()
            self.assertEqual(["doctor", "review", "dispatch", "close"], [item["provider_action"] for item in prepared])
            for item in prepared:
                self.assertEqual("provider_session_input", item["action_required"])
                self.assertFalse(item["requires_new_host_approval"])
                self.assertNotIn("argv", item)
                payload = json.loads(item["stdin"])
                self.assertEqual(item["provider_action"], payload["action"])
                self.assertEqual(item["request_id"], payload["request_id"])

    def test_continue_prepares_hash_bound_one_time_session_input(self):
        with tempfile.TemporaryDirectory() as temp:
            _project, run_dir = self._planned_run(Path(temp))
            store = triad_mcp.triad_core.RunStore(Path(run_dir))

            def executed(state):
                state["state"] = "executed"

            store.mutate(executed)
            result = triad_mcp.call_tool(
                "continue_grok",
                {"run_dir": run_dir, "instructions": "Fix the approved regression only."},
            )
            payload = json.loads(result["stdin"])
            request_path = Path(payload["instructions_file"])
            self.assertTrue(request_path.is_file())
            self.assertEqual(
                payload["instructions_sha256"],
                triad_mcp.triad_core.sha256_text(request_path.read_text(encoding="utf-8")),
            )
            self.assertFalse(result["requires_new_host_approval"])

    def test_start_requires_a_recorded_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            created = triad_mcp.triad_core.create_run(temp, "Task", "Acceptance", "Context")
            with self.assertRaisesRegex(triad_mcp.triad_core.TriadError, "Record a canonical plan"):
                triad_mcp.call_tool("start_provider_session", {"run_dir": created["run_dir"]})

    def test_tool_errors_are_returned_without_tracebacks(self):
        response = triad_mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "record_plan", "arguments": {"run_dir": "/missing", "plan": "Plan"}},
            }
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertFalse(payload["available"])
        self.assertNotIn("Traceback", payload["error"])


if __name__ == "__main__":
    unittest.main()
