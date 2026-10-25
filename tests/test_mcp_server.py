"""Production local MCP endpoint tests with synthetic tokens, roots and consent."""
from pathlib import Path
import json
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "vendor"), str(ROOT / "src")]
from primus.mcp_server import make_server, validate_settings
from primus.mcp_client import Client, call_preview, call_tool, PROTOCOL
from primus.agent import Agent
from primus.plugins import discover
from test_phase1 import ScriptedProvider, call

TOKEN = "synthetic-local-token-for-tests-only-123456"


class LocalMCPServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "data").mkdir(); (self.root / "logs").mkdir()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        self.row = {"name": "primus_local", "url": f"http://127.0.0.1:{port}/mcp", "enabled": True,
                    "network": False, "secret_name": "mcp-primus-token"}
        self.config = {"_root": self.root, "model": {"name": "fixture-model"}, "network_plugins_enabled": False,
                       "local_mcp": {"enabled": True, "port": port, "secret_name": "mcp-primus-token"},
                       "mcp": {"enabled": True, "servers": [self.row]}, "knowledge": {"enabled": False}}
        with patch("primus.mcp_server.get_secret", return_value=TOKEN):
            self.server = make_server(self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), self.thread.join(timeout=2)))
        credential = patch("primus.mcp_client.get_secret", return_value=TOKEN)
        credential.start(); self.addCleanup(credential.stop)

    def request(self, message=None, method=None, **headers):
        base = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        base.update(headers)
        return urlopen(Request(self.row["url"], data=json.dumps(message).encode() if message else None,
                               headers=base, method=method), timeout=3)

    def test_real_production_server_lifecycle_discovery_and_status(self):
        with Client(self.config, self.row) as client:
            catalog = client.tools()
            self.assertEqual([t["name"] for t in catalog], ["primus_status"])
            self.assertEqual(len(self.server.sessions), 1)
            result = client.request("tools/call", {"name": "primus_status", "arguments": {}})
            actual = json.loads(result["content"][0]["text"])
            self.assertEqual(actual["root"], str(self.root))
            self.assertEqual(actual["model"], "fixture-model")
            self.assertEqual(actual["knowledge_documents"], 0)
            self.assertFalse((self.root / "data" / "knowledge.sqlite3").exists())
        self.assertEqual(self.server.sessions, {})
        audit = (self.root / "logs" / "audit.jsonl").read_text()
        self.assertIn('"mcp_server_tool"', audit)
        self.assertNotIn(TOKEN, audit)

    def test_auth_host_origin_and_get_guardrails(self):
        for headers, code in (({"Authorization": "Bearer wrong"}, 401), ({"Host": "evil.example"}, 403),
                              ({"Origin": "https://evil.example"}, 403)):
            with self.assertRaises(HTTPError) as denied: self.request(method="GET", **headers)
            self.assertEqual(denied.exception.code, code)
        with self.assertRaises(HTTPError) as unsupported: self.request(method="GET")
        self.assertEqual(unsupported.exception.code, 405)
        self.assertFalse((self.root / "logs" / "audit.jsonl").exists())

    def test_missing_session_wrong_version_and_kill_switch(self):
        with self.assertRaises(HTTPError) as missing:
            self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, **{"MCP-Protocol-Version": PROTOCOL})
        self.assertEqual(missing.exception.code, 404)
        with self.assertRaises(HTTPError) as version:
            self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, **{"MCP-Protocol-Version": "wrong"})
        self.assertEqual(version.exception.code, 400)
        (self.root / "data" / "STOP").write_text("stop")
        with self.assertRaises(HTTPError) as stopped:
            self.request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(stopped.exception.code, 503)

    def test_server_refuses_arbitrary_tools_and_parameters(self):
        from primus.mcp_client import MCPError
        with Client(self.config, self.row) as client:
            for params in ({"name": "shell_run", "arguments": {}}, {"name": "primus_status", "arguments": {"path": "outside"}}):
                with self.assertRaises(MCPError): client.request("tools/call", params)
        self.assertFalse((self.root / "logs" / "audit.jsonl").exists())

    def test_host_permission_denial_never_executes_production_tool(self):
        model = ScriptedProvider(call("mcp_call", {"server": "primus_local", "tool": "primus_status", "arguments": {}}),
                                 {"role": "assistant", "content": "Not approved"})
        Agent(self.config, discover(ROOT), model, confirm=lambda _: False).run("Use MCP")
        self.assertFalse((self.root / "logs" / "audit.jsonl").read_text().find('"mcp_server_tool"') >= 0)

    def test_approved_synthetic_host_bridge_calls_real_server(self):
        model = ScriptedProvider(call("mcp_call", {"server": "primus_local", "tool": "primus_status", "arguments": {}}),
                                 {"role": "assistant", "content": "Status received"})
        prompts = []
        result = Agent(self.config, discover(ROOT), model, confirm=lambda p: prompts.append(p) or True).run("Use MCP")
        self.assertEqual(result, "Status received")
        self.assertEqual(len(prompts), 1)
        self.assertIn("EXTERNAL action", prompts[0])
        self.assertIn('"mcp_server_tool"', (self.root / "logs" / "audit.jsonl").read_text())

    def test_listener_configuration_and_missing_keychain_fail_closed(self):
        for changes in ({"enabled": True, "port": 8765}, {"enabled": True, "port": True}, {"enabled": True, "secret_name": "api-token"}):
            with self.assertRaises(ValueError): validate_settings({"local_mcp": changes})
        with patch("primus.mcp_server.get_secret", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "token is missing"): make_server(self.config)


if __name__ == "__main__":
    unittest.main()
