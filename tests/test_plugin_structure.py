from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "model-combo"


class PluginStructureTests(unittest.TestCase):
    def test_manifest_and_companion_paths(self):
        manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual("model-combo", manifest["name"])
        self.assertEqual("0.4.0", manifest["version"])
        self.assertEqual(
            "Subscription-native orchestration for AI coding CLIs.",
            manifest["description"],
        )
        self.assertEqual("./skills/", manifest["skills"])
        self.assertEqual("./.mcp.json", manifest["mcpServers"])
        self.assertTrue((PLUGIN / "skills" / "model-combo" / "SKILL.md").is_file())
        self.assertTrue(
            (PLUGIN / "skills" / "model-combo" / "scripts" / "combo_provider.py").is_file()
        )
        self.assertTrue(
            (PLUGIN / "skills" / "model-combo" / "scripts" / "combo_session.py").is_file()
        )
        self.assertTrue((PLUGIN / ".mcp.json").is_file())
        self.assertNotIn("[TODO:", json.dumps(manifest))

    def test_bilingual_readmes_use_the_new_public_identity(self):
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertTrue(english.startswith("# Model Combo\n"))
        self.assertTrue(chinese.startswith("# Model Combo\n"))
        self.assertIn("[简体中文](README.zh-CN.md)", english)
        self.assertIn("[English](README.md)", chinese)
        self.assertIn("not a voting ensemble", english)

    def test_marketplace_points_to_plugin(self):
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual("model-combo", marketplace["name"])
        entry = marketplace["plugins"][0]
        self.assertEqual("model-combo", entry["name"])
        self.assertEqual("./plugins/model-combo", entry["source"]["path"])
        self.assertEqual("AVAILABLE", entry["policy"]["installation"])

    def test_repository_has_no_scaffold_todos(self):
        candidates = list(PLUGIN.rglob("*.md")) + list(PLUGIN.rglob("*.json")) + list(PLUGIN.rglob("*.yaml"))
        for path in candidates:
            self.assertNotIn("[TODO:", path.read_text(encoding="utf-8"), str(path))


if __name__ == "__main__":
    unittest.main()
