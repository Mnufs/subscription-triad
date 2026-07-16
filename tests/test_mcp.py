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
    def test_initialize_and_tool_list(self):
        initialized = triad_mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual("subscription-triad", initialized["result"]["serverInfo"]["name"])
        listed = triad_mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(
            {
                "doctor",
                "create_run",
                "record_plan",
                "review_plan",
                "dispatch_grok",
                "continue_grok",
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

    def test_doctor_prepares_scoped_host_execution_without_calling_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(triad_mcp.triad_core, "doctor") as doctor:
                result = triad_mcp.call_tool("doctor", {"project_root": temp})
            doctor.assert_not_called()
            self.assertEqual("scoped_host_execution", result["action_required"])
            self.assertEqual("doctor", result["provider_action"])
            self.assertFalse(result["changes_codex_network_defaults"])
            self.assertFalse(result["allow_persistent_rule"])
            self.assertEqual([], result["config_files_to_modify"])
            self.assertTrue(result["argv"][1].endswith("triad_provider.py"))

    def test_continue_prepares_hash_bound_one_time_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            created = triad_mcp.triad_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            triad_mcp.triad_core.record_plan(run_dir, "Plan")
            store = triad_mcp.triad_core.RunStore(Path(run_dir))

            def executed(state):
                state["state"] = "executed"

            store.mutate(executed)
            result = triad_mcp.call_tool(
                "continue_grok",
                {"run_dir": run_dir, "instructions": "Fix the approved regression only."},
            )
            argv = result["argv"]
            request_path = Path(argv[argv.index("--instructions-file") + 1])
            expected_hash = argv[argv.index("--instructions-sha256") + 1]
            self.assertTrue(request_path.is_file())
            self.assertEqual(
                expected_hash,
                triad_mcp.triad_core.sha256_text(request_path.read_text(encoding="utf-8")),
            )
            self.assertFalse(result["allow_persistent_rule"])

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
