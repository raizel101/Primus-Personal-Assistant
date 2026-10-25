"""General desktop control checks without activating a real app."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from primus.accessibility import activate, controls, describe_activation, describe_text, set_text
from primus.agent import Agent
from primus.audit import redact
from primus.plugins import discover
from primus.windowing import Window


class Provider:
    def __init__(self, *messages):
        self.messages = list(messages)
    def chat(self, *_):
        return self.messages.pop(0)


class AccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("data", "logs", "temp"):
            (self.root / folder).mkdir()
        self.config = {"_root": self.root, "permissions": {"read": "auto"},
                       "max_steps": 3, "max_invalid_responses": 2}
        self.window = Window("0x123", 42, "Example editor", "example.exe")
        self.row = {"element_id": "1.2.3", "label": "Save", "control_type": "Button",
                    "operation": "invoke",
                    "enabled": True, "visible": True, "bounds": [10, 20, 80, 30]}
        self.args = {"id": self.window.id, "pid": self.window.pid,
                     "title": self.window.title, "element_id": self.row["element_id"]}

    def test_control_listing_and_exact_recheck(self):
        with patch("primus.accessibility.resolve_window", return_value=self.window), \
             patch("primus.accessibility._backend", return_value={
                 "controls": [self.row], "truncated": False}) as backend:
            result = controls(self.config, {**self.args, "query": "Save"})
            self.assertEqual(result["controls"][0]["label"], "Save")
            self.assertEqual(backend.call_args.args[1]["pid"], 42)
            preview = describe_activation(self.config, self.args)
            self.assertIn("Save", preview)
            self.assertEqual(self.args["_expected_control"],
                             {"label": "Save", "control_type": "Button", "operation": "invoke"})
            backend.return_value = {"sent": True}
            result = activate(self.config, self.args)
            self.assertTrue(result["sent"])
            self.assertFalse(result["verified"])
            self.assertEqual(backend.call_args.args[1]["expected"]["label"], "Save")

    def test_hidden_control_cannot_be_approved(self):
        hidden = {**self.row, "visible": False}
        with patch("primus.accessibility.resolve_window", return_value=self.window), \
             patch("primus.accessibility._backend", return_value={"controls": [hidden]}):
            with self.assertRaises(ValueError):
                describe_activation(self.config, self.args)

    def test_credential_windows_are_blocked_before_inspection(self):
        sensitive = Window("0x124", 43, "Credential Manager", "vault.exe")
        with patch("primus.accessibility.resolve_window", return_value=sensitive), \
             patch("primus.accessibility._backend") as backend:
            with self.assertRaises(PermissionError):
                controls(self.config, self.args)
            backend.assert_not_called()

    def test_edit_field_replacement_requires_exact_preview(self):
        edit = {**self.row, "label": "Notes", "control_type": "Edit", "operation": "focus"}
        args = {**self.args, "text": "Project idea"}
        with patch("primus.accessibility.resolve_window", return_value=self.window), \
             patch("primus.accessibility._backend", return_value={"sent": True}) as backend:
            with self.assertRaises(ValueError):
                set_text(self.config, args)
            backend.return_value = {"controls": [edit]}
            preview = describe_text(self.config, args)
            self.assertIn("overwritten", preview)
            self.assertIn("Project idea", preview)
            backend.return_value = {"sent": True}
            result = set_text(self.config, args)
            self.assertTrue(result["sent"])
            self.assertFalse(result["verified"])
            self.assertEqual(backend.call_args.args[1]["text"], "Project idea")

    def test_denial_prevents_activation_and_audit_redacts_label(self):
        plugins = discover(PROJECT)
        self.assertEqual(plugins["ui_controls"].tier, "READ")
        self.assertEqual(plugins["ui_activate"].tier, "EXTERNAL")
        self.assertEqual(plugins["ui_set_text"].tier, "EXTERNAL")
        call = {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "ui_activate", "arguments": self.args}}]}
        provider = Provider(call, {"role": "assistant", "content": "Not activated."})
        backend = Mock(return_value={"controls": [self.row], "truncated": False})
        with patch("primus.accessibility.resolve_window", return_value=self.window), \
             patch("primus.accessibility._backend", backend):
            result = Agent(self.config, plugins, provider, confirm=lambda _prompt: False).run("Click Save button")
        self.assertIn("needs review", result)
        self.assertEqual([entry.args[1]["mode"] for entry in backend.call_args_list], ["list"])
        audit = (self.root / "logs" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn('"label":"Save"', audit)
        self.assertTrue(redact({"label": "Save"})["label"]["redacted"])


if __name__ == "__main__":
    unittest.main()
