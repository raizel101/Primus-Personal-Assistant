"""Parallel coordinator, permission isolation, and task lineage."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from primus.plugins import Plugin
from primus.api import make_server
from primus.storage import Store
from primus.team import TeamRunner, read_only_plugins, team_request


class WorkerModel:
    def __init__(self, tool="probe"):
        self.tool = tool

    def chat(self, messages, tools):
        if any(message.get("role") == "tool" for message in messages):
            return {"role": "assistant", "content": "Inspection completed."}
        return {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": self.tool, "arguments": {}}}]}


class PlannerModel:
    def __init__(self, responses):
        self.responses = iter(responses)

    def chat(self, messages, tools):
        return {"role": "assistant", "content": next(self.responses)}


class TeamTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "temp").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "logs").mkdir()
        self.store = Store(self.root)
        self.config = {"_root": self.root, "approved_folders": [], "max_steps": 3,
                       "max_invalid_responses": 2, "memory": {"task_context_enabled": True},
                       "agents": {"max_parallel": 2, "max_subtasks": 3}}

    def test_independent_agents_overlap_and_history_links_to_parent(self):
        barrier = threading.Barrier(2)
        overlap = []

        def probe(_config, _args):
            barrier.wait(timeout=2)
            overlap.append(time.monotonic())
            return {"verified": True}

        plugins = {"probe": Plugin("probe", "Inspect locally", {"type": "object", "properties": {},
                          "additionalProperties": False}, "READ", False, probe)}
        team = TeamRunner(self.config, plugins, self.store,
                          worker_provider_factory=WorkerModel)
        answer = team.run(tasks=["Inspect first item", "Inspect second item"])
        self.assertIn("Agent 1: Inspection completed.", answer)
        self.assertIn("Agent 2: Inspection completed.", answer)
        self.assertEqual(len(overlap), 2)
        history = self.store.list_tasks()
        parent = next(row for row in history if row["agent_label"] == "coordinator")
        children = [row for row in history if row["parent_task_id"] == parent["id"]]
        self.assertEqual({row["agent_label"] for row in children}, {"agent-1", "agent-2"})
        self.assertTrue(all(row["status"] == "done" for row in history))
        audit = (self.root / "logs" / "audit.jsonl").read_text()
        self.assertIn('"agent":"agent-1"', audit)
        self.assertIn('"agent":"agent-2"', audit)
        self.assertNotIn("Inspect first item", audit)

    def test_parallel_workers_cannot_call_write_plugins(self):
        writes = []
        plugins = {
            "probe": Plugin("probe", "Inspect", {"type": "object", "properties": {},
                           "additionalProperties": False}, "READ", False, lambda *_: {"ok": True}),
            "file_write": Plugin("file_write", "Write", {"type": "object", "properties": {},
                                "additionalProperties": False}, "WRITE", False,
                                 lambda *_: writes.append(1) or {"ok": True}),
        }
        self.assertEqual(set(read_only_plugins(plugins)), {"probe"})
        team = TeamRunner(self.config, plugins, self.store,
                          worker_provider_factory=lambda: WorkerModel("file_write"))
        answer = team.run(tasks=["Write first", "Write second"])
        self.assertTrue(answer.startswith("Task needs review"))
        self.assertEqual(writes, [])
        self.assertEqual(next(row for row in self.store.list_tasks()
                              if row["agent_label"] == "coordinator")["status"], "failed")

    def test_planner_retries_bad_json_and_rejects_unsplittable_task(self):
        planner = PlannerModel(["not json", json.dumps({"tasks": ["Inspect A", "Inspect B"]})])
        team = TeamRunner(self.config, {}, self.store, planner=planner)
        self.assertEqual(team._plan("Compare two items"), ["Inspect A", "Inspect B"])
        self.assertEqual(TeamRunner(self.config, {}, self.store,
                         planner=PlannerModel(['{"tasks":[]}'])).run("Change a setting").split(";")[0],
                         "Task needs review")
        self.assertEqual(TeamRunner(self.config, {}, self.store,
                         planner=PlannerModel([]))._plan("Delete all files"), [])

    def test_kill_switch_prevents_team_start(self):
        (self.root / "data" / "STOP").write_text("stop")
        with self.assertRaisesRegex(RuntimeError, "kill switch"):
            TeamRunner(self.config, {}, self.store).run(tasks=["A", "B"])
        self.assertEqual(self.store.list_tasks(), [])

    def test_existing_task_history_schema_migrates(self):
        legacy_root = self.root / "legacycase"
        (legacy_root / "data").mkdir(parents=True)
        legacy = legacy_root / "data" / "primus.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("CREATE TABLE task_history (id TEXT PRIMARY KEY, request_sha256 TEXT NOT NULL, "
                       "request_bytes INTEGER NOT NULL, status TEXT NOT NULL, result_sha256 TEXT, "
                       "started_utc TEXT NOT NULL, finished_utc TEXT)")
        db.close()
        migrated = Store(legacy_root)
        row_id = migrated.start_task("test", parent_task_id="parent", agent_label="agent-1")
        self.assertEqual(migrated.list_tasks()[0]["id"], row_id)
        self.assertEqual(migrated.list_tasks()[0]["parent_task_id"], "parent")

    def test_team_prefix_is_explicit(self):
        self.assertEqual(team_request("Team, inspect outputs and reminders"), "inspect outputs and reminders")
        self.assertIsNone(team_request("Inspect outputs and reminders"))

    def test_local_api_routes_explicit_and_spoken_team_requests(self):
        seen = []

        class FakeTeam:
            def __init__(self, *_args):
                pass

            def run(self, request=None, *, tasks=None):
                seen.append((request, tasks))
                return "team completed"

        (self.root / "plugins").mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = {**self.config, "network_plugins_enabled": False,
                  "local_api": {"host": "127.0.0.1", "port": port},
                  "scheduler": {"poll_seconds": 1}}
        with patch("primus.api.api_token", return_value="test-token"), patch("primus.api.TeamRunner", FakeTeam):
            server, _ = make_server(config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def post(path, body, token="test-token"):
                    request = Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                      headers={"Authorization": "Bearer " + token,
                                               "Content-Type": "application/json"}, method="POST")
                    with urlopen(request, timeout=2) as response:
                        return json.load(response)["result"]

                self.assertEqual(post("/teams", {"tasks": ["A", "B"]}), "team completed")
                self.assertEqual(post("/tasks", {"task": "Team: inspect A and B"}), "team completed")
                self.assertEqual(seen, [(None, ["A", "B"]), ("inspect A and B", None)])
                with self.assertRaises(HTTPError) as denied:
                    post("/teams", {"tasks": ["A", "B"]}, token="bad")
                self.assertEqual(denied.exception.code, 401)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
