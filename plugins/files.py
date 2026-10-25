"""Guarded file operations. No delete or implicit overwrite."""
from __future__ import annotations
import hashlib
import os
import tempfile
from primus.plugins import Plugin
from primus.security import guard_path

PATH = {"type": "string", "minLength": 1, "maxLength": 4096}
CONTENT = {"type": "string", "maxLength": 1000000}

def read(config: dict, args: dict) -> dict:
    path = guard_path(args["path"], config)
    if not path.is_file():
        raise ValueError("File does not exist")
    if path.stat().st_size > 100000:
        raise ValueError("File exceeds 100 KB read limit")
    return {"path": str(path), "content": path.read_text(encoding="utf-8")}

def list_dir(config: dict, args: dict) -> dict:
    path = guard_path(args["path"], config)
    if not path.is_dir():
        raise ValueError("Directory does not exist")
    entries = [{"name": item.name, "kind": "dir" if item.is_dir() else "file"} for item in sorted(path.iterdir(), key=lambda x: x.name.lower())][:200]
    return {"path": str(path), "entries": entries}

def write_new(config: dict, args: dict) -> dict:
    path = guard_path(args["path"], config, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(args["content"])
    actual = path.read_text(encoding="utf-8")
    if actual != args["content"]:
        raise OSError("Read-back verification failed")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "verified": True}

def overwrite(config: dict, args: dict) -> dict:
    path = guard_path(args["path"], config, write=True)
    if not path.is_file():
        raise ValueError("Overwrite target must be an existing file")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".primus-", delete=False) as handle:
            temp_name = handle.name
            handle.write(args["content"])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    actual = path.read_text(encoding="utf-8")
    if actual != args["content"]:
        raise OSError("Read-back verification failed")
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "verified": True}

def overwrite_summary(config: dict, args: dict) -> str:
    path = guard_path(args["path"], config, write=True)
    return f"Replace the entire existing file {path} with {len(args['content'].encode('utf-8'))} UTF-8 bytes"

PLUGINS = [
    Plugin("file_read", "Read a UTF-8 text file in an approved folder", {"type": "object", "properties": {"path": PATH}, "required": ["path"], "additionalProperties": False}, "READ", False, read),
    Plugin("file_list", "List up to 200 entries in an approved folder", {"type": "object", "properties": {"path": PATH}, "required": ["path"], "additionalProperties": False}, "READ", False, list_dir),
    Plugin("file_write", "Create a new UTF-8 file and verify it; never overwrite", {"type": "object", "properties": {"path": PATH, "content": CONTENT}, "required": ["path", "content"], "additionalProperties": False}, "WRITE", False, write_new),
    Plugin("file_overwrite", "Replace an existing UTF-8 file after explicit confirmation", {"type": "object", "properties": {"path": PATH, "content": CONTENT}, "required": ["path", "content"], "additionalProperties": False}, "DESTRUCTIVE", False, overwrite, summary=overwrite_summary),
]
