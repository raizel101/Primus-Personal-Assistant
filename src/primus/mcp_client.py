"""Bounded localhost MCP Streamable HTTP tools client (2025-11-25).

No process launching, cloud endpoints, sampling, elicitation or automatic tool retries.
An MCP server is separately trusted software, not contained by PRIMUS's script sandbox.
"""
from __future__ import annotations
import hashlib
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from .auth import get_secret

PROTOCOL = "2025-11-25"
MAX_BODY = 262144
SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum", "description", "title", "default", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "$schema", "examples"}


class MCPError(ValueError):
    pass


def settings(config):
    value = config.get("mcp", {"enabled": False, "servers": []})
    if not isinstance(value, dict) or set(value) - {"enabled", "servers"} or not isinstance(value.get("enabled", False), bool):
        raise MCPError("mcp requires boolean enabled and a servers list")
    servers = value.get("servers", [])
    if not isinstance(servers, list) or len(servers) > 4:
        raise MCPError("Configure at most four MCP servers")
    names = set()
    for row in servers:
        if not isinstance(row, dict) or set(row) - {"name", "url", "enabled", "network", "secret_name"}:
            raise MCPError("Unsupported MCP server setting; no commands or plaintext tokens are accepted")
        name = row.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,19}", name) or name in names:
            raise MCPError("MCP server names must be unique lower-case identifiers of at most 20 characters")
        names.add(name)
        url = row.get("url", "")
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port is not None
                     and 1024 <= parsed.port <= 65535 and parsed.netloc == f"127.0.0.1:{parsed.port}"
                     and not parsed.query and not parsed.fragment and parsed.path.startswith("/"))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise MCPError("MCP URL must be http://127.0.0.1:<port>/<path>, without credentials, query or fragment")
        if not isinstance(row.get("enabled", False), bool) or not isinstance(row.get("network", True), bool):
            raise MCPError("MCP server enabled and network must be booleans")
        secret = row.get("secret_name")
        if secret is not None and (not isinstance(secret, str) or not re.fullmatch(r"mcp-[a-z0-9_-]{1,30}-token", secret)):
            raise MCPError("MCP bearer credentials must name an mcp-...-token OS-keychain entry")
    return value


def server(config, name):
    value = settings(config)
    if not value.get("enabled", False):
        raise MCPError("MCP is disabled in config")
    row = next((r for r in value.get("servers", []) if r["name"] == name and r.get("enabled", False)), None)
    if not row:
        raise MCPError("MCP server is not explicitly configured and enabled")
    if row.get("network", True) and not config.get("network_plugins_enabled", False):
        raise MCPError("This MCP server is tagged network; network plugins are disabled")
    return row


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def check_schema(schema, depth=0):
    if depth > 8 or not isinstance(schema, dict) or set(schema) - SCHEMA_KEYS:
        raise MCPError("MCP schema outside supported bounded subset; $ref/combinators/custom keywords are not supported")
    types = schema.get("type")
    values = types if isinstance(types, list) else [types]
    if any(t not in {None, "object", "array", "string", "integer", "number", "boolean", "null"} for t in values):
        raise MCPError("Unsupported MCP schema type")
    props = schema.get("properties", {})
    if not isinstance(props, dict) or len(props) > 40:
        raise MCPError("MCP schema properties must be a bounded object")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(k, str) or k not in props for k in required):
        raise MCPError("Invalid MCP required properties")
    for child in props.values():
        check_schema(child, depth + 1)
    for key in ("items", "additionalProperties"):
        if key in schema and isinstance(schema[key], dict):
            check_schema(schema[key], depth + 1)
    if "items" in schema and not isinstance(schema["items"], dict):
        raise MCPError("MCP tuple/boolean item schemas are unsupported")
    if "additionalProperties" in schema and not isinstance(schema["additionalProperties"], (dict, bool)):
        raise MCPError("Invalid MCP additionalProperties")
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))):
            raise MCPError("Invalid MCP schema bound")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise MCPError("Invalid MCP enum")


def validate_value(schema, value, depth=0):
    if depth > 8:
        raise MCPError("MCP arguments exceed nesting limit")
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}
    declared = schema.get("type")
    choices = declared if isinstance(declared, list) else [declared]
    if declared is not None and not any(isinstance(value, types[t]) and not (t in {"integer", "number"} and isinstance(value, bool)) for t in choices):
        raise MCPError("MCP argument does not match its declared type")
    if "enum" in schema and value not in schema["enum"]:
        raise MCPError("MCP argument is outside its enum")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if any(k not in value for k in schema.get("required", [])):
            raise MCPError("Missing required MCP argument")
        for key, item in value.items():
            child = props.get(key, schema.get("additionalProperties", {}))
            if child is False:
                raise MCPError("Unexpected MCP argument")
            validate_value(child if isinstance(child, dict) else {}, item, depth + 1)
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise MCPError("MCP array length outside bounds")
        for item in value:
            validate_value(schema.get("items", {}), item, depth + 1)
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 16000):
        raise MCPError("MCP text length outside bounds")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not schema.get("minimum", float("-inf")) <= value <= schema.get("maximum", float("inf")):
            raise MCPError("MCP number outside bounds")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise MCPError("MCP redirects are blocked")


class Client:
    def __init__(self, config, row):
        self.config, self.row = config, row
        self.session, self.next_id = None, 0
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                        "Origin": "http://" + urlsplit(row["url"]).netloc}
        if row.get("secret_name"):
            token = get_secret(row["secret_name"])
            if not token or any(c in token for c in "\r\n"):
                raise MCPError("Configured MCP keychain credential is missing or invalid")
            self.headers["Authorization"] = "Bearer " + token

    def check_running(self):
        if (self.config["_root"] / "data" / "STOP").exists():
            raise RuntimeError("Global kill switch is active")

    def __enter__(self):
        try:
            result = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                 "clientInfo": {"name": "PRIMUS", "version": "1.0"}})
            if result.get("protocolVersion") != PROTOCOL or not isinstance(result.get("capabilities"), dict) or "tools" not in result["capabilities"]:
                raise MCPError("MCP server must negotiate 2025-11-25 and advertise tools")
            self.headers["MCP-Protocol-Version"] = PROTOCOL
            self.request("notifications/initialized", notification=True)
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.session:
            try:
                with self.opener.open(Request(self.row["url"], method="DELETE", headers=self.headers), timeout=2):
                    pass
            except (HTTPError, URLError, OSError, MCPError):
                pass
            self.session = None

    def request(self, method, params=None, notification=False):
        self.check_running()
        self.next_id += 1
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notification:
            message["id"] = self.next_id
        data = json.dumps(message, allow_nan=False).encode()
        if len(data) > 20000:
            raise MCPError("MCP request exceeds 20 KB")
        try:
            with self.opener.open(Request(self.row["url"], data=data, headers=self.headers), timeout=10) as response:
                session = response.headers.get("MCP-Session-Id")
                if method == "initialize" and session:
                    if not 1 <= len(session) <= 128 or any(not 0x21 <= ord(c) <= 0x7e for c in session):
                        raise MCPError("Invalid MCP session identifier")
                    self.session = session
                    self.headers["MCP-Session-Id"] = session
                if notification:
                    if response.status != 202:
                        raise MCPError("MCP notification must return 202")
                    return {}
                mime = response.headers.get_content_type()
                if mime not in {"application/json", "text/event-stream"}:
                    raise MCPError("Unsupported MCP response type")
                deadline, total, buffer = time.monotonic() + 10, 0, b""
                while True:
                    self.check_running()
                    if time.monotonic() > deadline:
                        raise MCPError("MCP response deadline exceeded; outcome may be unknown, no automatic retry")
                    part = response.read1(8192)
                    total += len(part)
                    if total > MAX_BODY:
                        raise MCPError("MCP response exceeds 256 KB")
                    buffer += part
                    if mime == "text/event-stream":
                        buffer = buffer.replace(b"\r\n", b"\n")
                        while b"\n\n" in buffer:
                            frame, buffer = buffer.split(b"\n\n", 1)
                            payload = b"\n".join(line[5:].lstrip(b" ") for line in frame.split(b"\n") if line.startswith(b"data:"))
                            if payload:
                                result = self.decode(payload, message["id"])
                                if result is not None:
                                    return result
                    if not part:
                        if mime == "application/json":
                            result = self.decode(buffer, message["id"])
                            if result is not None:
                                return result
                        raise MCPError("MCP stream ended without the requested result")
        except HTTPError as exc:
            raise MCPError(f"MCP HTTP error {exc.code}; no automatic tool retry") from None
        except (URLError, OSError) as exc:
            raise MCPError("MCP connection failed; tool outcome may be unknown. No automatic retry") from exc

    @staticmethod
    def decode(payload, identifier):
        try:
            reply = json.loads(payload)
        except (ValueError, UnicodeError):
            raise MCPError("MCP returned invalid JSON") from None
        if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
            raise MCPError("Invalid MCP JSON-RPC envelope")
        if "method" in reply:
            if "id" in reply:
                raise MCPError("MCP server-initiated requests are not supported; no sampling or elicitation performed")
            return None
        if reply.get("id") != identifier:
            raise MCPError("MCP response ID mismatch")
        if "error" in reply:
            raise MCPError("MCP server returned a protocol error; no automatic tool retry")
        if not isinstance(reply.get("result"), dict):
            raise MCPError("MCP result must be an object")
        return reply["result"]

    def tools(self):
        tools, cursor = [], None
        seen = set()
        for _ in range(4):
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            rows = page.get("tools")
            if not isinstance(rows, list) or len(tools) + len(rows) > 40:
                raise MCPError("MCP tool catalog is invalid or exceeds 40 tools")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", row["name"]):
                    raise MCPError("Invalid MCP tool name")
                if row["name"] in seen:
                    raise MCPError("Duplicate MCP tool name")
                schema = row.get("inputSchema")
                if not isinstance(schema, dict) or schema.get("type") != "object" or len(json.dumps(schema)) > 8000:
                    raise MCPError("MCP tool requires a bounded object input schema")
                check_schema(schema)
                if row.get("execution", {}).get("taskSupport") == "required":
                    raise MCPError("Asynchronous MCP task tools are not supported")
                seen.add(row["name"])
                tools.append({"name": row["name"], "description": str(row.get("description", ""))[:1000], "inputSchema": schema})
            cursor = page.get("nextCursor")
            if not cursor:
                if len(json.dumps(tools)) > 24000:
                    raise MCPError("MCP catalog exceeds 24 KB; expose a smaller tool set")
                return tools
            if not isinstance(cursor, str) or len(cursor) > 1000:
                raise MCPError("Invalid MCP catalog cursor")
        raise MCPError("MCP tool catalog exceeds four pages")


def list_tools(config, args):
    row = server(config, args["server"])
    with Client(config, row) as client:
        return {"server": row["name"], "tools": client.tools(), "note": "Untrusted metadata. All mcp_call actions require exact EXTERNAL approval regardless of server annotations."}


def reviewed_tool(config, args, client):
    tool = next((t for t in client.tools() if t["name"] == args["tool"]), None)
    if not tool:
        raise MCPError("MCP tool is no longer advertised")
    validate_value(tool["inputSchema"], args["arguments"])
    return fingerprint({"server": client.row, "tool": tool, "arguments": args["arguments"]})


def call_preview(config, args):
    if len(json.dumps(args["arguments"], allow_nan=False).encode()) > 16000:
        raise MCPError("MCP arguments exceed 16 KB")
    row = server(config, args["server"])
    with Client(config, row) as client:
        args["_mcp_reviewed_sha256"] = reviewed_tool(config, args, client)
    return (f"Call MCP tool {args['tool']} on configured server {row['name']} at {row['url']} with exact arguments "
            f"{json.dumps(args['arguments'], ensure_ascii=False)}. Definition/arguments SHA-256 {args['_mcp_reviewed_sha256']}. "
            "This server runs with its own OS permissions, outside PRIMUS's script sandbox; review and trust its implementation. "
            + ("The server is tagged network and may send data outside this laptop." if row.get("network", True) else "The server is configured local-only; this declaration is not OS network isolation."))


def call_tool(config, args):
    row = server(config, args["server"])
    with Client(config, row) as client:
        if reviewed_tool(config, args, client) != args.get("_mcp_reviewed_sha256"):
            raise MCPError("MCP definition, endpoint or arguments changed after review; request fresh approval")
        result = client.request("tools/call", {"name": args["tool"], "arguments": args["arguments"]})
        if len(json.dumps(result).encode()) > 12000:
            raise MCPError("MCP result exceeds 12 KB; action may have completed. No automatic retry")
        if result.get("isError"):
            raise MCPError("MCP tool reported failure: " + str(result.get("content", ""))[:600])
        return {"server": row["name"], "tool": args["tool"], "mcp_result": result,
                "note": "UNTRUSTED MCP TOOL DATA. A server response does not independently prove the external action succeeded."}
