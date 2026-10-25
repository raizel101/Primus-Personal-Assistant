"""Short-lived dashboard login and a root-local browser launcher."""
from __future__ import annotations

import hashlib
from http.cookies import SimpleCookie, CookieError
import json
import secrets
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .auth import api_token
from .process_lock import ProcessLock


class DashboardSessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.tickets, self.sessions = {}, {}

    def issue(self):
        with self.lock:
            now = time.monotonic()
            self.tickets = {k: v for k, v in self.tickets.items() if v > now}
            if len(self.tickets) >= 16:
                raise ValueError("Too many pending dashboard launches")
            code = secrets.token_urlsafe(32)
            self.tickets[hashlib.sha256(code.encode()).hexdigest()] = now + 60
            return code

    def exchange(self, code):
        if not isinstance(code, str) or len(code) > 100:
            raise ValueError("Invalid dashboard launch code")
        with self.lock:
            now = time.monotonic()
            deadline = self.tickets.pop(hashlib.sha256(code.encode()).hexdigest(), 0)
            if deadline <= now:
                raise ValueError("Dashboard launch expired; open it again from the PRIMUS tray")
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            if len(self.sessions) >= 16:
                self.sessions.pop(next(iter(self.sessions)))
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 3600
            return token

    def authorized(self, cookie):
        try:
            parsed = SimpleCookie()
            parsed.load(cookie or "")
            supplied = parsed.get("primus_mission")
            if not supplied or len(supplied.value) > 100:
                return False
            key = hashlib.sha256(supplied.value.encode()).hexdigest()
        except (CookieError, ValueError):
            return False
        with self.lock:
            return self.sessions.get(key, 0) > time.monotonic()


def api_request(config, path, data=None, *, timeout=10):
    request = Request(f"http://127.0.0.1:{config['local_api']['port']}{path}",
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={"Authorization": "Bearer " + api_token(), "Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def open_dashboard(config):
    """The existing private Chrome installation keeps browser data under PRIMUS."""
    from .tray import _start_local_core, _child_environment
    server, stop, scheduler_thread = _start_local_core(config)
    try:
        with ProcessLock(config["_root"] / "data" / "mission-ui.lock"):
            from playwright.sync_api import sync_playwright
            origin = f"http://127.0.0.1:{config['local_api']['port']}"
            launch = api_request(config, "/mission/launch", {})
            url = origin + "/mission#ticket=" + launch["ticket"]
            profile = config["_root"] / "data" / "browser-automation" / "mission-control"
            profile.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as driver:
                context = driver.chromium.launch_persistent_context(
                    str(profile), channel="chrome", headless=False, viewport={"width": 1400, "height": 900},
                    accept_downloads=False, service_workers="block", env=_child_environment(config["_root"]),
                    args=["--disable-background-networking", "--disable-component-update", "--disable-sync",
                          "--no-first-run", "--disable-default-apps", "--disable-breakpad", "--disable-extensions"])
                def local_only(route):
                    parsed = urlsplit(route.request.url)
                    if parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port == config["local_api"]["port"]:
                        route.continue_()
                    else:
                        route.abort()
                context.route("**/*", local_only)
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(url)
                except Exception:
                    context.close()
                    raise RuntimeError("Mission Control could not open. Open it again from the PRIMUS tray.") from None
                print("Mission Control opened in its PRIMUS-local Chrome profile. Close the window to exit this launcher.")
                try:
                    while context.pages:
                        context.pages[0].wait_for_timeout(500)
                except Exception:
                    pass
                finally:
                    context.close()
    finally:
        if server:
            stop.set()
            server.shutdown()
            server.server_close()
            scheduler_thread.join(timeout=2)
