from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "model-combo" / "skills" / "model-combo" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import combo_core  # noqa: E402
import combo_provider  # noqa: E402
import combo_session  # noqa: E402


class ProviderBridgeTests(unittest.TestCase):
    def _planned_run(self, root: Path):
        project = root / "project"
        project.mkdir()
        created = combo_core.create_run(str(project), "Task", "Acceptance", "Context")
        combo_core.record_plan(created["run_dir"], "Plan")
        return project, created["run_dir"]

    def test_command_line_exposes_only_run_scoped_session(self):
        parser = combo_provider.build_parser()
        parsed = parser.parse_args(["session", "--run", "/tmp/run"])
        self.assertEqual("session", parsed.command)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["doctor", "--project", "/tmp"])

    def test_private_lease_is_exclusive_and_removed_on_close(self):
        with tempfile.TemporaryDirectory() as temp:
            _project, run_dir = self._planned_run(Path(temp))
            store = combo_core.RunStore(Path(run_dir))
            lease = combo_session.ProviderLease(store).acquire()
            self.assertTrue(combo_session.lease_is_current(lease.path, lease.token))
            self.assertFalse(
                combo_session.lease_is_current(
                    lease.path,
                    lease.token,
                    now=lease.path.stat().st_mtime + combo_session.LEASE_TTL_SECONDS + 1,
                )
            )
            self.assertEqual(0o600, stat.S_IMODE(lease.path.stat().st_mode))
            with self.assertRaisesRegex(combo_core.ComboError, "already active"):
                combo_session.ProviderLease(store).acquire()
            lease.close()
            self.assertFalse(lease.path.exists())

    def test_run_binding_rejects_relocated_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _project, run_dir = self._planned_run(root)
            other = root / "other"
            other.mkdir()
            store = combo_core.RunStore(Path(run_dir))

            def relocate(state):
                state["project_root"] = str(other)

            store.mutate(relocate)
            with self.assertRaisesRegex(combo_core.ComboError, "escapes the project root|outside its target project"):
                combo_session.validate_run_binding(run_dir)

    def test_orphaned_worker_state_is_recoverable(self):
        with tempfile.TemporaryDirectory() as temp:
            _project, run_dir = self._planned_run(Path(temp))
            store = combo_core.RunStore(Path(run_dir))

            def orphan(state):
                state["state"] = "executing"
                state["worker_pid"] = 999_999_999

            state = store.mutate(orphan)
            recovered = combo_session.recover_orphaned_worker(store, state)
            self.assertEqual("execution_failed", recovered["state"])
            self.assertIsNone(recovered["worker_pid"])
            self.assertIn("previous provider session ended", recovered["executor_error"])

    def test_one_process_reuses_doctor_and_reviews_then_closes(self):
        with tempfile.TemporaryDirectory() as temp:
            project, run_dir = self._planned_run(Path(temp))
            requests = [
                {"action": "doctor", "request_id": "doctor-1"},
                {"action": "doctor", "request_id": "doctor-2"},
                {"action": "review", "request_id": "review-1", "effort": "max"},
                {"action": "close", "request_id": "close-1"},
            ]
            source = io.StringIO("".join(json.dumps(item) + "\n" for item in requests))
            output = io.StringIO()
            with mock.patch.object(combo_core, "doctor", return_value={"ready": True}) as doctor:
                with mock.patch.object(
                    combo_core,
                    "review_plan",
                    return_value={"decision": "PLAN_APPROVED"},
                ) as review:
                    code = combo_provider.serve_session(
                        run_dir,
                        input_stream=source,
                        output_stream=output,
                        idle_timeout_seconds=2,
                        hard_timeout_seconds=5,
                    )
            self.assertEqual(0, code)
            doctor.assert_called_once_with(str(project.resolve()))
            review.assert_called_once_with(run_dir, effort="max")
            messages = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual("session_ready", messages[0]["event"])
            self.assertEqual(
                ["doctor-1", "doctor-2", "review-1", "close-1"],
                [message["request_id"] for message in messages[1:]],
            )
            self.assertFalse((Path(run_dir) / combo_session.LEASE_FILE_NAME).exists())

    def test_real_json_lines_process_stays_open_until_close(self):
        with tempfile.TemporaryDirectory() as temp:
            _project, run_dir = self._planned_run(Path(temp))
            process = subprocess.Popen(
                [sys.executable, str(SCRIPTS / "combo_provider.py"), "session", "--run", run_dir],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(
                json.dumps({"action": "close", "request_id": "close-process"}) + "\n",
                timeout=5,
            )
            self.assertEqual(0, process.returncode, stderr)
            messages = [json.loads(line) for line in stdout.splitlines()]
            self.assertEqual("session_ready", messages[0]["event"])
            self.assertEqual("close-process", messages[1]["request_id"])
            self.assertTrue(messages[1]["result"]["closed"])
            self.assertFalse((Path(run_dir) / combo_session.LEASE_FILE_NAME).exists())

    def test_provider_actions_require_successful_doctor(self):
        with tempfile.TemporaryDirectory() as temp:
            project, run_dir = self._planned_run(Path(temp))
            controller = combo_provider.ProviderSession(combo_core.RunStore(Path(run_dir)), project)
            response, should_close = controller.handle(
                {"action": "review", "request_id": "review-early", "effort": "high"}
            )
            self.assertFalse(response["ok"])
            self.assertFalse(should_close)
            self.assertIn("Run doctor successfully", response["error"])

    def test_bound_instructions_are_consumed_once(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            created = combo_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            store = combo_core.RunStore(Path(run_dir))

            def executed(state):
                state["state"] = "executed"

            store.mutate(executed)
            request = combo_core.prepare_continuation_request(run_dir, "Bound correction")
            value = combo_provider.read_bound_instructions(run_dir, request["path"], request["sha256"])
            self.assertEqual("Bound correction\n", value)
            self.assertFalse(Path(request["path"]).exists())

    def test_bound_instructions_reject_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            created = combo_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            store = combo_core.RunStore(Path(run_dir))

            def executed(state):
                state["state"] = "executed"

            store.mutate(executed)
            request = combo_core.prepare_continuation_request(run_dir, "Original correction")
            Path(request["path"]).write_text("Changed correction\n", encoding="utf-8")
            with self.assertRaisesRegex(combo_core.ComboError, "changed after its session input"):
                combo_provider.read_bound_instructions(run_dir, request["path"], request["sha256"])


if __name__ == "__main__":
    unittest.main()
