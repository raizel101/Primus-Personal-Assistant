"""Bounded Windows accessibility bridge for visible app controls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from .windowing import resolve_window

SENSITIVE_WINDOWS = ("credential manager", "windows security", "password manager",
                     "bitwarden", "keepass", "1password", "dashlane", "lastpass", "keychain")


def _safe_window(args: dict):
    window = resolve_window(args)
    identity = f"{window.title} {window.application}".lower()
    if any(name in identity for name in SENSITIVE_WINDOWS):
        raise PermissionError("Credential and password manager windows are blocked")
    return window


def _backend(config: dict, request: dict) -> dict:
    if sys.platform != "win32":
        raise NotImplementedError("Desktop accessibility currently supports Windows only")
    root: Path = config["_root"]
    script = root / "scripts" / "uia.ps1"
    if not script.is_file():
        raise RuntimeError("PRIMUS accessibility helper is missing")
    shell = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    environment = os.environ.copy()
    environment.update({"TEMP": str(root / "temp"), "TMP": str(root / "temp"),
                        "POWERSHELL_TELEMETRY_OPTOUT": "1"})
    completed = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        input=json.dumps(request), text=True, capture_output=True, cwd=root,
        env=environment, timeout=10, check=False,
    )
    if len(completed.stdout) > 150_000:
        raise RuntimeError("Accessibility response exceeded limit")
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Accessibility helper returned an invalid response") from exc
    if completed.returncode or not result.get("ok"):
        raise RuntimeError(str(result.get("error", "Accessibility operation failed"))[:200])
    return result


def _request(args: dict, mode: str) -> dict:
    window = _safe_window(args)
    return {"mode": mode, "window_id": window.id, "pid": window.pid}


def controls(config: dict, args: dict) -> dict:
    request = _request(args, "list")
    request["query"] = args.get("query", "")
    result = _backend(config, request)
    return {"window": {"id": request["window_id"], "pid": request["pid"]},
            "controls": result["controls"], "truncated": result["truncated"]}


def _preview_control(config: dict, args: dict):
    window = _safe_window(args)
    result = _backend(config, {"mode": "list", "window_id": window.id, "pid": window.pid,
                               "element_id": args["element_id"], "query": ""})
    match = next((row for row in result["controls"] if row["element_id"] == args["element_id"]), None)
    if match is None or not match["enabled"] or not match["visible"]:
        raise ValueError("Control is unavailable; inspect the window controls again")
    args["_expected_control"] = {"label": match["label"],
                                 "control_type": match["control_type"],
                                 "operation": match["operation"]}
    return window, match


def describe_activation(config: dict, args: dict) -> str:
    window, match = _preview_control(config, args)
    label = match["label"] or "unnamed control"
    return (f"{match['operation'].capitalize()} {match['control_type']} {label!r} in window {window.title!r} "
            f"({window.application}, PID {window.pid}, id {window.id}). "
            "The app's final result may not be verifiable")


def activate(config: dict, args: dict) -> dict:
    request = _request(args, "activate")
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    expected = args.get("_expected_control")
    if not isinstance(expected, dict):
        raise ValueError("Control activation requires an exact approval preview")
    request.update({"element_id": args["element_id"], "expected": expected})
    result = _backend(config, request)
    return {"element_id": args["element_id"], "sent": bool(result["sent"]),
            "verified": False,
            "note": "Control activation was sent; check the app for the resulting action"}


def describe_text(config: dict, args: dict) -> str:
    if not isinstance(args.get("text"), str) or not args["text"]:
        raise ValueError("Replacement text must be non-empty")
    window, match = _preview_control(config, args)
    if match["control_type"] != "Edit":
        raise ValueError("Target is not an editable field")
    label = match["label"] or "unnamed field"
    return (f"Replace text in Edit {label!r} of window {window.title!r} "
            f"({window.application}, PID {window.pid}, id {window.id}) "
            f"with exactly {args['text']!r}. Existing field text will be overwritten")


def set_text(config: dict, args: dict) -> dict:
    request = _request(args, "set_text")
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    expected = args.get("_expected_control")
    if not isinstance(expected, dict) or expected.get("control_type") != "Edit":
        raise ValueError("Text replacement requires an exact editable-field approval preview")
    request.update({"element_id": args["element_id"], "expected": expected,
                    "text": args["text"]})
    result = _backend(config, request)
    return {"element_id": args["element_id"], "sent": bool(result["sent"]),
            "verified": False,
            "note": "Text replacement was sent; check the app for the resulting action"}
