"""Phase 1 behavior tests. All generated files stay under PRIMUS/temp."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from primus.agent import Agent
from primus.audit import append_audit
from primus.plugins import discover
from primus.security import SecurityError, guard_path, authorize

class ScriptedProvider:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.seen = []
    def chat(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        return self.messages.pop(0)

def call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}

class PhaseOneTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "temp").mkdir(exist_ok=True)
        self.workspace = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        (self.root / "logs").mkdir()
        (self.root / "data").mkdir()
        self.config = {"_root": self.root, "approved_folders": [], "max_steps": 5, "max_invalid_responses": 2}
        self.plugins = discover(ROOT)

    def test_path_guard_blocks_escape_and_secrets(self):
        with self.assertRaises(SecurityError):
            guard_path(str(self.root.parent.parent), self.config)
        with self.assertRaises(SecurityError):
            guard_path(".ssh/id_rsa", self.config)
        with self.assertRaises(SecurityError):
            guard_path(".env.production", self.config)
        with self.assertRaises(SecurityError):
            guard_path("secrets.json", self.config)
        with self.assertRaises(SecurityError):
            guard_path("plugins/evil.py", self.config, write=True)
        with self.assertRaises(SecurityError):
            guard_path("config.json", self.config, write=True)

    def test_approved_user_folder_aliases_keep_path_guards(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as base_name:
            base = Path(base_name)
            project = base / "Downloads" / "Primus"
            project.mkdir(parents=True)
            documents = base / "Documents"
            documents.mkdir()
            desktop = base / "Desktop"
            desktop.mkdir()
            config = {"_root": project, "approved_folders": [str(documents), str(desktop), str(project.parent)]}
            self.assertEqual(guard_path("Documents", config), documents)
            self.assertEqual(guard_path("documents/hello.py", config, write=True), documents / "hello.py")
            self.assertEqual(guard_path("Desktop/note.txt", config, write=True), desktop / "note.txt")
            self.assertEqual(guard_path("Downloads/Primus/outputs/x.txt", config, write=True), project / "outputs/x.txt")
            self.plugins["file_write"].run(config, {"path": "Documents/demo.py", "content": "print('ok')\n"})
            self.assertEqual((documents / "demo.py").read_text(), "print('ok')\n")
            self.assertEqual(self.plugins["file_list"].run(config, {"path": "Documents"})["path"], str(documents))
            with self.assertRaises(SecurityError):
                guard_path("Documents/../private.txt", config)
            with self.assertRaises(SecurityError):
                guard_path("Documents/.ssh/key.txt", config)
            with self.assertRaises(SecurityError):
                guard_path("Downloads/Primus/src/change.py", config, write=True)
            unapproved = {"_root": project, "approved_folders": []}
            self.assertEqual(guard_path("Documents/demo.py", unapproved), project / "Documents/demo.py")

    def test_create_file_end_to_end(self):
        provider = ScriptedProvider(call("file_write", {"path": "outputs/checklist.txt", "content": "one\ntwo\n"}), {"role": "assistant", "content": "Created and verified the checklist."})
        result = Agent(self.config, self.plugins, provider).run("Create a checklist")
        self.assertIn("verified", result)
        self.assertEqual((self.root / "outputs" / "checklist.txt").read_text(), "one\ntwo\n")
        audit = (self.root / "logs" / "audit.jsonl").read_text()
        self.assertIn("file_write", audit)
        self.assertNotIn("one\\ntwo", audit)
        self.assertIn("UNTRUSTED TOOL DATA", provider.seen[1][-1]["content"])

    def test_list_directory_end_to_end(self):
        (self.root / "sample.txt").write_text("sample")
        provider = ScriptedProvider(call("file_list", {"path": "."}), {"role": "assistant", "content": "sample.txt is present."})
        result = Agent(self.config, self.plugins, provider).run("List this directory")
        self.assertIn("sample.txt", result)
        self.assertIn("file_list", (self.root / "logs" / "audit.jsonl").read_text())

    def test_shell_inspection_end_to_end(self):
        provider = ScriptedProvider(call("shell_run", {"command": "python_version"}), {"role": "assistant", "content": "Python is available."})
        result = Agent(self.config, self.plugins, provider).run("Check Python")
        self.assertIn("Python", result)
        audit = [json.loads(line) for line in (self.root / "logs" / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(audit[0]["result"]["exit_code"], 0)

    def test_overwrite_requires_confirmation(self):
        (self.root / "existing.txt").write_text("old")
        provider = ScriptedProvider(call("file_overwrite", {"path": "existing.txt", "content": "new"}), {"role": "assistant", "content": "The overwrite was denied."})
        result = Agent(self.config, self.plugins, provider, confirm=lambda _: False).run("Replace file")
        self.assertIn("needs review", result)
        self.assertEqual((self.root / "existing.txt").read_text(), "old")

    def test_kill_switch_blocks_task(self):
        (self.root / "data" / "STOP").write_text("stop")
        provider = ScriptedProvider({"role": "assistant", "content": "Should not run"})
        with self.assertRaises(RuntimeError):
            Agent(self.config, self.plugins, provider).run("Anything")

    def test_permission_config_can_require_read_confirmation(self):
        self.config["permissions"] = {"read": "confirm", "write": "approved_folders", "destructive": "confirm", "external": "confirm"}
        with self.assertRaises(SecurityError):
            authorize(self.config, "READ", "file_list", {"path": "."}, "List directory", lambda _: False)

    def test_audit_redacts_content(self):
        append_audit(self.root, "file_write", {"path": "x", "content": "hidden"}, {"ok": True})
        audit = (self.root / "logs" / "audit.jsonl").read_text()
        self.assertNotIn("hidden", audit)
        self.assertIn("redacted", audit)

if __name__ == "__main__":
    unittest.main()
