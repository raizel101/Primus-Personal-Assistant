"""Extract brief reusable references without saving requests or transcripts."""
from __future__ import annotations

from pathlib import Path
import re

from .security import SecurityError, guard_path
from .storage import assert_no_secret

PRIVATE_ROOT_DIRS = {"data", "logs", "models", "plugins", "scripts", "src", "temp", "tests", "vendor"}


def _safe_label(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or len(value) > 120 or any(ord(char) < 32 for char in value):
        return None
    try:
        assert_no_secret(value)
    except ValueError:
        return None
    return value


def _safe_path(raw, config: dict) -> str | None:
    try:
        path = guard_path(raw, config)
    except (SecurityError, TypeError, ValueError, OSError):
        return None
    root: Path = config["_root"]
    if path.is_relative_to(root):
        relative = path.relative_to(root)
        if not relative.parts or relative.parts[0].lower() in PRIVATE_ROOT_DIRS:
            return None
        value = str(relative)
    else:
        value = str(path)
    if len(value) > 290:
        return None
    return _safe_label(value)


def context_from_tool(name: str, args: dict, result: dict, config: dict) -> dict[str, str]:
    """Return only whitelisted targets from a successfully completed tool call."""
    if name in {"app_open", "installed_app_open"} and result.get("window_verified"):
        app = _safe_label(args.get("name"))
        return {"last_app": app, "last_target": f"app:{app}"} if app else {}
    if name in {"file_read", "file_write", "file_overwrite", "file_list"}:
        path = _safe_path(result.get("path"), config)
        if path:
            kind = "folder" if name == "file_list" else "file"
            return {f"last_{kind}": path, "last_target": f"{kind}:{path}"}
    if name == "window_manage" and result.get("verified"):
        app = _safe_label(result.get("window", {}).get("application"))
        return {"last_app": app, "last_target": f"app:{app}"} if app else {}
    if name == "schedule_add" and args.get("kind") == "reminder" and result.get("scheduled"):
        identifier = result.get("id")
        if isinstance(identifier, str) and re.fullmatch(r"[0-9a-f]{32}", identifier):
            return {"last_reminder_id": identifier, "last_target": f"reminder:{identifier}"}
    return {}
