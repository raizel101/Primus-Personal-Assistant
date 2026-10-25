"""Real localhost dashboard rendering, authentication, layout, and safe text display."""
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
from unittest.mock import patch
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "vendor"))
sys.path.insert(0, str(PROJECT / "src"))
from primus.api import make_server
from test_missions import FakeModel, PLAN
from primus.plugins import discover


class DashboardUITests(unittest.TestCase):
    def test_dashboard_runs_real_pipeline_and_renders_untrusted_text_safely(self):
        try:
            from playwright.sync_api import sync_playwright, expect
        except ImportError:
            self.skipTest("Optional Playwright is not installed")
        with tempfile.TemporaryDirectory(dir=PROJECT / "temp") as temporary:
            root = Path(temporary)
            (root / "plugins").mkdir(); (root / "logs").mkdir()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
            config = {"_root": root, "approved_folders": [], "network_plugins_enabled": False,
                      "missions": {"enabled": True, "max_steps": 4}, "max_invalid_responses": 2,
                      "script_execution": {"enabled": True},
                      "model": {"name": "test"}, "scheduler": {"poll_seconds": 1},
                      "local_api": {"host": "127.0.0.1", "port": port}}
            hostile = '<img src=x onerror="window.untrustedExecuted=true">'
            class UIModel(FakeModel):
                def __init__(self, role):
                    super().__init__(role, "main.py", "print(missing_value)")
                def chat(self, messages, tools):
                    if self.role == "planner":
                        return {"role": "assistant", "content": json.dumps({**PLAN, "title": hostile})}
                    if self.role == "coder" and "UNTRUSTED ACTUAL FAILED EXECUTION" in messages[1].get("content", ""):
                        self.calls += 1
                        if self.calls == 1:
                            return {"role": "assistant", "tool_calls": [{"function": {"name": "file_read", "arguments": {"path": "main.py"}}}]}
                        if self.calls == 2:
                            return {"role": "assistant", "tool_calls": [{"function": {"name": "file_overwrite", "arguments": {"path": "main.py", "content": "for n in range(6):\n    print(n)"}}}]}
                        return {"role": "assistant", "content": "Source repaired; not run."}
                    return super().chat(messages, tools)
            with patch("primus.api.api_token", return_value="test-token"):
                server, _ = make_server(config)
                server.missions.plugins = discover(PROJECT)
                executions = []
                original = server.missions.plugins["script_run"]
                def test_run(_c, args):
                    executions.append(args.copy())
                    success = len(executions) > 1
                    return {"verified": success, "exit_code": 0 if success else 1,
                            "stdout": "0\n1\n2\n3\n4\n5\n" + hostile if success else "",
                            "stderr": "" if success else "Traceback: NameError: missing_value is not defined", "output_dir": "sandbox-output"}
                server.missions.plugins["script_run"] = replace(original, run=test_run)
                server.missions.provider_factory = UIModel
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    base = f"http://127.0.0.1:{port}"
                    request = Request(base + "/mission/launch", data=b"{}", headers={"Authorization": "Bearer test-token"})
                    with urlopen(request, timeout=3) as response:
                        ticket = json.load(response)["ticket"]
                    with sync_playwright() as driver:
                        try:
                            browser = driver.chromium.launch(channel="chrome", headless=True,
                                                             args=["--disable-background-networking", "--disable-component-update"])
                        except Exception as exc:
                            if "not found" in str(exc).lower() or "doesn't exist" in str(exc).lower():
                                self.skipTest("Installed Chrome is unavailable")
                            raise
                        try:
                            page = browser.new_page(viewport={"width": 1440, "height": 1000})
                            errors = []
                            page.on("pageerror", lambda value: errors.append(str(value)))
                            page.goto(base + "/mission#ticket=" + ticket)
                            expect(page.locator("#launch")).to_be_enabled()
                            self.assertEqual(page.url, base + "/mission")
                            page.locator("#goal").fill("Create a Python file")
                            page.locator("#launch").click()
                            expect(page.locator("#mission-status")).to_have_text("completed", timeout=10000)
                            self.assertEqual(page.locator("#mission-title").inner_text(), hostile)
                            self.assertEqual(page.locator("img").count(), 0)
                            self.assertFalse(page.evaluate("() => Boolean(window.untrustedExecuted)"))
                            self.assertIn("python syntax", page.locator("#checks").inner_text())
                            page.locator(".artifact").first.click()
                            expect(page.locator("#file-content")).to_contain_text("print(missing_value)")
                            page.locator("#close-file").click()
                            page.get_by_role("button", name="Run main.py in sandbox").click()
                            expect(page.locator("#approval")).to_be_visible()
                            expect(page.locator("#approval-notice")).to_be_visible()
                            page.locator("#review-action").click()
                            expect(page.locator("#approval-prompt")).to_contain_text("SHA-256")
                            for width in (1440, 768, 390):
                                page.set_viewport_size({"width": width, "height": 1000})
                                expect(page.locator("#review-action")).to_be_in_viewport()
                                self.assertTrue(page.evaluate("() => document.documentElement.scrollWidth<=window.innerWidth"), f"Approval notice overflow at {width}px")
                            page.set_viewport_size({"width": 1440, "height": 1000})
                            self.assertEqual(executions, [])
                            mission = next(iter(server.missions.items.values()))
                            old_approval = mission.snapshot()["pending"]["id"]
                            with mission.lock:
                                mission.pending["expires_at"] = time.time() - 1
                            expect(page.locator("#executions")).to_contain_text("Approval expired without a decision")
                            expect(page.locator("#executions .runtime-stdout").first).to_have_text("Not executed; no output was captured.")
                            expect(page.locator("#approval-notice")).to_be_hidden()
                            self.assertNotIn("rejected by user", page.locator("#executions").inner_text())
                            page.get_by_role("button", name="Request fresh approval for main.py").click()
                            expect(page.locator("#approval-notice")).to_be_visible()
                            expect(page.locator("#approval")).to_be_visible()
                            self.assertNotEqual(mission.snapshot()["pending"]["id"], old_approval)
                            self.assertEqual(executions, [])
                            page.locator("#approve").click()
                            expect(page.locator("#executions .runtime-stderr").nth(1)).to_contain_text("NameError")
                            expect(page.locator("#approval-prompt")).to_contain_text("Tool: file_overwrite")
                            self.assertEqual(len(executions), 1)
                            page.locator("#approve").click()
                            expect(page.locator("#approval-prompt")).to_contain_text("Tool: script_run")
                            self.assertEqual(len(executions), 1)
                            page.locator("#approve").click()
                            expect(page.locator("#executions .runtime-stdout").last).to_contain_text("0\n1\n2\n3\n4\n5")
                            expect(page.locator("#executions")).to_contain_text("Exit code: 0")
                            expect(page.locator("#mission-status")).to_have_text("completed")
                            self.assertEqual(len(executions), 2)
                            expect(page.locator(".runtime-manifest").last).to_contain_text("main.py")
                            self.assertEqual(len(executions[-1]["_project_manifest"]), 1)
                            expect(page.locator("#runtime-repair-state")).to_contain_text("1/1")
                            self.assertEqual(page.locator("img").count(), 0)
                            self.assertFalse(page.evaluate("() => Boolean(window.untrustedExecuted)"))
                            for width in (1440, 768, 390):
                                page.set_viewport_size({"width": width, "height": 1000})
                                self.assertTrue(page.evaluate("() => document.documentElement.scrollWidth<=window.innerWidth"), f"Overflow at {width}px")
                            page.set_viewport_size({"width": 1440, "height": 1000})
                            page.evaluate("() => window.scrollTo(0,0)")
                            page.screenshot(path=str(PROJECT / "temp" / "mission-ui-check.png"), full_page=True)
                            mission = next(iter(server.missions.items.values()))
                            mission.status = "failed"
                            mission.summary = "Coder stopped: old snippet did not match. " + hostile
                            expect(page.locator("#mission-attention")).to_be_visible()
                            expect(page.locator("#mission-attention")).to_contain_text("old snippet did not match")
                            self.assertEqual(page.locator("img").count(), 0)
                            self.assertFalse(page.evaluate("() => Boolean(window.untrustedExecuted)"))
                            self.assertEqual(errors, [])
                        finally:
                            browser.close()
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
