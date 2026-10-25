"""Automatic consent policy, real file effects, and retained restrictions."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from primus.agent import Agent
from primus.config import ConfigError, load_config
from primus.plugins import discover
from primus.security import SecurityError, authorize, guard_path


class AutoApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("logs", "data", "outputs"):
            (self.root / name).mkdir()
        self.config = {"_root": self.root, "permissions": {"auto_approve": True}}

    def test_all_tiers_skip_prompt_and_record_policy_not_human_consent(self):
        confirm = Mock(side_effect=AssertionError("Must not open a consent prompt"))
        for tier in ("READ", "WRITE", "DESTRUCTIVE", "EXTERNAL"):
            authorize(self.config, tier, "example_tool", {"content": "private text"}, "Action", confirm)
        confirm.assert_not_called()
        records = [json.loads(line) for line in (self.root / "logs/audit.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 4)
        self.assertTrue(all(row["result"]["approval_mode"] == "automatic" for row in records))
        self.assertNotIn("private text", (self.root / "logs/audit.jsonl").read_text())
        self.assertIn("not an individual manual review", (self.root / "decision.md").read_text())

    def test_disabling_restores_confirmation_and_default_is_manual(self):
        for policy in ({}, {"auto_approve": False}, {"auto_approve": "true"}):
            self.config["permissions"] = policy
            with self.assertRaises(SecurityError):
                authorize(self.config, "EXTERNAL", "app_open", {}, "Open", None)

    def test_unknown_tier_and_kill_switch_and_cancellation_still_block(self):
        with self.assertRaises(SecurityError):
            authorize(self.config, "INVALID", "tool", {}, "Action", None)
        marker = self.root / "data/STOP"
        marker.write_text("stop")
        with self.assertRaises(SecurityError):
            authorize(self.config, "EXTERNAL", "tool", {}, "Action", None)
        marker.unlink()
        self.config["_cancel_event"] = threading.Event()
        self.config["_cancel_event"].set()
        with self.assertRaises(SecurityError):
            authorize(self.config, "EXTERNAL", "tool", {}, "Action", None)

    def test_path_and_credential_guards_remain(self):
        for path in (str(self.root.parent / "outside.txt"), ".ssh/id_rsa", "config.json"):
            with self.subTest(path=path), self.assertRaises(SecurityError):
                guard_path(path, self.config, write=True)

    def test_agent_overwrites_real_file_without_prompt(self):
        target = self.root / "outputs/demo.txt"
        target.write_text("old")
        messages = iter([
            {"role": "assistant", "tool_calls": [{"function": {"name": "file_overwrite", "arguments":
                {"path": "outputs/demo.txt", "content": "new"}}}]},
            {"role": "assistant", "content": "Updated the file."},
        ])
        provider = Mock()
        provider.chat.side_effect = lambda *_: next(messages)
        confirm = Mock(side_effect=AssertionError("Unexpected prompt"))
        agent = Agent(self.config, discover(ROOT), provider, confirm=confirm)
        agent.run("Overwrite outputs/demo.txt")
        self.assertEqual(target.read_text(), "new")
        confirm.assert_not_called()
        self.assertIn("TRUSTED CONFIGURATION", provider.chat.call_args_list[0].args[0][0]["content"])

    def test_config_rejects_non_boolean_auto_approval(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
        config["root"] = str(self.root)
        for invalid in ("true", 1, None):
            config["permissions"]["auto_approve"] = invalid
            path = self.root / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "auto_approve"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
