"""Mission lineage, scoped tools, independent evidence, cancellation, and browser auth."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "vendor"))
sys.path.insert(0, str(PROJECT / "src"))
from primus.api import make_server
from primus.dashboard import DashboardSessions
from primus.missions import Mission, MissionManager, TERMINAL, scoped_plugins
from primus.plugins import discover
from primus.storage import Store

PLAN = {"title": "Build settings", "steps": ["Create requested JSON", "Inspect actual file"], "acceptance": ["JSON has project PRIMUS"]}
REVIEW = {"verdict": "pass", "summary": "Actual file meets the requested criteria.", "issues": []}


class FakeModel:
    def __init__(self, role, filename="settings.json", content='{"project":"PRIMUS"}'):
        self.role, self.filename, self.content = role, filename, content
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.role == "planner":
            return {"role": "assistant", "content": json.dumps(PLAN)}
        if self.role == "verifier":
            return {"role": "assistant", "content": json.dumps(REVIEW)}
        if self.calls == 1:
            return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "file_write", "arguments": {"path": self.filename, "content": self.content}}}]}
        return {"role": "assistant", "content": "Created the requested file."}


class MissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("logs", "plugins"):
            (self.root / name).mkdir()
        self.config = {"_root": self.root, "approved_folders": [], "missions": {"enabled": True, "max_steps": 4},
                       "permissions": {"read": "auto", "write": "approved_folders"}, "max_invalid_responses": 2,
                       "model": {"name": "test", "endpoint": "http://127.0.0.1:11434/api/chat"},
                       "network_plugins_enabled": False, "scheduler": {"poll_seconds": 1}}
        self.store = Store(self.root)
        self.plugins = discover(PROJECT)

    def manager(self, factory=FakeModel):
        manager = MissionManager(self.config, self.plugins, self.store, provider_factory=factory)
        self.addCleanup(manager.close)
        return manager

    def wait_finished(self, manager, identifier):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = manager.get(identifier)
            if value["status"] in TERMINAL:
                return value
            time.sleep(.01)
        self.fail("Mission did not stop")

    def test_full_pipeline_uses_real_files_and_links_agent_history(self):
        manager = self.manager()
        created = manager.start("Create settings.json for project PRIMUS")
        mission = self.wait_finished(manager, created["id"])
        self.assertEqual(mission["status"], "completed", mission)
        self.assertTrue(all(v["status"] == "completed" for v in mission["roles"].values()))
        self.assertEqual(mission["roles"]["verifier"]["attempts"], 1)
        artifact = mission["artifacts"][0]
        self.assertEqual(json.loads(Path(artifact["path"]).read_text()), {"project": "PRIMUS"})
        self.assertEqual(mission["checks"][0]["check"], "json_syntax")
        self.assertIn("not tested", mission["summary"])
        self.assertIn("tool_finished", {event["kind"] for event in mission["events"]})
        children = [t for t in self.store.list_tasks() if t["parent_task_id"] == mission["task_id"]]
        self.assertEqual({t["agent_label"] for t in children}, {"planner", "coder", "verifier"})
        self.assertNotIn("Create settings.json", self.store.path.read_bytes().decode("utf-8", "ignore"))

    def test_model_pass_cannot_override_actual_syntax_failure(self):
        manager = self.manager(lambda role: FakeModel(role, "broken.py", "def broken(:"))
        mission = self.wait_finished(manager, manager.start("Create Python code")["id"])
        self.assertEqual(mission["status"], "needs_review")
        self.assertFalse(mission["checks"][0]["passed"])
        self.assertEqual(mission["roles"]["coder"]["attempts"], 2)

    def test_automatic_mode_runs_verifier_without_manual_approval(self):
        self.config["permissions"]["auto_approve"] = True
        self.config["script_execution"] = {"enabled": True}
        manager = self.manager(lambda role: FakeModel(role, "main.py", "print('auto-output')"))
        executed = []
        manager.plugins = {**self.plugins, "script_run": replace(self.plugins["script_run"], run=lambda _c, args:
            executed.append(args.copy()) or {"verified": True, "exit_code": 0,
                "stdout": "auto-output\n", "stderr": "", "output_dir": "sandbox-output"})}
        identifier = manager.start("Create main.py")["id"]
        self.wait_finished(manager, identifier)
        def no_manual_consent(_prompt):
            raise AssertionError("Automatic mode must not request a manual decision")
        manager.items[identifier].confirm = no_manual_consent
        manager.run_artifact(identifier, 0)
        state = self.wait_finished(manager, identifier)
        self.assertEqual(len(executed), 1)
        self.assertIsNone(state.get("pending"))
        run = state["executions"][-1]
        self.assertEqual(run["approval_status"], "auto_approved")
        self.assertEqual(run["stdout"], "auto-output\n")
        self.assertEqual(run["status"], "completed")

    def test_recursive_repair_uses_full_overwrite_and_waits_for_exact_consent(self):
        original = "def count(n):\n    if n < 0:\n        return\n    print(n)\n    count(n-1)\n\ncount(5)\n"
        corrected = "def count(n):\n    if n < 0:\n        return\n    count(n-1)\n    print(n)\n\ncount(5)\n"
        counts = {"coder": 0, "verifier": 0}
        class RepairModel(FakeModel):
            def __init__(self, role):
                super().__init__(role, "main.py", original)
                if role in counts:
                    counts[role] += 1
                self.repair = role == "coder" and counts["coder"] == 2

            def chat(self, messages, tools):
                if self.role == "verifier" and counts["verifier"] == 1:
                    return {"role": "assistant", "content": json.dumps({"verdict": "needs_review", "summary": "Print follows recursion for ascending order.", "issues": ["Move print after the recursive call."]})}
                if self.repair:
                    if any(t["function"]["name"] == "code_patch" for t in tools):
                        raise AssertionError("Repair must not advertise snippet patches")
                    self.calls += 1
                    if self.calls == 1:
                        return {"role": "assistant", "tool_calls": [{"function": {"name": "file_read", "arguments": {"path": "main.py"}}}]}
                    if self.calls == 2:
                        return {"role": "assistant", "tool_calls": [{"function": {"name": "file_overwrite", "arguments": {"path": "main.py", "content": corrected}}}]}
                    return {"role": "assistant", "content": "Repair applied."}
                return super().chat(messages, tools)
        manager = self.manager(RepairModel)
        identifier = manager.start("Create a recursive function from 0 to 5")["id"]
        deadline = time.monotonic() + 3
        pending = None
        while not pending and time.monotonic() < deadline:
            state = manager.get(identifier)
            pending = state.get("pending")
            time.sleep(.01)
        self.assertIsNotNone(pending, state)
        path = Path(state["workspace"]) / "main.py"
        self.assertEqual(path.read_text(), original)
        self.assertIn("file_overwrite", pending["prompt"])
        self.assertIn("SHA-256", pending["prompt"])
        manager.approve(identifier, pending["id"], True)
        final = self.wait_finished(manager, identifier)
        self.assertEqual(final["status"], "completed", final)
        self.assertEqual(path.read_text(), corrected)
        self.assertEqual(final["roles"]["coder"]["attempts"], 2)

    def test_repeated_existing_file_write_reports_actionable_stopping_reason(self):
        class RepeatingModel(FakeModel):
            def chat(self, messages, tools):
                if self.role == "coder":
                    return {"role": "assistant", "tool_calls": [{"function": {"name": "file_write", "arguments": {"path": "settings.json", "content": '{"project":"PRIMUS"}'}}}]}
                return super().chat(messages, tools)
        manager = self.manager(RepeatingModel)
        state = self.wait_finished(manager, manager.start("Create JSON")["id"])
        self.assertEqual(state["status"], "failed")
        self.assertIn("Coder stopped", state["summary"])
        self.assertIn("File already exists", state["summary"])
        self.assertIn("exact user approval", state["summary"])
        self.assertEqual(json.loads((Path(state["workspace"]) / "settings.json").read_text()), {"project": "PRIMUS"})

    def test_generated_runtime_requires_consent_and_preserves_actual_output(self):
        self.config["script_execution"] = {"enabled": True}
        for approved, exit_code in ((False, 0), (True, 0), (True, 7)):
            with self.subTest(approved=approved, exit_code=exit_code):
                executed = []
                class NoRepairModel(FakeModel):
                    def chat(self, messages, tools):
                        if self.role == "coder" and "UNTRUSTED ACTUAL FAILED EXECUTION" in messages[-1].get("content", ""):
                            return {"role": "assistant", "content": "No source correction available."}
                        return super().chat(messages, tools)
                manager = self.manager(lambda role: NoRepairModel(role, "main.py", "print('runtime-output')"))
                manager.plugins = {**self.plugins, "script_run": replace(self.plugins["script_run"], run=lambda _c, args: executed.append(args.copy()) or
                    {"verified": exit_code == 0, "exit_code": exit_code, "stdout": "runtime-output\n", "stderr": "actual-error\n" if exit_code else "", "output_dir": "sandbox-output", "stdout_truncated": True})}
                identifier = manager.start("Create a Python file")["id"]
                self.wait_finished(manager, identifier)
                manager.run_artifact(identifier, 0)
                deadline = time.monotonic() + 3
                pending = None
                while not pending and time.monotonic() < deadline:
                    pending = manager.get(identifier).get("pending")
                    time.sleep(.01)
                self.assertIsNotNone(pending)
                self.assertIn("offline filesystem sandbox", pending["prompt"])
                self.assertEqual(executed, [])
                with self.assertRaisesRegex(ValueError, "active mission"):
                    manager.run_artifact(identifier, 0)
                manager.approve(identifier, pending["id"], approved)
                state = self.wait_finished(manager, identifier)
                run = state["executions"][-1]
                self.assertEqual(len(executed), int(approved))
                self.assertEqual(run["status"], "not_executed" if not approved else "completed" if exit_code == 0 else "failed")
                self.assertEqual(state["status"], "completed" if approved and exit_code == 0 else "needs_review")
                self.assertEqual(run["stdout"], "runtime-output\n" if approved else "")
                self.assertEqual(run["stderr"], "actual-error\n" if approved and exit_code else "")
                self.assertEqual(run["exit_code"], exit_code if approved else None)
                self.assertEqual(run["stdout_truncated"], approved)
                self.assertNotIn("runtime-output", self.store.path.read_bytes().decode("utf-8", "ignore"))
                audit=(self.root / "logs" / "audit.jsonl").read_text()
                self.assertNotIn("runtime-output", audit)
                self.assertNotIn("actual-error", audit)
                manager.close()

    def test_runtime_failure_repairs_then_requires_fresh_run_approval_and_stops_at_limit(self):
        self.config["script_execution"] = {"enabled": True}
        original = "print(missing_value)\n"
        corrected = "for n in range(6):\n    print(n)\n"
        for repair_approved, rerun_approved, second_exit in ((False, True, 0), (True, False, 0), (True, True, 0), (True, True, 1)):
            with self.subTest(repair=repair_approved, rerun=rerun_approved, second_exit=second_exit):
                executed, repair_prompts = [], []
                class RuntimeModel(FakeModel):
                    def __init__(self, role):
                        super().__init__(role, "main.py", original)
                    def chat(self, messages, tools):
                        prompt = messages[1].get("content", "")
                        if self.role == "coder" and "UNTRUSTED ACTUAL FAILED EXECUTION" in prompt:
                            self.calls += 1
                            repair_prompts.append(prompt)
                            if self.calls == 1:
                                return {"role": "assistant", "tool_calls": [{"function": {"name": "file_read", "arguments": {"path": "main.py"}}}]}
                            if self.calls == 2:
                                return {"role": "assistant", "tool_calls": [{"function": {"name": "file_overwrite", "arguments": {"path": "main.py", "content": corrected}}}]}
                            return {"role": "assistant", "content": "Applied correction; not executed."}
                        return super().chat(messages, tools)
                def run(_c, args):
                    executed.append(args.copy())
                    exit_code = 1 if len(executed) == 1 else second_exit
                    return {"verified": exit_code == 0, "exit_code": exit_code,
                            "stdout": "0\n1\n2\n3\n4\n5\n" if exit_code == 0 else "before-failure\n",
                            "stderr": "Traceback: NameError: missing_value is not defined\n" if exit_code else "", "output_dir": "sandbox-output"}
                manager = self.manager(RuntimeModel)
                manager.plugins = {**self.plugins, "script_run": replace(self.plugins["script_run"], run=run)}
                identifier = manager.start("Create a Python function")["id"]
                state = self.wait_finished(manager, identifier)
                path = Path(state["workspace"]) / "main.py"
                manager.run_artifact(identifier, 0)
                def pending():
                    deadline = time.monotonic() + 4
                    while time.monotonic() < deadline:
                        state = manager.get(identifier)
                        if state.get("pending"):
                            return state["pending"]
                        time.sleep(.01)
                    self.fail("Expected a fresh exact approval: " + json.dumps(state))
                initial = pending()
                manager.approve(identifier, initial["id"], True)
                repair = pending()
                # Avoid reading the just-resolved initial approval during the handoff.
                if repair["id"] == initial["id"]:
                    time.sleep(.03); repair = pending()
                self.assertEqual(repair["role"], "coder")
                self.assertIn("file_overwrite", repair["prompt"])
                self.assertEqual(len(executed), 1)
                self.assertEqual(path.read_text(), original)
                self.assertIn("NameError", repair_prompts[0])
                self.assertIn("before-failure", repair_prompts[0])
                manager.approve(identifier, repair["id"], repair_approved)
                if repair_approved:
                    rerun = pending()
                    if rerun["id"] == repair["id"]:
                        time.sleep(.03); rerun = pending()
                    self.assertEqual(rerun["role"], "verifier")
                    self.assertNotEqual(initial["id"], rerun["id"])
                    self.assertEqual(path.read_text(), corrected)
                    self.assertEqual(len(executed), 1)
                    manager.approve(identifier, rerun["id"], rerun_approved)
                state = self.wait_finished(manager, identifier)
                self.assertEqual(state["status"], "completed" if repair_approved and rerun_approved and second_exit == 0 else "needs_review", state)
                self.assertEqual(len(executed), 2 if repair_approved and rerun_approved else 1)
                self.assertEqual(state["runtime_repairs"]["used"], 1)
                self.assertEqual(state["executions"][0]["exit_code"], 1)
                self.assertIsNone(state["pending"])
                if repair_approved and rerun_approved:
                    self.assertNotEqual(executed[0]["_expected_hashes"], executed[1]["_expected_hashes"])
                if not repair_approved:
                    self.assertEqual(path.read_text(), original)
                    self.assertEqual(state["roles"]["coder"]["status"], "needs_review")
                manager.close()

    def test_runtime_repair_does_not_handle_infrastructure_or_change_other_files(self):
        manager = self.manager()
        mission = Mission(manager, uuid.uuid4().hex, "test", False)
        base = {"path": str(mission.workspace / "main.py"), "status": "failed", "error": "", "exit_code": 1, "stderr": "bwrap: namespace unavailable"}
        self.assertFalse(mission.repairable_execution(base))
        self.assertFalse(mission.repairable_execution({**base, "stderr": "Traceback", "exit_code": 137}))
        self.assertTrue(mission.repairable_execution({**base, "stderr": "Traceback: NameError"}))
        self.assertFalse(mission.repairable_execution({**base, "path": str(mission.workspace / "verify.py"), "stderr": "AssertionError"}))
        tools = scoped_plugins(self.plugins, self.config, mission.workspace, writable_paths={mission.workspace / "main.py"})
        with self.assertRaisesRegex(PermissionError, "only the failed generated script"):
            tools["file_write"].run(self.config, {"path": "other.py", "content": "pass"})
        project_tools = scoped_plugins(self.plugins, self.config, mission.workspace, protect_tests=True)
        for path in ("verify.py", "tests/check.py", "Tests/check.py", "test_values.py"):
            with self.assertRaisesRegex(PermissionError, "independent verification"):
                project_tools["file_write"].run(self.config, {"path": path, "content": "pass"})
        with self.assertRaisesRegex(PermissionError, "own project workspace"):
            project_tools["script_run"].summary(self.config, {"path": "main.py", "project_root": str(self.root)})

    def test_project_repair_updates_helpers_or_multiple_files_before_fresh_project_approval(self):
        self.config["script_execution"] = {"enabled": True}
        entry = "from helper import average\nprint(average([]))\n"
        fixed_entry = "from helper import average\nif __name__ == '__main__':\n    print(average([]))\n"
        helper = "def average(values):\n    return sum(values)/len(values)\n"
        fixed_helper = "def average(values):\n    return sum(values)/len(values) if values else 0.0\n"
        for edit_entry in (False, True):
            with self.subTest(edit_entry=edit_entry):
                executed = []
                class ProjectModel(FakeModel):
                    def chat(self, messages, tools):
                        if self.role != "coder": return super().chat(messages, tools)
                        repair = "UNTRUSTED ACTUAL FAILED EXECUTION" in messages[1].get("content", "")
                        self.calls += 1
                        edits = [("helper.py", fixed_helper)] + ([("main.py", fixed_entry)] if edit_entry else []) if repair else [("main.py", entry), ("helper.py", helper)]
                        if self.calls <= len(edits):
                            path, content = edits[self.calls-1]
                            return {"role": "assistant", "tool_calls": [{"function": {"name": "file_overwrite" if repair else "file_write", "arguments": {"path": path, "content": content}}}]}
                        return {"role": "assistant", "content": "Sources ready; not executed."}
                def run(_c, args):
                    executed.append(args.copy())
                    success = len(executed) > 1
                    return {"verified": success, "exit_code": 0 if success else 1, "stdout": "0.0\n" if success else "",
                            "stderr": "" if success else "ZeroDivisionError in /work/project/helper.py", "output_dir": "project-output"}
                manager = self.manager(ProjectModel)
                manager.plugins = {**self.plugins, "script_run": replace(self.plugins["script_run"], run=run)}
                identifier = manager.start("Build a Python project")["id"]
                state = self.wait_finished(manager, identifier)
                index = next(i for i, a in enumerate(state["artifacts"]) if a["name"] == "main.py")
                manager.run_artifact(identifier, index)
                def pending(after=None):
                    deadline = time.monotonic()+4
                    while time.monotonic()<deadline:
                        value = manager.get(identifier).get("pending")
                        if value and value["id"] != after: return value
                        time.sleep(.01)
                    self.fail("Expected next project approval")
                approval = pending()
                self.assertIn("helper.py", approval["prompt"])
                manager.approve(identifier, approval["id"], True)
                for _ in range(2 if edit_entry else 1):
                    approval = pending(approval["id"])
                    self.assertEqual(approval["role"], "coder")
                    self.assertEqual(len(executed), 1)
                    manager.approve(identifier, approval["id"], True)
                approval = pending(approval["id"])
                self.assertEqual(approval["role"], "verifier")
                self.assertIn("helper.py", approval["prompt"])
                self.assertEqual(len(executed), 1)
                manager.approve(identifier, approval["id"], True)
                final = self.wait_finished(manager, identifier)
                self.assertEqual(final["status"], "completed", final)
                self.assertEqual(len(executed), 2)
                self.assertNotEqual(executed[0]["_project_manifest"], executed[1]["_project_manifest"])
                if not edit_entry:
                    self.assertEqual(executed[0]["_expected_hashes"], executed[1]["_expected_hashes"])
                self.assertEqual(final["executions"][-1]["stdout"], "0.0\n")
                self.assertEqual(len(final["executions"][-1]["project_files"]), 2)
                manager.close()

    def test_generated_runtime_rejects_changed_file_and_non_script(self):
        self.config["script_execution"] = {"enabled": True}
        manager = self.manager()
        identifier = manager.start("Create JSON")["id"]
        self.wait_finished(manager, identifier)
        with self.assertRaisesRegex(ValueError, "Only generated Python"):
            manager.run_artifact(identifier, 0)
        manager = self.manager(lambda role: FakeModel(role, "main.py", "print('original')"))
        identifier = manager.start("Create Python")["id"]
        state = self.wait_finished(manager, identifier)
        Path(state["artifacts"][0]["path"]).write_text("print('changed')")
        manager.run_artifact(identifier, 0)
        state = self.wait_finished(manager, identifier)
        self.assertEqual(state["status"], "failed")
        self.assertIn("changed since review", state["summary"])
        self.assertFalse(state["executions"])
        self.assertIsNone(state["pending"])

    def test_workspace_escape_and_changed_overwrite_are_rejected(self):
        workspace = self.root / "outputs" / "isolated"
        workspace.mkdir(parents=True)
        tools = scoped_plugins(self.plugins, self.config, workspace)
        with self.assertRaises(PermissionError):
            tools["file_write"].run(self.config, {"path": str(self.root / "outside.py"), "content": "x=1"})
        target = workspace / "file.py"
        target.write_text("original")
        args = {"path": "file.py", "content": "replacement"}
        tools["file_overwrite"].summary(self.config, args)
        target.write_text("changed after review")
        with self.assertRaisesRegex(ValueError, "changed after review"):
            tools["file_overwrite"].run(self.config, args)
        self.assertEqual(target.read_text(), "changed after review")

    def test_double_escaped_json_is_rejected_before_file_creation(self):
        workspace = self.root / "outputs" / "json-check"
        workspace.mkdir(parents=True)
        tools = scoped_plugins(self.plugins, self.config, workspace)
        with self.assertRaisesRegex(ValueError, "No file was changed"):
            tools["file_write"].run(self.config, {"path": "settings.json", "content": r'{\"project\":\"PRIMUS\"}'})
        self.assertFalse((workspace / "settings.json").exists())
        tools["file_write"].run(self.config, {"path": "settings.json", "content": '{"project":"PRIMUS"}'})
        self.assertEqual(json.loads((workspace / "settings.json").read_text()), {"project": "PRIMUS"})

    def test_expired_run_requires_fresh_approval_without_claiming_user_rejection(self):
        self.config["script_execution"] = {"enabled": True}
        executed = []
        manager = self.manager(lambda role: FakeModel(role, "main.py", "print('checked')"))
        manager.plugins = {**self.plugins, "script_run": replace(
            self.plugins["script_run"], run=lambda _c, args: executed.append(args.copy()) or
            {"verified": True, "exit_code": 0, "stdout": "checked\n", "stderr": "", "output_dir": "sandbox-output"})}
        identifier = manager.start("Create a Python file")["id"]
        self.wait_finished(manager, identifier)
        manager.threads[identifier].join(timeout=2)

        def wait_pending():
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                pending = manager.get(identifier)["pending"]
                if pending:
                    return pending
                time.sleep(.01)
            self.fail("No approval appeared")

        manager.run_artifact(identifier, 0)
        expired = wait_pending()
        mission = manager.items[identifier]
        with mission.lock:
            mission.pending["expires_at"] = time.time() - 1
        state = self.wait_finished(manager, identifier)
        manager.threads[identifier].join(timeout=2)
        run = state["executions"][0]
        self.assertEqual(run["approval_status"], "expired")
        self.assertEqual(run["status"], "not_executed")
        self.assertIsNone(run["exit_code"])
        self.assertIn("expired without a decision", run["error"])
        self.assertNotIn("rejected by user", json.dumps(state))
        self.assertEqual(state["runtime_repairs"]["used"], 0)
        self.assertFalse(executed)
        audit = [json.loads(line) for line in (self.root / "logs" / "audit.jsonl").read_text().splitlines()]
        self.assertTrue(any(row["tool"] == "mission_approval_expired" for row in audit))
        self.assertFalse(any(row["tool"] == "mission_approval" for row in audit))

        manager.run_artifact(identifier, 0)
        fresh = wait_pending()
        self.assertNotEqual(fresh["id"], expired["id"])
        with self.assertRaises(ValueError):
            manager.approve(identifier, expired["id"], True)
        self.assertFalse(executed)
        manager.approve(identifier, fresh["id"], True)
        state = self.wait_finished(manager, identifier)
        self.assertEqual(state["status"], "completed", state["summary"])
        self.assertEqual(len(executed), 1)
        self.assertEqual(state["executions"][-1]["approval_status"], "approved")

    def test_exact_approval_is_single_use_and_logged_without_prompt(self):
        manager = self.manager()
        mission = Mission(manager, uuid.uuid4().hex, "review test", False)
        manager.items[mission.id] = mission
        mission.begin_role("coder")
        replies = []
        thread = threading.Thread(target=lambda: replies.append(mission.confirm("private reviewed action")))
        thread.start()
        deadline = time.monotonic() + 2
        while mission.pending is None and time.monotonic() < deadline:
            time.sleep(.01)
        pending = mission.snapshot()["pending"]
        manager.approve(mission.id, pending["id"], True)
        thread.join(timeout=2)
        self.assertEqual(replies, [True])
        with self.assertRaises(ValueError):
            manager.approve(mission.id, pending["id"], True)
        decision = (self.root / "decision.md").read_text()
        self.assertIn("Mission action approved", decision)
        self.assertNotIn("private reviewed action", decision)

    def test_cancel_during_model_wait_prevents_coder_tools(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        class WaitingModel:
            def chat(self, *_):
                gate.wait(2)
                return {"role": "assistant", "content": json.dumps(PLAN)}
        manager = self.manager(lambda _role: WaitingModel())
        identifier = manager.start("Create a file")["id"]
        with self.assertRaisesRegex(ValueError, "already active"):
            manager.start("Create a second file")
        manager.cancel_mission(identifier)
        value = self.wait_finished(manager, identifier)
        gate.set()
        self.assertEqual(value["status"], "cancelled")
        self.assertFalse(list(Path(value["workspace"]).rglob("*")))

    def test_sandbox_check_waits_for_approval_and_rejection_never_executes(self):
        self.config["script_execution"] = {"enabled": True}
        class TestModel(FakeModel):
            def chat(self, messages, tools):
                if self.role != "coder":
                    return super().chat(messages, tools)
                self.calls += 1
                if self.calls <= 2:
                    filename, content = (("settings.json", '{"project":"PRIMUS"}') if self.calls == 1 else ("verify.py", "print('checked')"))
                    return {"role": "assistant", "tool_calls": [{"function": {"name": "file_write", "arguments": {"path": filename, "content": content}}}]}
                return {"role": "assistant", "content": "Files created."}
        for approved in (False, True):
            with self.subTest(approved=approved):
                executed = []
                manager = self.manager(TestModel)
                manager.plugins = {**self.plugins, "script_run": replace(self.plugins["script_run"], run=lambda _c, args: executed.append(args.copy()) or {"verified": True, "exit_code": 0, "stdout": "checked", "stderr": "", "output_dir": "sandbox-output"})}
                identifier = manager.start("Create settings JSON and a verification script", run_tests=True)["id"]
                deadline = time.monotonic() + 3
                pending = None
                while not pending and time.monotonic() < deadline:
                    pending = manager.get(identifier).get("pending")
                    time.sleep(.01)
                self.assertIsNotNone(pending)
                self.assertIn("EXTERNAL action", pending["prompt"])
                self.assertIn("SHA-256", pending["prompt"])
                self.assertEqual(executed, [])
                manager.approve(identifier, pending["id"], approved)
                mission = self.wait_finished(manager, identifier)
                self.assertEqual(len(executed), 1 if approved else 0)
                self.assertEqual(mission["status"], "completed" if approved else "needs_review")
                self.assertEqual(mission["roles"]["coder"]["attempts"], 1)
                manager.close()

    def test_invalid_plan_retries_before_coder_runs(self):
        planner = FakeModel("planner")
        original = planner.chat
        seen = []
        def malformed_then_valid(messages, tools):
            seen.append(1)
            return {"role": "assistant", "content": "bad JSON"} if len(seen) == 1 else original(messages, tools)
        planner.chat = malformed_then_valid
        manager = self.manager(lambda role: planner if role == "planner" else FakeModel(role))
        mission = self.wait_finished(manager, manager.start("Create JSON")["id"])
        self.assertEqual(mission["status"], "completed")
        self.assertEqual(len(seen), 2)
        self.assertIn("retry", {e["kind"] for e in mission["events"]})

    def test_restart_records_interruption_without_retaining_request(self):
        manager = self.manager()
        task_id = self.store.start_task("private transient goal", agent_label="mission")
        identifier = uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute("INSERT INTO missions VALUES(?,?,?,?,?,?,?)", (identifier, task_id, "running", "coder", str(self.root / "outputs"), "2026-10-21T00:00:00+00:00", "2026-10-21T00:00:00+00:00"))
        recovered = MissionManager(self.config, self.plugins, self.store)
        state = recovered.get(identifier)
        self.assertEqual(state["status"], "interrupted")
        self.assertNotIn("private transient goal", json.dumps(state))

    def test_launch_codes_are_one_time_and_cookie_requires_custom_header(self):
        auth = DashboardSessions()
        ticket = auth.issue()
        cookie = auth.exchange(ticket)
        self.assertTrue(auth.authorized("primus_mission=" + cookie))
        with self.assertRaises(ValueError):
            auth.exchange(ticket)
        self.assertFalse(auth.authorized("primus_mission=wrong"))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = {**self.config, "local_api": {"host": "127.0.0.1", "port": port}}
        with patch("primus.api.api_token", return_value="test-token"):
            server, _ = make_server(config)
            server.missions.provider_factory = FakeModel
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}"
            def request(path, data=None, **headers):
                req = Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                              headers={"Content-Type": "application/json", **headers})
                return urlopen(req, timeout=3)
            try:
                with request("/mission") as response:
                    self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
                    self.assertIn(b"Mission Control", response.read())
                with request("/mission/launch", {}, Authorization="Bearer test-token") as response:
                    launch = json.load(response)["ticket"]
                with request("/mission/session", {"ticket": launch}, Origin=base) as response:
                    session_cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                    self.assertIn("HttpOnly", response.headers["Set-Cookie"])
                with request("/missions", Cookie=session_cookie, **{"X-Primus-Control": "1"}) as response:
                    self.assertEqual(json.load(response)["missions"], [])
                for path, headers in (("/missions", {"Cookie": session_cookie}),
                                      ("/health", {"Cookie": session_cookie, "X-Primus-Control": "1"}),
                                      ("/missions?token=test-token", {})):
                    with self.assertRaises(HTTPError) as denied:
                        request(path, **headers)
                    self.assertEqual(denied.exception.code, 401)
                with self.assertRaises(HTTPError) as denied:
                    request("/missions", Authorization="Bearer test-token", Origin="https://evil.example")
                self.assertEqual(denied.exception.code, 403)
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
