"""Google Desktop OAuth PKCE and read-only Gmail/Calendar API client."""
from __future__ import annotations
import base64
import hashlib
import json
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request
from .auth import get_secret, put_secret
from .net import OPENER, json_get

SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar.events.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"

def enabled(config):
    if not config.get("network_plugins_enabled") or not config.get("google", {}).get("enabled"):
        raise PermissionError("Google connector is disabled in config")
    client_id = config["google"].get("client_id", "")
    if not client_id:
        raise ValueError("Google Desktop OAuth client_id is missing")
    return client_id

def token(config):
    client_id = enabled(config)
    refresh = get_secret("google-refresh-token")
    if not refresh:
        raise RuntimeError("Google account is not connected; run google-connect")
    payload = urllib.parse.urlencode({"client_id": client_id, "refresh_token": refresh,
                                      "grant_type": "refresh_token"}).encode()
    request = Request(TOKEN_URL, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with OPENER.open(request, timeout=15) as response:
        data = json.load(response)
    return data["access_token"]

def google_get(config, url, *, access_token=None):
    return json_get(url, headers={"Authorization": "Bearer " + (access_token or token(config))})

def connect(config):
    client_id = enabled(config)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    answer = {}
    event = threading.Event()
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if params.get("state", [None])[0] != state:
                self.send_error(400)
                return
            answer["code"] = params.get("code", [None])[0]
            answer["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"PRIMUS authorization received. You may close this tab.")
            event.set()
    server = HTTPServer(("127.0.0.1", 0), Callback)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    params = urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect,
        "response_type": "code", "scope": SCOPES, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state, "access_type": "offline", "prompt": "consent"})
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + params
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    try:
        webbrowser.open(url)
        if not event.wait(180):
            raise TimeoutError("Google authorization timed out")
    finally:
        server.server_close()
    if answer.get("error") or not answer.get("code"):
        raise RuntimeError("Google authorization was denied")
    payload = urllib.parse.urlencode({"client_id": client_id, "code": answer["code"],
        "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    request = Request(TOKEN_URL, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with OPENER.open(request, timeout=15) as response:
        data = json.load(response)
    refresh = data.get("refresh_token")
    if not refresh:
        raise RuntimeError("Google did not return a refresh token")
    put_secret("google-refresh-token", refresh)
