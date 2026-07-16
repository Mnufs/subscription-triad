from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "subscription-triad" / "skills" / "subscription-triad" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import triad_core  # noqa: E402
import triad_session  # noqa: E402
import triad_worker  # noqa: E402


class WorkerLeaseTests(unittest.TestCase):
    def _lease(self, root: Path):
        project = root / "project"
        project.mkdir()
        created = triad_core.create_run(str(project), "Task", "Acceptance", "Context")
        store = triad_core.RunStore(Path(created["run_dir"]))
        return triad_session.ProviderLease(store).acquire()

    def test_watchdog_accepts_current_session_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            lease = self._lease(Path(temp))
            with mock.patch.dict(os.environ, lease.environment(), clear=False):
                stop = triad_worker._start_lease_watchdog()
                self.assertFalse(stop.is_set())
                stop.set()
            lease.close()

    def test_watchdog_rejects_incomplete_session_environment(self):
        with mock.patch.dict(
            os.environ,
            {
                triad_session.LEASE_PATH_ENV: "/missing/lease",
                triad_session.LEASE_TOKEN_ENV: "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(triad_core.TriadError, "incomplete"):
                triad_worker._start_lease_watchdog()


if __name__ == "__main__":
    unittest.main()
