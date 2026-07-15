from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


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
