"""Append-only JSONL tool audit with content redaction."""
from __future__ import annotations
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

SENSITIVE_KEYS = {"content", "text", "body", "token", "password", "secret", "api_key", "authorization", "stdout", "stderr", "arguments", "error", "query", "url", "href", "subject", "from", "snippet", "old", "new", "summary", "email", "title", "label", "last_app", "last_file", "last_folder", "last_reminder_id", "last_target", "question", "observation", "request", "tasks", "hands", "landmarks"}
_THREAD_LOCK = threading.Lock()
SENSITIVE_KEYS.update({"mcp_result", "inputSchema", "description"})


def _lock_file(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_file(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_UN)

def redact(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                data = str(item).encode("utf-8")
                result[key] = {"redacted": True, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…[truncated]"
    return value

def append_audit(root: Path, tool: str, arguments: dict, result: dict) -> None:
    path = root / "logs" / "audit.jsonl"
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "arguments": redact(arguments),
        "result": redact(result),
    }
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _THREAD_LOCK:
        lock_descriptor = os.open(root / "logs" / "audit.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _lock_file(lock_descriptor)
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                try:
                    data = line.encode("utf-8")
                    while data:
                        data = data[os.write(descriptor, data):]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                _unlock_file(lock_descriptor)
        finally:
            os.close(lock_descriptor)
