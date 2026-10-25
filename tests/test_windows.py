"""Window actions must match the listed window and reach the consent gate."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from primus.agent import Agent
from primus.plugins import PluginError, discover, validate
from primus.windowing import Window, describe_action, describe_input, manage_window, resolve_window, send_window_input, split_work_area


class ScriptedProvider:
    def __init__(self, tool_call):
        self.messages = [tool_call, {"role": "assistant", "content": "Window action checked."}]
    def chat(self, messages, tools):
        return self.messages.pop(0)


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        (self.root / "data").mkdir()
        (self.root / "logs").mkdir()
        self.config = {"_root": self.root, "max_steps": 3, "max_invalid_responses": 2}
        self.plugins = discover(ROOT)
        self.window = Window("0x1234", 42, "Example - Notepad", "notepad.exe")
        self.args = {"action": "close", "id": self.window.id,
                     "pid": self.window.pid, "title": self.window.title}

    def test_stale_window_is_rejected_before_consent(self):
        with patch("primus.windowing.list_windows", return_value=[self.window]):
            self.assertEqual(resolve_window(self.args), self.window)
            self.assertIn("unsaved work", describe_action(self.args))
            with self.assertRaisesRegex(ValueError, "changed"):
                resolve_window({**self.args, "title": "Different title"})

    def test_denied_close_never_reaches_native_action(self):
        provider = ScriptedProvider({"role": "assistant", "tool_calls": [
            {"function": {"name": "window_manage", "arguments": self.args}}]})
        prompts = []
        plugins = dict(self.plugins)
        plugins["window_manage"] = replace(plugins["window_manage"],
                                           run=lambda *_: self.fail("Action ran without consent"))
        with patch("primus.windowing.list_windows", return_value=[self.window]):
            answer = Agent(self.config, plugins, provider,
                           confirm=lambda prompt: prompts.append(prompt) or False).run("Close the window")
        self.assertIn("needs review", answer)
        self.assertEqual(len(prompts), 1)
        self.assertIn("unsaved work", prompts[0])
        audit = [json.loads(line) for line in (self.root / "logs" / "audit.jsonl").read_text().splitlines()]
        self.assertFalse(audit[0]["result"]["ok"])

    def test_changed_window_during_approval_never_runs(self):
        provider = ScriptedProvider({"role": "assistant", "tool_calls": [
            {"function": {"name": "window_manage", "arguments": self.args}}]})
        snapshots = [[self.window], [Window(self.window.id, self.window.pid, "Other title", self.window.application)]]
        prompts = []
        with patch("primus.windowing.list_windows", side_effect=snapshots):
            answer = Agent(self.config, self.plugins, provider,
                           confirm=lambda prompt: prompts.append(prompt) or True).run("Close the window")
        self.assertIn("needs review", answer)
        self.assertEqual(len(prompts), 1)
        self.assertIn("Window changed", answer)

    def test_approved_focus_uses_window_plugin_and_audit(self):
        args = {**self.args, "action": "focus"}
        provider = ScriptedProvider({"role": "assistant", "tool_calls": [
            {"function": {"name": "window_manage", "arguments": args}}]})
        prompts = []
        calls = []
        plugins = dict(self.plugins)
        plugins["window_manage"] = replace(plugins["window_manage"], run=lambda config, value:
                                           calls.append(value.copy()) or {"action": "focus", "verified": True})
        with patch("primus.windowing.list_windows", return_value=[self.window]):
            answer = Agent(self.config, plugins, provider,
                           confirm=lambda prompt: prompts.append(prompt) or True).run("Focus the window")
        self.assertEqual(answer, "Window action checked.")
        self.assertEqual(calls, [args])
        self.assertIn("Focus window", prompts[0])
        audit = [json.loads(line) for line in (self.root / "logs" / "audit.jsonl").read_text().splitlines()]
        self.assertTrue(audit[0]["result"]["verified"])

    def test_window_title_is_redacted_from_audit(self):
        from primus.audit import append_audit
        append_audit(self.root, "window_list", {}, {"windows": [self.window.public()]})
        data = (self.root / "logs" / "audit.jsonl").read_text()
        self.assertNotIn(self.window.title, data)
        self.assertIn("redacted", data)

    def test_layout_uses_monitor_work_area_including_negative_coordinates(self):
        self.assertEqual(split_work_area((-1920, 40, 0, 1040), "snap_left"),
                         (-1920, 40, 960, 1000))
        self.assertEqual(split_work_area((-1920, 40, 0, 1040), "snap_right"),
                         (-960, 40, 960, 1000))
        self.assertEqual(split_work_area((0, 0, 1919, 1080), "snap_right"),
                         (959, 0, 960, 1080))
        self.assertEqual(split_work_area((0, 0, 1919, 1080), "maximize"),
                         (0, 0, 1919, 1080))

    def test_layout_preview_is_pinned_and_changed_monitor_is_rejected(self):
        args = {**self.args, "action": "snap_left"}
        with patch("primus.windowing.list_windows", return_value=[self.window]):
            with patch("primus.windowing._layout_target", side_effect=[(0, 0, 800, 900), (0, 0, 700, 900)]):
                preview = describe_action(args)
                self.assertIn("width=800", preview)
                self.assertEqual(args["_expected_layout"], [0, 0, 800, 900])
                with self.assertRaisesRegex(ValueError, "Monitor layout changed"):
                    manage_window(self.config, args)

    def test_model_cannot_supply_internal_layout_snapshot(self):
        schema = self.plugins["window_manage"].input_schema
        validate(schema, {**self.args, "action": "snap_left"})
        with self.assertRaises(PluginError):
            validate(schema, {**self.args, "action": "snap_left",
                              "_expected_layout": [0, 0, 800, 900]})

    def test_window_input_needs_focus_and_reports_unverified_result(self):
        args = {**self.args, "action": "type", "text": "hello"}
        with patch("primus.windowing.list_windows", return_value=[self.window]):
            self.assertIn("hello", describe_input(args))
            with patch("primus.windowing._focus_window", return_value=False):
                with patch("primus.windowing._send_keyboard") as sender:
                    with self.assertRaisesRegex(RuntimeError, "no input was sent"):
                        send_window_input(self.config, args)
                    sender.assert_not_called()
            with patch("primus.windowing._focus_window", return_value=True):
                with patch("primus.windowing._foreground_window_id", return_value=int(self.window.id, 16)):
                    with patch("primus.windowing._send_keyboard") as sender:
                        result = send_window_input(self.config, args)
                    sender.assert_called_once_with(args)
        self.assertTrue(result["sent"])
        self.assertFalse(result["verified"])

    def test_window_input_needs_consent_and_redacts_text(self):
        args = {**self.args, "action": "type", "text": "private draft"}
        provider = ScriptedProvider({"role": "assistant", "tool_calls": [
            {"function": {"name": "window_input", "arguments": args}}]})
        plugins = dict(self.plugins)
        plugins["window_input"] = replace(plugins["window_input"],
                                          run=lambda *_: self.fail("Typed without consent"))
        prompts = []
        with patch("primus.windowing.list_windows", return_value=[self.window]):
            answer = Agent(self.config, plugins, provider,
                           confirm=lambda prompt: prompts.append(prompt) or False).run("Type into the window")
        self.assertIn("needs review", answer)
        self.assertEqual(len(prompts), 1)
        self.assertIn("private draft", prompts[0])
        audit = (self.root / "logs" / "audit.jsonl").read_text()
        self.assertNotIn("private draft", audit)
        self.assertNotIn(self.window.title, audit)


if __name__ == "__main__":
    unittest.main()
