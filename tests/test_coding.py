"""Coding tools preserve approved-folder boundaries and exact consent."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from primus.agent import Agent
from primus.plugins import discover
from primus.security import SecurityError
from primus.tool_router import select_plugins


class ScriptedProvider:
    def __init__(self, *messages):
        self.messages = list(messages)

    def chat(self, messages, tools):
        return self.messages.pop(0)


def call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}


class CodingTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "temp").mkdir(exist_ok=True)
        workspace = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        (self.root / "data").mkdir()
        (self.root / "logs").mkdir()
        (self.root / "outputs").mkdir()
        self.config = {"_root": self.root, "approved_folders": [], "max_steps": 5,
                       "max_invalid_responses": 2}
        self.plugins = discover(ROOT)

    def test_code_request_selects_coding_tools(self):
        selected = select_plugins("write Python code in a file", self.plugins, [])
        self.assertIn("code_check", selected)
        self.assertIn("code_patch", selected)
        self.assertIn("file_write", selected)
        self.assertNotIn("browser_open", selected)
        landing = select_plugins("Build an HTML landing page with a visible button and report its path", self.plugins, [])
        self.assertIn("file_write", landing)
        self.assertNotIn("window_list", landing)
        combined = select_plugins("Build an HTML landing page and open Chrome", self.plugins, [])
        self.assertIs(combined, self.plugins)
        self.assertIn("app_open", combined)

    def test_create_and_check_python_end_to_end(self):
        provider = ScriptedProvider(
            call("file_write", {"path": "outputs/demo/hello.py", "content": "print('hello')\n"}),
            call("code_check", {"path": "outputs/demo/hello.py"}),
            {"role": "assistant", "content": "Created and syntax checked outputs/demo/hello.py."})
        answer = Agent(self.config, self.plugins, provider).run("Write Python code and check syntax")
        self.assertIn("syntax checked", answer)
        self.assertEqual((self.root / "outputs/demo/hello.py").read_text(), "print('hello')\n")
        audit = (self.root / "logs/audit.jsonl").read_text()
        self.assertIn("code_check", audit)
        self.assertNotIn("print('hello')", audit)

    def test_patch_requires_consent_and_verifies(self):
        path = self.root / "outputs/demo.py"
        path.write_text("value = 1\n", encoding="utf-8")
        args = {"path": "outputs/demo.py", "old": "value = 1", "new": "value = 2"}
        rejected = Agent(self.config, self.plugins,
                         ScriptedProvider(call("code_patch", args), {"role": "assistant", "content": "Denied."}),
                         confirm=lambda _: False).run("Change the value")
        self.assertIn("needs review", rejected)
        self.assertEqual(path.read_text(), "value = 1\n")
        prompts = []
        accepted = Agent(self.config, self.plugins,
                         ScriptedProvider(call("code_patch", {"path": "outputs/demo.py", "old": "value = 1", "new": "value = 2"}),
                                          {"role": "assistant", "content": "Patched."}),
                         confirm=lambda prompt: prompts.append(prompt) or True).run("Change the value")
        self.assertIn("Patched", accepted)
        self.assertIn("value = 1", prompts[0])
        self.assertIn("value = 2", prompts[0])
        self.assertEqual(path.read_text(), "value = 2\n")
        audit = (self.root / "logs/audit.jsonl").read_text()
        self.assertNotIn("value = 1", audit)
        self.assertNotIn("value = 2", audit)

    def test_changed_file_and_protected_path_rejected(self):
        path = self.root / "outputs/demo.py"
        path.write_text("value = 1\n", encoding="utf-8")
        plugin = self.plugins["code_patch"]
        args = {"path": "outputs/demo.py", "old": "value = 1", "new": "value = 2"}
        plugin.summary(self.config, args)
        path.write_text("value = 3\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            plugin.run(self.config, args)
        self.assertEqual(path.read_text(), "value = 3\n")
        protected = self.root / "src/private.py"
        protected.parent.mkdir()
        protected.write_text("value = 1\n", encoding="utf-8")
        with self.assertRaises(SecurityError):
            plugin.summary(self.config, {"path": "src/private.py", "old": "1", "new": "2"})

    def test_invalid_syntax_is_reported_as_unverified(self):
        path = self.root / "outputs/bad.py"
        path.write_text("def bad(:\n", encoding="utf-8")
        provider = ScriptedProvider(call("code_check", {"path": "outputs/bad.py"}),
                                    {"role": "assistant", "content": "Looks fine."})
        answer = Agent(self.config, self.plugins, provider).run("Check Python code")
        self.assertIn("Task needs review", answer)
        self.assertFalse(self.plugins["code_check"].run(self.config, {"path": "outputs/bad.py"})["valid"])


if __name__ == "__main__":
    unittest.main()
