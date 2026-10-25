"""Phase 2 memory, schedule, API, and network-default regression tests."""
from __future__ import annotations
import json
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from primus.api import make_server
from primus.audit import redact
from primus.plugins import discover
from primus.scheduler import Notifier, Scheduler
from primus.security import SecurityError, guard_path
from primus.net import public_address
from primus.storage import Store
from primus.tray import _start_local_core

PROJECT = Path(__file__).resolve().parents[1]

class PhaseTwoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.root = Path(self.temp.name)
        (self.root / "plugins").mkdir()
        (self.root / "logs").mkdir()
        for name in ("memory.py", "schedule.py", "web.py", "google.py"):
            shutil.copy2(PROJECT / "plugins" / name, self.root / "plugins" / name)
        self.config = {"_root": self.root, "network_plugins_enabled": False,
                       "local_api": {"host": "127.0.0.1", "port": 8765},
                       "scheduler": {"poll_seconds": 1}}
        self.store = Store(self.root)
    def tearDown(self):
        self.temp.cleanup()

    def test_network_and_email_audit_fields_are_redacted(self):
        data = {"query": "private search", "url": "https://example.com/private",
                "subject": "private email", "snippet": "private preview", "summary": "private meeting"}
        result = redact(data)
        for key in data:
            self.assertTrue(result[key]["redacted"])
            self.assertNotIn(data[key], json.dumps(result))

    def test_approved_memory_crud_and_secret_rejection(self):
        with self.assertRaises(PermissionError):
            self.store.add_memory("fact", "Prefers dark mode")
        identifier = self.store.add_memory("preference", "Prefers dark mode", approved=True)
        self.assertEqual(self.store.list_memories()[0]["content"], "Prefers dark mode")
        self.store.edit_memory(identifier, "Prefers light mode", approved=True)
        self.assertEqual(self.store.list_memories()[0]["content"], "Prefers light mode")
        with self.assertRaises(ValueError):
            self.store.add_memory("fact", "My password is 123", approved=True)
        self.store.delete_memory(identifier, approved=True)
        self.assertEqual(self.store.list_memories(), [])

    def test_private_task_history(self):
        identifier = self.store.start_task("A private task")
        self.store.finish_task(identifier, "done", "A private result")
        row = self.store.list_tasks()[0]
        self.assertEqual(row["status"], "done")
        self.assertNotIn("private", json.dumps(row))
        self.assertEqual(len(row["request_sha256"]), 64)

    def test_reminder_executes_and_recurrence_moves_forward(self):
        due = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        identifier = self.store.add_schedule("reminder", "Stand up", due, repeat_seconds=60)
        with self.store.connect() as db:
            db.execute("UPDATE schedules SET due_utc=? WHERE id=?",
                       ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds"), identifier))
        seen = []
        scheduler = Scheduler(self.config, self.store, {}, notify=Notifier(lambda title, message: seen.append(message)))
        self.assertTrue(scheduler.run_once())
        self.assertEqual(seen, ["Stand up"])
        row = self.store.list_schedules()[0]
        self.assertEqual(row["status"], "pending")
        self.assertGreater(datetime.fromisoformat(row["due_utc"]), datetime.now(timezone.utc))

    def test_network_plugins_disabled(self):
        names = discover(self.root, network_enabled=False)
        self.assertIn("memory_list", names)
        self.assertNotIn("web_fetch", names)
        self.assertNotIn("gmail_today", names)

    def test_new_project_internals_are_guarded(self):
        for path in ("vendor/example.py", "temp/scratch.py", "requirements-phase2.txt"):
            with self.assertRaises(SecurityError):
                guard_path(path, self.config, write=True)

    def test_google_gmail_uses_one_token_and_local_day_bounds(self):
        self.config["network_plugins_enabled"] = True
        self.config["google"] = {"enabled": True, "client_id": "test-client"}
        plugin = discover(self.root, network_enabled=True)["gmail_today"]
        seen = []
        def fake_get(config, url, *, access_token=None):
            seen.append((url, access_token))
            if "format=metadata" in url:
                return {"payload": {"headers": [{"name": "Subject", "value": "Example"}]}, "snippet": "Preview"}
            return {"messages": [{"id": "a"}, {"id": "b"}]}
        fake_token = Mock(return_value="access")
        with patch.dict(plugin.run.__globals__, {"token": fake_token, "google_get": fake_get}):
            result = plugin.run(self.config, {})
        self.assertEqual(fake_token.call_count, 1)
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(access == "access" for _, access in seen))
        self.assertIn("after%3A", seen[0][0])
        self.assertEqual(len(result["messages"]), 2)

    def test_network_rejects_local_host(self):
        with self.assertRaises(ValueError):
            public_address("https://127.0.0.1/private")

    def test_api_rejects_missing_token(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.config["local_api"]["port"] = sock.getsockname()[1]
        with patch("primus.api.api_token", return_value="test-token"):
            server, _ = make_server(self.config)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{self.config['local_api']['port']}/health"
        try:
            with self.assertRaises(HTTPError) as unauthorized:
                urlopen(url, timeout=2)
            self.assertEqual(unauthorized.exception.code, 401)
            hostile = Request(url, headers={"Authorization": "Bearer test-token",
                                           "Origin": "https://untrusted.example"})
            with self.assertRaises(HTTPError) as forbidden:
                urlopen(hostile, timeout=2)
            self.assertEqual(forbidden.exception.code, 403)
            request = Request(url, headers={"Authorization": "Bearer test-token"})
            with urlopen(request, timeout=2) as response:
                self.assertTrue(json.load(response)["ok"])
            with patch("primus.api.api_token", return_value="test-token"), patch("primus.tray.api_token", return_value="test-token"):
                self.assertEqual(_start_local_core(self.config), (None, None, None))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

if __name__ == "__main__":
    unittest.main()
