from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "subscription-triad"


class PluginStructureTests(unittest.TestCase):
    def test_manifest_and_companion_paths(self):
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("subscription-triad", manifest["name"])
        self.assertEqual("0.2.0", manifest["version"])
        self.assertEqual("./skills/", manifest["skills"])
        self.assertEqual("./.mcp.json", manifest["mcpServers"])
        self.assertTrue((PLUGIN / "skills" / "subscription-triad" / "SKILL.md").is_file())
        self.assertTrue(
            (PLUGIN / "skills" / "subscription-triad" / "scripts" / "triad_provider.py").is_file()
        )
        self.assertTrue((PLUGIN / ".mcp.json").is_file())
        self.assertNotIn("[TODO:", json.dumps(manifest))

    def test_marketplace_points_to_plugin(self):
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual("subscription-triad", marketplace["name"])
        entry = marketplace["plugins"][0]
        self.assertEqual("subscription-triad", entry["name"])
        self.assertEqual("./plugins/subscription-triad", entry["source"]["path"])
        self.assertEqual("AVAILABLE", entry["policy"]["installation"])

    def test_repository_has_no_scaffold_todos(self):
        candidates = list(PLUGIN.rglob("*.md")) + list(PLUGIN.rglob("*.json")) + list(PLUGIN.rglob("*.yaml"))
        for path in candidates:
            self.assertNotIn("[TODO:", path.read_text(encoding="utf-8"), str(path))


if __name__ == "__main__":
    unittest.main()
