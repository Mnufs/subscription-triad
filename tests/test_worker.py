from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "model-combo" / "skills" / "model-combo" / "scripts"
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(SCRIPTS))

import support  # noqa: E402,F401
import combo_core  # noqa: E402
import combo_session  # noqa: E402
import combo_worker  # noqa: E402


class WorkerLeaseTests(unittest.TestCase):
    def _lease(self, root: Path):
        project = root / "project"
        project.mkdir()
        created = combo_core.create_run(str(project), "Task", "Acceptance", "Context")
        store = combo_core.RunStore(Path(created["run_dir"]))
        return combo_session.ProviderLease(store).acquire()

    def test_watchdog_accepts_current_session_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            lease = self._lease(Path(temp))
            with mock.patch.dict(os.environ, lease.environment(), clear=False):
                stop = combo_worker._start_lease_watchdog()
                self.assertFalse(stop.is_set())
                stop.set()
            lease.close()

    def test_watchdog_rejects_incomplete_session_environment(self):
        with mock.patch.dict(
            os.environ,
            {
                combo_session.LEASE_PATH_ENV: "/missing/lease",
                combo_session.LEASE_TOKEN_ENV: "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(combo_core.ComboError, "incomplete"):
                combo_worker._start_lease_watchdog()


if __name__ == "__main__":
    unittest.main()
