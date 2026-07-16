from __future__ import annotations

import os
from pathlib import Path
import tempfile


_STATE_HOME = tempfile.TemporaryDirectory(prefix="model-combo-tests-")
STATE_ROOT = (Path(_STATE_HOME.name) / "state").resolve()
os.environ["MODEL_COMBO_STATE_DIR"] = str(STATE_ROOT)
