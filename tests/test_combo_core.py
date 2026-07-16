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
SCRIPTS = ROOT / "plugins" / "model-combo" / "skills" / "model-combo" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import combo_core  # noqa: E402


def approved(packet: str, effort: str):
    assert "# CANONICAL_PLAN" in packet
    return {
        "decision": "PLAN_APPROVED",
        "review": "PLAN_APPROVED\nNo material gaps.\n",
        "model": combo_core.FABLE_MODEL,
        "effort": effort,
    }


def revise(packet: str, effort: str):
    assert "# ACCEPTANCE_CRITERIA" in packet
    return {
        "decision": "PLAN_REVISE",
        "review": "PLAN_REVISE\nF-001: Add a regression test.\n",
        "model": combo_core.FABLE_MODEL,
        "effort": effort,
    }


class RunStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        created = combo_core.create_run(
            str(self.project),
            "Implement a bounded feature.",
            "The behavior is tested and documented.",
            "The repository is clean and uses Python.",
        )
        self.run_dir = created["run_dir"]

    def tearDown(self):
        self.temp.cleanup()

    def test_approval_is_bound_to_exact_plan_hash(self):
        first = combo_core.record_plan(self.run_dir, "1. Change the parser.\n2. Add tests.")
        first_hash = first["state"]["plan_sha256"]
        reviewed = combo_core.review_plan(self.run_dir, invoker=approved)
        self.assertEqual("approved", reviewed["state"]["state"])
        self.assertEqual(first_hash, reviewed["state"]["approved_plan_sha256"])

        changed = combo_core.record_plan(self.run_dir, "1. Change the parser.\n2. Add regression tests.\n3. Update docs.")
        self.assertEqual("planned", changed["state"]["state"])
        self.assertIsNone(changed["state"]["approved_plan_sha256"])
        self.assertNotEqual(first_hash, changed["state"]["plan_sha256"])

    def test_revise_requires_new_plan_and_counts_rounds(self):
        combo_core.record_plan(self.run_dir, "Initial plan")
        first = combo_core.review_plan(self.run_dir, invoker=revise)
        self.assertEqual("review_revise", first["state"]["state"])
        self.assertEqual(1, first["state"]["review_count"])
        with self.assertRaisesRegex(combo_core.ComboError, "unreviewed plan"):
            combo_core.review_plan(self.run_dir, invoker=approved)

        combo_core.record_plan(self.run_dir, "Revised plan with a regression test")
        second = combo_core.review_plan(self.run_dir, invoker=approved)
        self.assertEqual("approved", second["state"]["state"])
        self.assertEqual(2, second["state"]["review_count"])

    def test_five_review_limit_fails_closed(self):
        combo_core.record_plan(self.run_dir, "Plan v1")
        store = combo_core.RunStore(Path(self.run_dir))

        def lower_limit(state):
            state["max_reviews"] = 1

        store.mutate(lower_limit)
        combo_core.review_plan(self.run_dir, invoker=revise)
        combo_core.record_plan(self.run_dir, "Plan v2")
        with self.assertRaisesRegex(combo_core.ComboError, "safety limit"):
            combo_core.review_plan(self.run_dir, invoker=approved)

    def test_dispatch_rejects_unapproved_plan_before_external_checks(self):
        combo_core.record_plan(self.run_dir, "Unreviewed plan")
        with self.assertRaisesRegex(combo_core.ComboError, "approved plan"):
            combo_core.dispatch_grok(self.run_dir)

    def test_root_verification_is_required_for_completion(self):
        combo_core.record_plan(self.run_dir, "Approved plan")
        combo_core.review_plan(self.run_dir, invoker=approved)
        store = combo_core.RunStore(Path(self.run_dir))

        def fake_execution(state):
            state["state"] = "executed"

        store.mutate(fake_execution)
        completed = combo_core.record_verification(self.run_dir, "pass", "Reviewed the diff and all tests passed.")
        self.assertEqual("complete", completed["state"]["state"])

    def test_run_path_must_match_recorded_project(self):
        state_path = Path(self.run_dir) / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["project_root"] = str(Path(self.temp.name) / "somewhere-else")
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(combo_core.ComboError):
            combo_core.RunStore(Path(self.run_dir))


class ProviderBoundaryTests(unittest.TestCase):
    def test_provider_environment_is_sanitized(self):
        source = {
            "PATH": "/bin",
            "ANTHROPIC_API_KEY": "secret-a",
            "XAI_API_KEY": "secret-x",
            "XAI_API_BASE_URL": "https://example.invalid",
            "COMBO_PROVIDER_SESSION_LEASE": "/tmp/lease",
            "COMBO_PROVIDER_SESSION_TOKEN": "opaque-token",
        }
        clean = combo_core.sanitized_provider_environment(source)
        self.assertEqual("/bin", clean["PATH"])
        self.assertNotIn("ANTHROPIC_API_KEY", clean)
        self.assertNotIn("XAI_API_KEY", clean)
        self.assertNotIn("XAI_API_BASE_URL", clean)
        self.assertEqual("1", clean["GROK_DISABLE_API_KEY_AUTH"])
        self.assertEqual("/tmp/lease", clean["COMBO_PROVIDER_SESSION_LEASE"])
        self.assertEqual("opaque-token", clean["COMBO_PROVIDER_SESSION_TOKEN"])

    def test_parent_environment_report_lists_names_not_values(self):
        report = combo_core.present_api_environment({"XAI_API_KEY": "do-not-print", "PATH": "/bin"})
        self.assertEqual(["XAI_API_KEY"], report)
        self.assertNotIn("do-not-print", json.dumps(report))

    def test_doctor_uses_embedded_transport_when_agmsg_is_absent(self):
        with mock.patch.object(
            combo_core,
            "check_claude_subscription",
            return_value={"available": True},
        ):
            with mock.patch.object(
                combo_core,
                "check_grok_subscription",
                return_value={"available": True},
            ):
                with mock.patch.object(combo_core, "find_agmsg_root", return_value=None):
                    report = combo_core.doctor()
        self.assertTrue(report["ready"])
        self.assertEqual("embedded", report["agmsg"]["mode"])
        self.assertIsNone(report["agmsg"]["root"])

    def test_embedded_transport_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            created = combo_core.create_run(str(project), "Task", "Acceptance", "Context")
            state = created["state"]
            transport_path = combo_core._embedded_transport_path(state)
            self.assertEqual([], combo_core._read_agmsg_messages(None, state))
            self.assertFalse(transport_path.exists())
            message_id = combo_core._agmsg_send(None, state, "COMBO_EXECUTION_DONE test")
            messages = combo_core._read_agmsg_messages(None, state)
        self.assertEqual("1", message_id)
        self.assertEqual(1, len(messages))
        self.assertEqual("COMBO_EXECUTION_DONE test", messages[0]["body"])

    def test_grok_readiness_prefers_current_model_and_accepts_legacy_alias(self):
        inspection = subprocess.CompletedProcess(
            ["grok", "inspect", "--json"],
            0,
            json.dumps({"loginPolicy": {"apiKeyAuthDisabled": True}}),
            "",
        )
        cases = (
            ("Default model: grok-4.5\n", "grok-4.5"),
            ("Available models:\n  * grok-build\n", "grok-build"),
        )
        for model_output, expected in cases:
            with self.subTest(expected=expected):
                models = subprocess.CompletedProcess(["grok", "--oauth", "models"], 0, model_output, "")
                with mock.patch.object(combo_core, "resolve_grok", return_value=Path("/fake/grok")):
                    with mock.patch.object(combo_core, "_run", side_effect=(inspection, models)):
                        result = combo_core.check_grok_subscription()
                self.assertEqual(expected, result["model"])

    def test_grok_readiness_rejects_unrecognized_models(self):
        inspection = subprocess.CompletedProcess(
            ["grok", "inspect", "--json"],
            0,
            json.dumps({"loginPolicy": {"apiKeyAuthDisabled": True}}),
            "",
        )
        models = subprocess.CompletedProcess(
            ["grok", "--oauth", "models"],
            0,
            "Available models:\n  * grok-composer-2.5-fast\n",
            "",
        )
        with mock.patch.object(combo_core, "resolve_grok", return_value=Path("/fake/grok")):
            with mock.patch.object(combo_core, "_run", side_effect=(inspection, models)):
                with self.assertRaisesRegex(combo_core.ComboError, "supported model"):
                    combo_core.check_grok_subscription()

    def test_grok_readiness_rejects_cached_models_after_network_failure(self):
        inspection = subprocess.CompletedProcess(
            ["grok", "inspect", "--json"],
            0,
            json.dumps({"loginPolicy": {"apiKeyAuthDisabled": True}}),
            "",
        )
        models = subprocess.CompletedProcess(
            ["grok", "--oauth", "models"],
            0,
            "Default model: grok-4.5\n",
            "Failed to fetch models: tcp connect error\n",
        )
        with mock.patch.object(combo_core, "resolve_grok", return_value=Path("/fake/grok")):
            with mock.patch.object(combo_core, "_run", side_effect=(inspection, models)):
                with self.assertRaisesRegex(combo_core.ComboError, "approved provider session"):
                    combo_core.check_grok_subscription()

    def test_grok_timeout_identifies_the_failed_stage(self):
        with mock.patch.object(combo_core, "resolve_grok", return_value=Path("/fake/grok")):
            with mock.patch.object(
                combo_core,
                "_run",
                side_effect=combo_core.ComboError("Command timed out: grok"),
            ):
                with self.assertRaisesRegex(combo_core.ComboError, "configuration inspection"):
                    combo_core.check_grok_subscription()

    def test_grok_commands_force_oauth_and_reuse_session(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            created = combo_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            combo_core.record_plan(run_dir, "Plan")
            combo_core.review_plan(run_dir, invoker=approved)
            store = combo_core.RunStore(Path(run_dir))
            state = store.read()
            (store.run_dir / "handoff.md").write_text("handoff", encoding="utf-8")
            followup = store.run_dir / "followup-v2.md"
            followup.write_text("followup", encoding="utf-8")
            state["active_followup"] = str(followup)
            with mock.patch.object(combo_core, "resolve_grok", return_value=Path("/fake/grok")):
                initial = combo_core.build_grok_command(state, store, "initial")
                state["grok_model"] = "grok-build"
                continuation = combo_core.build_grok_command(state, store, "continue")
            self.assertIn("--oauth", initial)
            self.assertEqual(str(project.resolve()), initial[initial.index("--cwd") + 1])
            self.assertEqual("workspace", initial[initial.index("--sandbox") + 1])
            self.assertEqual("grok-4.5", initial[initial.index("--model") + 1])
            self.assertIn("--session-id", initial)
            self.assertIn(state["grok_session_id"], initial)
            self.assertIn("--resume", continuation)
            self.assertEqual(str(project.resolve()), continuation[continuation.index("--cwd") + 1])
            self.assertEqual("workspace", continuation[continuation.index("--sandbox") + 1])
            self.assertEqual("grok-build", continuation[continuation.index("--model") + 1])
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

            created = combo_core.create_run(str(project), "Task", "Acceptance", "Context")
            run_dir = created["run_dir"]
            combo_core.record_plan(run_dir, "Plan")
            combo_core.review_plan(run_dir, invoker=approved)
            store = combo_core.RunStore(Path(run_dir))
            (store.run_dir / "handoff.md").write_text("handoff", encoding="utf-8")

            def dispatched(state):
                state["state"] = "dispatched"
                state["execution_round"] = 1

            store.mutate(dispatched)
            with mock.patch.object(combo_core, "resolve_grok", side_effect=combo_core.ComboError("grok unavailable")):
                result = combo_core.run_grok_worker(run_dir, str(agmsg), "initial")
            self.assertFalse(result["succeeded"])
            self.assertEqual("execution_failed", result["state"]["state"])
            self.assertIsNone(result["state"]["worker_pid"])


if __name__ == "__main__":
    unittest.main()
