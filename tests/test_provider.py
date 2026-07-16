from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "subscription-triad" / "skills" / "subscription-triad" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import triad_core  # noqa: E402
import triad_provider  # noqa: E402


class ProviderBridgeTests(unittest.TestCase):
    def test_only_provider_dependent_actions_are_exposed(self):
        parser = triad_provider.build_parser()
        for argv, expected in (
            (["doctor", "--project", "/tmp"], "doctor"),
            (["review", "--run", "/tmp/run"], "review"),
            (["dispatch", "--run", "/tmp/run"], "dispatch"),
        ):
            self.assertEqual(expected, parser.parse_args(argv).command)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["create"])

    def test_doctor_delegates_to_core(self):
        args = triad_provider.build_parser().parse_args(["doctor", "--project", "/tmp"])
        with mock.patch.object(triad_core, "doctor", return_value={"ready": True}) as doctor:
            self.assertEqual({"ready": True}, triad_provider.execute(args))
        doctor.assert_called_once_with("/tmp")

    def test_bound_instructions_are_consumed_once(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            created = triad_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            store = triad_core.RunStore(Path(run_dir))

            def executed(state):
                state["state"] = "executed"

            store.mutate(executed)
            request = triad_core.prepare_continuation_request(run_dir, "Bound correction")
            value = triad_provider.read_bound_instructions(run_dir, request["path"], request["sha256"])
            self.assertEqual("Bound correction\n", value)
            self.assertFalse(Path(request["path"]).exists())

    def test_bound_instructions_reject_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            created = triad_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            store = triad_core.RunStore(Path(run_dir))

            def executed(state):
                state["state"] = "executed"

            store.mutate(executed)
            request = triad_core.prepare_continuation_request(run_dir, "Original correction")
            Path(request["path"]).write_text("Changed correction\n", encoding="utf-8")
            with self.assertRaisesRegex(triad_core.TriadError, "changed after host approval"):
                triad_provider.read_bound_instructions(run_dir, request["path"], request["sha256"])


if __name__ == "__main__":
    unittest.main()
