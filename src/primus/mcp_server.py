"""First-party authenticated localhost MCP status server; no arbitrary commands or paths."""
from __future__ import annotations
from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from .audit import append_audit
from .auth import get_secret
from .knowledge import Knowledge
from .mcp_client import PROTOCOL

STATUS_TOOL = {
    "name": "primus_status",
    "description": "Read actual PRIMUS model name, root, indexed-document/passage counts and voice state. No document text, credentials, commands or settings changes.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}


def validate_settings(config):
    value = config.get("local_mcp", {"enabled": False, "port": 8767, "secret_name": "mcp-primus-token"})
    if (not isinstance(value, dict) or set(value) - {"enabled", "port", "secret_name"}
            or not isinstance(value.get("enabled", False), bool)):
        raise ValueError("local_mcp requires enabled, port and a keychain secret_name")
    port = value.get("port", 8767)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("local_mcp.port must be a nonprivileged TCP port")
    if value.get("enabled", False) and port in {config.get("local_api", {}).get("port", 8765), 11434, 11435}:
        raise ValueError("local_mcp.port must differ from the core API and local model ports")
    name = value.get("secret_name", "mcp-primus-token")
    if not isinstance(name, str) or not re.fullmatch(r"mcp-[a-z0-9_-]{1,30}-token", name):
        raise ValueError("local_mcp.secret_name must name a dedicated mcp-...-token keychain account")
    return {"enabled": value.get("enabled", False), "port": port, "secret_name": name}


def primus_status(config):
    """Called only after the server has authenticated and validated tools/call."""
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    count, passages = 0, 0
    database = config["_root"] / "data" / "knowledge.sqlite3"
    if config.get("knowledge", {}).get("enabled", False) and database.is_file():
        db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            count, passages = db.execute("SELECT count(*),coalesce(sum(chunk_count),0) FROM documents").fetchone()
        finally:
            db.close()
    voice = {"state": "not-started", "updated_at": None}
    path = config["_root"] / "data" / "voice-status.json"
    if path.is_file() and path.stat().st_size <= 4096:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            voice = {"state": str(raw.get("state", "unknown"))[:40], "updated_at": raw.get("updated_at")}
        except (OSError, ValueError, AttributeError):
            voice = {"state": "unavailable", "updated_at": None}
    return {"root": str(config["_root"]), "model": config["model"]["name"],
            "knowledge_documents": count, "knowledge_passages": passages, "voice": voice,
            "server_pid": os.getpid(), "observed_utc": datetime.now(timezone.utc).isoformat()}


class StatusServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 8

    def server_bind(self):
        if self.server_address[0] != "127.0.0.1":
            raise ValueError("MCP server must bind only to 127.0.0.1")
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, *args):
        try:
            super().process_request_thread(*args)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "PRIMUS-MCP/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_):
        pass

    def reply(self, status, body=None, session=None):
        payload = json.dumps(body, allow_nan=False).encode("utf-8") if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if session:
            self.send_header("MCP-Session-Id", session)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except ConnectionError:
            pass

    def rpc_error(self, identifier, code, message):
        self.reply(200, {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}})

    def permitted(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            self.reply(403, {"error": "Invalid Host"}); return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {"http://" + h for h in hosts}:
            self.reply(403, {"error": "Invalid Origin"}); return False
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token):
            self.reply(401, {"error": "Authentication required"}); return False
        parsed = urlsplit(self.path)
        if parsed.path != "/mcp" or parsed.query or parsed.fragment:
            self.reply(404, {"error": "Not found"}); return False
        return True

    def current_session(self):
        if self.headers.get("MCP-Protocol-Version") != PROTOCOL:
            self.reply(400, {"error": "Unsupported MCP protocol version"}); return None
        identifier = self.headers.get("MCP-Session-Id", "")
        with self.server.lock:
            session = self.server.sessions.get(identifier)
            if session and session["expires"] > time.monotonic():
                return session
        self.reply(404, {"error": "MCP session unavailable"})
        return None

    def do_GET(self):
        if not self.permitted(): return
        self.reply(405, {"error": "This server responds to POST requests; unsolicited SSE is not offered"})

    def do_DELETE(self):
        if not self.permitted() or self.current_session() is None: return
        with self.server.lock:
            self.server.sessions.pop(self.headers.get("MCP-Session-Id"), None)
        self.reply(204)

    def do_POST(self):
        if not self.permitted(): return
        if (self.server.config["_root"] / "data" / "STOP").exists():
            self.reply(503, {"error": "Global kill switch is active"}); return
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            self.reply(400, {"error": "Use a bounded application/json request"}); return
        accept = self.headers.get("Accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            self.reply(406, {"error": "Accept must include application/json and text/event-stream"}); return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384: raise ValueError()
            message = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError, OSError):
            self.reply(400, {"error": "Invalid bounded JSON request"}); return
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            self.rpc_error(None, -32600, "Invalid JSON-RPC request"); return
        method, params = message["method"], message.get("params", {})
        identifier = message.get("id")
        notification = "id" not in message
        if not notification and (isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or len(str(identifier)) > 64):
            self.rpc_error(None, -32600, "Invalid request ID"); return
        if not isinstance(params, dict):
            self.rpc_error(identifier, -32602, "Parameters must be an object"); return
        if method == "initialize":
            if notification or not isinstance(params.get("protocolVersion"), str) or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                self.rpc_error(identifier, -32602, "Initialize requires version, capabilities and client info"); return
            with self.server.lock:
                now = time.monotonic()
                self.server.sessions = {k:v for k,v in self.server.sessions.items() if v["expires"] > now}
                if len(self.server.sessions) >= 64:
                    self.reply(429, {"error": "Session limit reached"}); return
                session = secrets.token_urlsafe(32)
                self.server.sessions[session] = {"expires": now + 600, "initialized": False, "ids": set()}
            self.reply(200, {"jsonrpc": "2.0", "id": identifier, "result": {"protocolVersion": PROTOCOL,
                       "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "PRIMUS local status", "version": "1.0.0"}}}, session=session)
            return
        session = self.current_session()
        if session is None: return
        if method == "notifications/initialized" and notification:
            with self.server.lock: session["initialized"] = True
            self.reply(202); return
        if notification:
            self.reply(202); return
        with self.server.lock:
            if not session["initialized"]:
                self.rpc_error(identifier, -32000, "Complete initialized notification first"); return
            if identifier in session["ids"] or len(session["ids"]) >= 128:
                self.rpc_error(identifier, -32600, "Request ID reused or session request limit reached"); return
            session["ids"].add(identifier)
        if method == "ping":
            result = {}
        elif method == "tools/list":
            if params:
                self.rpc_error(identifier, -32602, "No catalog cursor is supported"); return
            result = {"tools": [STATUS_TOOL]}
        elif method == "tools/call":
            if set(params) - {"name", "arguments", "_meta"} or params.get("name") != "primus_status" or params.get("arguments", {}) != {}:
                self.rpc_error(identifier, -32602, "Only primus_status with empty object arguments is allowed"); return
            try:
                actual = primus_status(self.server.config)
                append_audit(self.server.config["_root"], "mcp_server_tool", {"tool": "primus_status", "arguments": {}}, {"ok": True, "result": actual})
                result = {"content": [{"type": "text", "text": json.dumps(actual, ensure_ascii=False)}], "isError": False}
            except Exception as exc:
                append_audit(self.server.config["_root"], "mcp_server_tool", {"tool": "primus_status"}, {"ok": False, "error": str(exc)})
                result = {"content": [{"type": "text", "text": "Local status could not be read; inspect the local audit record."}], "isError": True}
        else:
            self.rpc_error(identifier, -32601, "Method not found"); return
        self.reply(200, {"jsonrpc": "2.0", "id": identifier, "result": result})


def make_server(config):
    value = validate_settings(config)
    if not value["enabled"]:
        raise ValueError("local_mcp is disabled")
    token = get_secret(value["secret_name"])
    if not isinstance(token, str) or len(token) < 32 or any(c in token for c in "\r\n"):
        raise RuntimeError("Dedicated local MCP token is missing; create it in the OS keychain after exact approval")
    server = StatusServer(("127.0.0.1", value["port"]), Handler)
    server.config, server.token = config, token
    server.sessions, server.lock, server.slots = {}, threading.RLock(), threading.BoundedSemaphore(8)
    return server


class ManagedServer:
    def __init__(self, config):
        self.server = make_server(config)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .2}, daemon=True, name="primus-mcp")
        self.thread.start()
        append_audit(config["_root"], "mcp_server_start", {}, {"host": "127.0.0.1", "port": self.server.server_port, "protocol": PROTOCOL})

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.server.sessions.clear()


def start_managed(config):
    return ManagedServer(config) if validate_settings(config)["enabled"] else None


def serve(config):
    server = make_server(config)
    try:
        print(f"Authenticated PRIMUS MCP listening at http://127.0.0.1:{server.server_port}/mcp")
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()
