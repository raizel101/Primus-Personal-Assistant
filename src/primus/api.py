"""Authenticated localhost API shared by local interfaces."""
from __future__ import annotations
import hmac
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from .auth import api_token
from .audit import append_audit
from .consent import confirm_desktop
from .image_service import run_image
from .dashboard import DashboardSessions
from .missions import MissionManager, mission_request
from .plugins import discover
from .scheduler import Scheduler
from .service import TaskRunner
from .storage import Store
from .team import TeamRunner, team_request

MAX_BODY = 8192

class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False
    def start_local_mcp(self, config):
        if not getattr(self, "local_mcp", None):
            from .mcp_server import start_managed
            self.local_mcp = start_managed(config)
    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
    def server_close(self):
        if getattr(self, "local_mcp", None):
            self.local_mcp.close()
            self.local_mcp = None
        if hasattr(self, "missions"):
            self.missions.close()
        super().server_close()

def make_server(config: dict):
    root = config["_root"]
    store = Store(root)
    browser_tools = {"browser_open", "browser_navigate", "browser_read", "browser_click", "browser_type"} if config.get("browser", {}).get("enabled") else set()
    plugins = discover(root, network_enabled=config["network_plugins_enabled"], enabled_network_plugins=browser_tools)
    token = api_token()
    host, port = config["local_api"]["host"], config["local_api"]["port"]
    if host != "127.0.0.1":
        raise ValueError("Local API must bind to 127.0.0.1")
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    sessions: dict[str, tuple[float, TaskRunner, threading.Lock]] = {}
    sessions_lock = threading.Lock()
    dashboard_sessions = DashboardSessions()
    missions = None
    assets = Path(__file__).with_name("mission_web")

    class Handler(BaseHTTPRequestHandler):
        server_version = "PRIMUS/2"
        def log_message(self, *_):
            pass
        def reply(self, code, data, cookie=None):
            # Windows can reset a socket closed with an unread POST body before
            # the client receives its 401/403 response. Discard only a bounded
            # rejected body, without parsing it or performing any task work.
            if code >= 400 and self.command == "POST" and not getattr(self, "_body_consumed", False):
                previous_timeout = self.connection.gettimeout()
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if 0 < size <= MAX_BODY:
                        self.connection.settimeout(1)
                        self.rfile.read(size)
                except (OSError, ValueError):
                    pass
                finally:
                    self.connection.settimeout(previous_timeout)
                    self._body_consumed = True
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            try:
                self.wfile.write(body)
            except ConnectionError:
                pass
        def valid_surface(self):
            if self.headers.get("Host", "") not in allowed_hosts:
                self.reply(403, {"error": "Invalid Host"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{host}:{port}", f"http://localhost:{port}"}:
                self.reply(403, {"error": "Invalid Origin"})
                return False
            return True
        def authorized(self, dashboard=False):
            if not self.valid_surface():
                return False
            supplied = self.headers.get("Authorization", "")
            if hmac.compare_digest(supplied, "Bearer " + token):
                return True
            if dashboard and self.headers.get("X-Primus-Control") == "1" and dashboard_sessions.authorized(self.headers.get("Cookie")):
                return True
            self.reply(401, {"error": "Open Mission Control from the PRIMUS tray to sign in"})
            return False
        def do_GET(self):
            path = urlsplit(self.path).path
            static = {"/mission": ("index.html", "text/html; charset=utf-8"),
                      "/mission/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/mission/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in static:
                if not self.valid_surface():
                    return
                name, mime = static[path]
                body = (assets / name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                self.end_headers()
                self.wfile.write(body)
                return
            if not self.authorized(dashboard=path == "/missions" or path.startswith("/missions/")):
                return
            if path == "/missions":
                self.reply(200, {"missions": missions.list(), "model": config["model"]["name"],
                                 "approval_mode": "automatic" if config.get("permissions", {}).get("auto_approve") is True else "manual",
                                 "stopped": (root / "data" / "STOP").exists(), "root": str(root), "history": store.list_tasks(12)})
                return
            match = re.fullmatch(r"/missions/([0-9a-f]{32})(?:/files/([0-9]+))?", path)
            if match:
                try:
                    self.reply(200, missions.file(match[1], int(match[2])) if match[2] is not None else missions.get(match[1]))
                except (ValueError, KeyError, PermissionError) as exc:
                    self.reply(404, {"error": str(exc)})
                return
            if path == "/health":
                self.reply(200, {"ok": True, "root": str(root),
                                 "approval_mode": "automatic" if config.get("permissions", {}).get("auto_approve") is True else "manual"})
            elif path == "/memories":
                self.reply(200, {"memories": store.list_memories()})
            elif path == "/context":
                self.reply(200, {"context": store.get_context(config.get("memory", {}).get("context_retention_days", 30))})
            elif path == "/history":
                self.reply(200, {"tasks": store.list_tasks()})
            elif path == "/schedules":
                self.reply(200, {"schedules": store.list_schedules()})
            elif path == "/knowledge":
                try:
                    from .knowledge import Knowledge
                    self.reply(200, Knowledge(config).list())
                except (ValueError, OSError, RuntimeError) as exc:
                    self.reply(400, {"error": str(exc)})
            else:
                self.reply(404, {"error": "Not found"})
        def do_POST(self):
            path = urlsplit(self.path).path
            if path == "/mission/session":
                if not self.valid_surface():
                    return
            elif not self.authorized(dashboard=path == "/missions" or path.startswith("/missions/")):
                return
            if path not in {"/tasks", "/teams", "/knowledge/search", "/knowledge/ask", "/images/analyze", "/mission/launch", "/mission/session", "/missions"} and not re.fullmatch(r"/missions/[0-9a-f]{32}/(?:cancel|approval|run)", path):
                self.reply(404, {"error": "Not found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    raise ValueError("Invalid request size")
                raw = self.rfile.read(size)
                self._body_consumed = True
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Request must be an object")
                if path in {"/knowledge/search", "/knowledge/ask"}:
                    if set(data) != {"question"} or not isinstance(data["question"], str) or not 1 <= len(data["question"].strip()) <= 2000:
                        raise ValueError("Knowledge requests require question (1 to 2000 characters)")
                    from .knowledge import Knowledge, answer
                    result = Knowledge(config).search(data["question"]) if path.endswith("/search") else answer(config, data["question"])
                    append_audit(root, "knowledge_query", {"question": data["question"]}, {"route": path, "ok": True})
                    self.reply(200, result)
                    return
                if path == "/mission/launch":
                    if data:
                        raise ValueError("Launch accepts an empty object")
                    self.reply(200, {"ticket": dashboard_sessions.issue()})
                    return
                if path == "/mission/session":
                    if set(data) != {"ticket"}:
                        raise ValueError("Session requires a launch ticket")
                    session_token = dashboard_sessions.exchange(data["ticket"])
                    self.reply(200, {"ok": True}, "primus_mission=" + session_token + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600")
                    return
                if path == "/missions":
                    if set(data) - {"goal", "run_tests"} or "goal" not in data:
                        raise ValueError("Mission accepts goal and optional run_tests")
                    self.reply(202, missions.start(data["goal"], data.get("run_tests", False)))
                    return
                mission_action = re.fullmatch(r"/missions/([0-9a-f]{32})/(cancel|approval|run)", path)
                if mission_action:
                    if mission_action[2] == "run" and set(data) == {"artifact_index"}:
                        self.reply(202, missions.run_artifact(mission_action[1], data["artifact_index"]))
                        return
                    elif mission_action[2] == "cancel" and not data:
                        missions.cancel_mission(mission_action[1])
                    elif mission_action[2] == "approval" and set(data) == {"approval_id", "approved"}:
                        missions.approve(mission_action[1], data["approval_id"], data["approved"])
                    else:
                        raise ValueError("Invalid mission action")
                    self.reply(200, {"ok": True})
                    return
                if path == "/images/analyze":
                    self.reply(200, {"result": run_image(config, plugins, store, data)})
                    return
                if path == "/teams":
                    if "tasks" in data:
                        if set(data) != {"tasks"}:
                            raise ValueError("Explicit team request accepts only tasks")
                        result = TeamRunner(config, plugins, store).run(tasks=data["tasks"])
                    else:
                        if set(data) != {"task"} or not isinstance(data["task"], str) or not data["task"].strip():
                            raise ValueError("Team request needs a non-empty task")
                        result = TeamRunner(config, plugins, store).run(data["task"])
                    self.reply(200, {"result": result})
                    return
                if not isinstance(data.get("task"), str) or not data["task"].strip():
                    raise ValueError("Task must be a non-empty string")
                mission_goal = mission_request(data["task"])
                if mission_goal is not None:
                    started = missions.start(mission_goal)
                    self.reply(200, {"result": f"Mission {started['id'][:8]} started. Open Mission Control from the PRIMUS tray to follow the planner, coder, and verifier."})
                    return
                team_task = team_request(data["task"])
                if team_task is not None:
                    result = TeamRunner(config, plugins, store).run(team_task)
                    self.reply(200, {"result": result})
                    return
                # The authenticated local API asks the signed-in desktop user for
                # one exact action. Headless sessions and timeouts deny by default.
                session = data.get("session")
                if session is None:
                    result = TaskRunner(config, plugins, store, confirm=confirm_desktop).run(data["task"])
                else:
                    if not isinstance(session, str) or len(session) != 32 or any(c not in "0123456789abcdef" for c in session):
                        raise ValueError("Invalid session identifier")
                    with sessions_lock:
                        now = time.monotonic()
                        for key, value in list(sessions.items()):
                            if now - value[0] > 3600:
                                del sessions[key]
                        if session not in sessions:
                            if len(sessions) >= 8:
                                del sessions[min(sessions, key=lambda key: sessions[key][0])]
                            sessions[session] = (now, TaskRunner(config, plugins, store, confirm=confirm_desktop), threading.Lock())
                        _, runner, lock = sessions[session]
                        sessions[session] = (now, runner, lock)
                    with lock:
                        result = runner.run(data["task"])
                self.reply(200, {"result": result})
            except ConnectionError:
                return
            except Exception as exc:
                self.reply(400, {"error": str(exc)})
    server = LocalServer((host, port), Handler)
    missions = MissionManager(config, plugins, store)
    server.missions = missions
    server.daemon_threads = True
    return server, Scheduler(config, store, plugins)

def serve(config: dict):
    server, scheduler = make_server(config)
    try:
        server.start_local_mcp(config)
    except Exception:
        server.server_close()
        raise
    stop = threading.Event()
    worker = threading.Thread(target=scheduler.run_forever, args=(stop,), daemon=True)
    worker.start()
    try:
        print(f"PRIMUS API listening on http://127.0.0.1:{config['local_api']['port']}")
        server.serve_forever(poll_interval=0.2)
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=2)
