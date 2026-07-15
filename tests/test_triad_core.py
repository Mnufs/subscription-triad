from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "subscription-triad" / "skills" / "subscription-triad" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import triad_core  # noqa: E402


def approved(packet: str, effort: str):
    assert "# CANONICAL_PLAN" in packet
    return {
        "decision": "PLAN_APPROVED",
        "review": "PLAN_APPROVED\nNo material gaps.\n",
        "model": triad_core.FABLE_MODEL,
        "effort": effort,
    }


def revise(packet: str, effort: str):
    assert "# ACCEPTANCE_CRITERIA" in packet
    return {
        "decision": "PLAN_REVISE",
        "review": "PLAN_REVISE\nF-001: Add a regression test.\n",
        "model": triad_core.FABLE_MODEL,
        "effort": effort,
    }


class RunStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        created = triad_core.create_run(
            str(self.project),
            "Implement a bounded feature.",
            "The behavior is tested and documented.",
            "The repository is clean and uses Python.",
        )
        self.run_dir = created["run_dir"]

    def tearDown(self):
        self.temp.cleanup()

    def test_approval_is_bound_to_exact_plan_hash(self):
        first = triad_core.record_plan(self.run_dir, "1. Change the parser.\n2. Add tests.")
        first_hash = first["state"]["plan_sha256"]
        reviewed = triad_core.review_plan(self.run_dir, invoker=approved)
        self.assertEqual("approved", reviewed["state"]["state"])
        self.assertEqual(first_hash, reviewed["state"]["approved_plan_sha256"])

        changed = triad_core.record_plan(self.run_dir, "1. Change the parser.\n2. Add regression tests.\n3. Update docs.")
        self.assertEqual("planned", changed["state"]["state"])
        self.assertIsNone(changed["state"]["approved_plan_sha256"])
        self.assertNotEqual(first_hash, changed["state"]["plan_sha256"])

    def test_revise_requires_new_plan_and_counts_rounds(self):
        triad_core.record_plan(self.run_dir, "Initial plan")
        first = triad_core.review_plan(self.run_dir, invoker=revise)
        self.assertEqual("review_revise", first["state"]["state"])
        self.assertEqual(1, first["state"]["review_count"])
        with self.assertRaisesRegex(triad_core.TriadError, "unreviewed plan"):
            triad_core.review_plan(self.run_dir, invoker=approved)

        triad_core.record_plan(self.run_dir, "Revised plan with a regression test")
        second = triad_core.review_plan(self.run_dir, invoker=approved)
        self.assertEqual("approved", second["state"]["state"])
        self.assertEqual(2, second["state"]["review_count"])

    def test_five_review_limit_fails_closed(self):
        triad_core.record_plan(self.run_dir, "Plan v1")
        store = triad_core.RunStore(Path(self.run_dir))

        def lower_limit(state):
            state["max_reviews"] = 1

        store.mutate(lower_limit)
        triad_core.review_plan(self.run_dir, invoker=revise)
        triad_core.record_plan(self.run_dir, "Plan v2")
        with self.assertRaisesRegex(triad_core.TriadError, "safety limit"):
            triad_core.review_plan(self.run_dir, invoker=approved)

    def test_dispatch_rejects_unapproved_plan_before_external_checks(self):
        triad_core.record_plan(self.run_dir, "Unreviewed plan")
        with self.assertRaisesRegex(triad_core.TriadError, "approved plan"):
            triad_core.dispatch_grok(self.run_dir)

    def test_root_verification_is_required_for_completion(self):
        triad_core.record_plan(self.run_dir, "Approved plan")
        triad_core.review_plan(self.run_dir, invoker=approved)
        store = triad_core.RunStore(Path(self.run_dir))

        def fake_execution(state):
            state["state"] = "executed"

        store.mutate(fake_execution)
        completed = triad_core.record_verification(self.run_dir, "pass", "Reviewed the diff and all tests passed.")
        self.assertEqual("complete", completed["state"]["state"])

    def test_run_path_must_match_recorded_project(self):
        state_path = Path(self.run_dir) / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["project_root"] = str(Path(self.temp.name) / "somewhere-else")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(triad_core.TriadError):
            triad_core.RunStore(Path(self.run_dir))


class ProviderBoundaryTests(unittest.TestCase):
    def test_provider_environment_is_sanitized(self):
        source = {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "secret-a",
            "XAI_API_KEY": "secret-x",
            "XAI_API_BASE_URL": "https://example.invalid",
        }
        clean = triad_core.sanitized_provider_environment(source)
        self.assertEqual("/bin", clean["PATH"])
        self.assertNotIn("ANTHROPIC_API_KEY", clean)
        self.assertNotIn("XAI_API_KEY", clean)
        self.assertNotIn("XAI_API_BASE_URL", clean)
        self.assertEqual("1", clean["GROK_DISABLE_API_KEY_AUTH"])

    def test_parent_environment_report_lists_names_not_values(self):
        report = triad_core.present_api_environment({"XAI_API_KEY": "do-not-print", "PATH": "/bin"})
        self.assertEqual(["XAI_API_KEY"], report)
        self.assertNotIn("do-not-print", json.dumps(report))

    def test_grok_commands_force_oauth_and_reuse_session(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            created = triad_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            triad_core.record_plan(run_dir, "Plan")
            triad_core.review_plan(run_dir, invoker=approved)
            store = triad_core.RunStore(Path(run_dir))
            state = store.read()
            (store.run_dir / "handoff.md").write_text("handoff", encoding="utf-8")
            followup = store.run_dir / "followup-v2.md"
            followup.write_text("followup", encoding="utf-8")
            state["active_followup"] = str(followup)
            with mock.patch.object(triad_core, "resolve_grok", return_value=Path("/fake/grok")):
                initial = triad_core.build_grok_command(state, store, "initial")
                continuation = triad_core.build_grok_command(state, store, "continue")
            self.assertIn("--oauth", initial)
            self.assertIn("--session-id", initial)
            self.assertIn(state["grok_session_id"], initial)
            self.assertIn("--resume", continuation)
            self.assertIn(state["grok_session_id"], continuation)
            self.assertNotIn("api.x.ai", " ".join(initial + continuation))

    def test_worker_start_failure_updates_state_instead_of_sticking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            agmsg = root / "agmsg"
            scripts = agmsg / "scripts"
            scripts.mkdir(parents=True)
            for name in ("api.sh", "join.sh", "send.sh"):
                (scripts / name).write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

            created = triad_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            triad_core.record_plan(run_dir, "Plan")
            triad_core.review_plan(run_dir, invoker=approved)
            store = triad_core.RunStore(Path(run_dir))
            (store.run_dir / "handoff.md").write_text("handoff", encoding="utf-8")

            def dispatched(state):
                state["state"] = "dispatched"
                state["execution_round"] = 1

            store.mutate(dispatched)
            with mock.patch.object(triad_core, "resolve_grok", side_effect=triad_core.TriadError("grok unavailable")):
                result = triad_core.run_grok_worker(run_dir, str(agmsg), "initial")
            self.assertFalse(result["succeeded"])
            self.assertEqual("execution_failed", result["state"]["state"])
            self.assertIsNone(result["state"]["worker_pid"])


if __name__ == "__main__":
    unittest.main()
