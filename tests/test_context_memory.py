"""Persistent brief context without saving task text or voice transcripts."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from primus.context_memory import context_from_tool
from primus.plugins import Plugin
from primus.service import TaskRunner
from primus.storage import Store
from primus.audit import redact


class Provider:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.systems = []
    def chat(self, messages, _tools):
        self.systems.append(messages[0]["content"])
        return self.messages.pop(0)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "logs").mkdir()
        (self.root / "data").mkdir()
        self.config = {"_root": self.root, "memory": {"task_context_enabled": True,
                                                     "context_retention_days": 30},
                       "permissions": {"write": "approved_folders"},
                       "max_steps": 4, "max_invalid_responses": 2}
        self.store = Store(self.root)
        self.apps = {"app_open": Plugin("app_open", "Open a fixed local app", {
            "type": "object", "properties": {"name": {"type": "string", "enum": ["chrome"]}},
            "required": ["name"], "additionalProperties": False,
        }, "WRITE", False, lambda _config, _args: {"window_verified": True})}

    def test_context_survives_new_runner_without_task_text(self):
        task = "Please open Chrome and then report"
        call = {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "app_open", "arguments": {"name": "chrome"}}}]}
        first = Provider(call, {"role": "assistant", "content": "Chrome opened."})
        self.assertEqual(TaskRunner(self.config, self.apps, self.store, provider=first).run(task),
                         "Chrome opened.")
        self.assertEqual(Store(self.root).get_context(),
                         {"last_app": "chrome", "last_target": "app:chrome"})
        second = Provider({"role": "assistant", "content": "Chrome was the last app."})
        answer = TaskRunner(self.config, self.apps, Store(self.root), provider=second).run("Which app was that?")
        self.assertIn("Chrome", answer)
        self.assertIn('"last_app": "chrome"', second.systems[0])
        self.assertNotIn(task, (self.root / "data" / "primus.sqlite3").read_bytes().decode("utf-8", "ignore"))

    def test_fast_app_request_updates_context(self):
        provider = Provider()
        answer = TaskRunner(self.config, self.apps, self.store, provider=provider).run("Open Google Chrome")
        self.assertIn("Opened", answer)
        self.assertEqual(self.store.get_context()["last_app"], "chrome")
        self.assertEqual(provider.systems, [])

    def test_only_safe_references_are_extracted(self):
        result = context_from_tool("file_read", {"path": "outputs/note.txt"},
                                   {"path": str(self.root / "outputs" / "note.txt"),
                                    "content": "private transcript"}, self.config)
        path = "outputs\\note.txt" if sys.platform == "win32" else "outputs/note.txt"
        self.assertEqual(result, {"last_file": path, "last_target": f"file:{path}"})
        self.assertNotIn("private transcript", json.dumps(result))
        blocked = context_from_tool("file_read", {},
                                    {"path": str(self.root / "data" / "token.json")}, self.config)
        self.assertEqual(blocked, {})
        self.assertEqual(context_from_tool("app_open", {"name": "chrome"},
                                           {"window_verified": False}, self.config), {})

    def test_clear_requires_approval_and_old_context_expires(self):
        self.store.update_context({"last_app": "chrome"})
        with self.store.connect() as db:
            db.execute("UPDATE task_context SET updated_utc='2020-01-01T00:00:00+00:00'")
        self.assertEqual(self.store.get_context(30), {})
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM task_context").fetchone()[0], 0)
        self.store.update_context({"last_app": "chrome"})
        with self.assertRaises(PermissionError):
            self.store.clear_context()
        self.store.clear_context(approved=True)
        self.assertEqual(self.store.get_context(), {})
        with self.assertRaises(ValueError):
            self.store.update_context({"raw_transcript": "hello"})

    def test_clear_plugin_pins_reviewed_context_without_audit_leak(self):
        from importlib.util import module_from_spec, spec_from_file_location
        spec = spec_from_file_location("memory_plugin", PROJECT / "plugins" / "memory.py")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        self.store.update_context({"last_file": "outputs/private-note.txt"})
        arguments = {}
        summary = module.clear_context_summary(self.config, arguments)
        self.assertIn("outputs/private-note.txt", summary)
        self.assertNotIn("private-note", json.dumps(arguments))
        self.assertNotIn("private-note", json.dumps(redact(arguments)))
        self.assertNotIn("private-note", json.dumps(redact({"context": self.store.get_context()})))
        self.store.update_context({"last_app": "chrome"})
        with self.assertRaisesRegex(ValueError, "changed during approval"):
            module.clear_context(self.config, arguments)
        self.assertIn("last_file", self.store.get_context())
        module.clear_context_summary(self.config, arguments)
        self.assertEqual(module.clear_context(self.config, arguments), {"cleared": True})
        self.assertEqual(self.store.get_context(), {})


if __name__ == "__main__":
    unittest.main()
