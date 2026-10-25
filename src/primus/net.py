"""Constrained HTTPS requests with public-address pinning."""
from __future__ import annotations
import http.client
import ipaddress
import json
import socket
import ssl
import urllib.parse
import urllib.request

def public_address(url: str):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTPS URLs are allowed")
    if parsed.port not in (None, 443):
        raise ValueError("Only HTTPS port 443 is allowed")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Host has no address")
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if any(not ip.is_global for ip in ips):
        raise ValueError("Private, local, and reserved hosts are blocked")
    return parsed, str(ips[0])

def fetch(url: str, *, headers=None, max_bytes=100_000) -> tuple[int, bytes, str]:
    parsed, address = public_address(url)
    connection = http.client.HTTPSConnection(parsed.hostname, 443, timeout=12, context=ssl.create_default_context())
    connection._create_connection = lambda _target, timeout=None, source_address=None: socket.create_connection((address, 443), timeout)
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    safe_headers = {"User-Agent": "PRIMUS/2", **(headers or {})}
    try:
        connection.request("GET", path, headers=safe_headers)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("Network response too large")
        return response.status, body, response.getheader("Content-Type", "")
    finally:
        connection.close()

def json_get(url: str, *, headers=None):
    _, body, _ = fetch(url, headers=headers)
    return json.loads(body)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise ValueError("Redirects are blocked")

OPENER = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
