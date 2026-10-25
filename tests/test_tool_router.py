"""The fast route may narrow a prompt, never bypass execution gates."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from primus.plugins import Plugin, discover
from primus.tool_router import select_plugins
from primus.fast_intents import recognize
from primus.agent import Agent


class ToolRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugins = discover(ROOT, enabled_network_plugins={
            "browser_open", "browser_navigate", "browser_read", "browser_click", "browser_type"
        })

    def test_clear_folder_request_uses_file_tools_only(self):
        selected = select_plugins("List my PRIMUS outputs folder", self.plugins, [])
        self.assertEqual(set(selected), {"file_read", "file_list", "file_write", "file_overwrite"})

    def test_clear_reminder_request_uses_schedule_tools_only(self):
        selected = select_plugins("What reminders are scheduled?", self.plugins, [])
        self.assertEqual(set(selected), {"schedule_add", "schedule_list", "schedule_disable"})

    def test_window_request_uses_window_tools_only(self):
        selected = select_plugins("List visible windows", self.plugins, [])
        self.assertEqual(set(selected), {"window_list", "window_manage", "window_input",
                                         "ui_controls", "ui_activate", "ui_set_text", "window_vision"})

    def test_compound_context_and_unknown_plugin_get_full_catalog(self):
        self.assertIs(select_plugins("List files and set a reminder", self.plugins, []), self.plugins)
        self.assertIs(select_plugins("Open it", self.plugins, [{"role": "user", "content": "a page"}]), self.plugins)
        self.assertEqual(set(select_plugins("Build a landing page", self.plugins, [])),
                         {"file_read", "file_list", "file_write", "file_overwrite",
                          "code_patch", "code_check", "script_run"})
        custom = dict(self.plugins)
        custom["custom_ability"] = Plugin("custom_ability", "Something new", {"type": "object"}, "READ", False, lambda *_: {})
        self.assertIs(select_plugins("List files", custom, []), custom)

    def test_exact_read_only_intents_do_not_grab_complex_requests(self):
        self.assertEqual(recognize("List my PRIMUS outputs folder"), ("file_list", {"path": "outputs"}))
        self.assertEqual(recognize("What reminders are scheduled?"), ("schedule_list", {}))
        self.assertEqual(recognize("List open windows"), ("window_list", {}))
        self.assertEqual(recognize("List installed apps"), ("installed_app_search", {"query": ""}))
        self.assertIsNone(recognize("List my outputs folder and send it to me"))

    def test_unadvertised_tool_cannot_execute(self):
        class BadProvider:
            def __init__(self):
                self.calls = 0
            def chat(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return {"role": "assistant", "tool_calls": [{"function": {
                        "name": "schedule_add", "arguments": {"kind": "reminder", "content": "test",
                        "when": "2099-01-01T00:00:00+00:00"}}}]}
                return {"role": "assistant", "content": "Could not list the folder."}
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            (root / "data").mkdir()
            provider = BadProvider()
            answer = Agent({"_root": root, "max_steps": 3}, self.plugins, provider).run("List my outputs folder")
            self.assertIn("Tool was not advertised", answer)
            self.assertFalse((root / "data" / "primus.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
