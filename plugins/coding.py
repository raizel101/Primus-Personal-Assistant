"""Small, verifiable coding operations inside approved folders."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile

from primus.plugins import Plugin
from primus.security import guard_path


PATH = {"type": "string", "minLength": 1, "maxLength": 4096}
SNIPPET = {"type": "string", "minLength": 1, "maxLength": 4000}
MAX_CODE_BYTES = 100_000


def _source(config: dict, raw: str, *, write: bool = False) -> tuple[Path, bytes]:
    path = guard_path(raw, config, write=write)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Choose an existing regular code file")
    if path.stat().st_size > MAX_CODE_BYTES:
        raise ValueError("Code file exceeds the 100 KB limit")
    return path, path.read_bytes()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_summary(config: dict, args: dict) -> str:
    path, data = _source(config, args["path"], write=True)
    old, new = args["old"], args["new"]
    source = data.decode("utf-8")
    if source.count(old) != 1:
        raise ValueError("The old snippet must occur exactly once")
    if old == new:
        raise ValueError("Replacement must change the file")
    args["_reviewed_sha256"] = _digest(data)
    return (f"In {path}, replace exactly one occurrence of the supplied old snippet with the supplied "
            f"new snippet. Current SHA-256: {args['_reviewed_sha256']}. "
            "The full snippets appear in the action arguments; the change is rejected if the file changes before approval.")


def patch_code(config: dict, args: dict) -> dict:
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    path, data = _source(config, args["path"], write=True)
    expected = args.get("_reviewed_sha256")
    if not isinstance(expected, str) or _digest(data) != expected:
        raise ValueError("Code file changed after review; request a new approval")
    source = data.decode("utf-8")
    if source.count(args["old"]) != 1:
        raise ValueError("The old snippet must occur exactly once")
    updated = source.replace(args["old"], args["new"], 1).encode("utf-8")
    if len(updated) > MAX_CODE_BYTES:
        raise ValueError("Updated code exceeds the 100 KB limit")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".primus-code-", delete=False) as handle:
            temp_name = handle.name
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck just before replacement to reject a change during approval.
        if _digest(path.read_bytes()) != expected:
            raise ValueError("Code file changed during patch; request a new approval")
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    if path.read_bytes() != updated:
        raise OSError("Read-back verification failed")
    return {"path": str(path), "bytes": len(updated), "sha256": _digest(updated), "verified": True}


def check_code(config: dict, args: dict) -> dict:
    path, data = _source(config, args["path"])
    source = data.decode("utf-8")
    try:
        if path.suffix.lower() == ".py":
            ast.parse(source, filename=str(path))
            kind = "python_syntax"
        elif path.suffix.lower() == ".json":
            json.loads(source)
            kind = "json_syntax"
        else:
            raise ValueError("Static syntax check supports .py and .json files only")
    except (SyntaxError, json.JSONDecodeError) as exc:
        return {"path": str(path), "check": path.suffix.lower(), "valid": False,
                "line": exc.lineno, "error": str(exc), "sha256": _digest(data)}
    return {"path": str(path), "check": kind, "valid": True, "sha256": _digest(data)}


PLUGINS = [
    Plugin("code_patch", "Replace one exact code snippet in an existing approved file; exact approval and hash recheck required", {
        "type": "object", "properties": {"path": PATH, "old": SNIPPET, "new": SNIPPET},
        "required": ["path", "old", "new"], "additionalProperties": False
    }, "DESTRUCTIVE", False, patch_code, summary=patch_summary),
    Plugin("code_check", "Check Python or JSON syntax without running code", {
        "type": "object", "properties": {"path": PATH}, "required": ["path"], "additionalProperties": False
    }, "READ", False, check_code),
]
