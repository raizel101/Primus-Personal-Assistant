"""List and manage visible Windows desktop windows after exact consent."""
from __future__ import annotations

from primus.plugins import Plugin
from primus.windowing import describe_action, describe_input, list_windows, manage_window, send_window_input


def inventory(config: dict, args: dict) -> dict:
    return {"windows": [window.public() for window in list_windows()]}


PLUGINS = [
    Plugin("window_list", "List visible desktop windows with id, title, PID, and application", {
        "type": "object", "properties": {}, "additionalProperties": False
    }, "READ", False, inventory),
    Plugin("window_manage", "Focus, minimize, restore, maximize, snap left/right, or close a listed window after exact confirmation", {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": ["focus", "minimize", "restore", "maximize", "snap_left", "snap_right", "close"]},
            "id": {"type": "string", "maxLength": 18},
            "pid": {"type": "integer"},
            "title": {"type": "string", "minLength": 1, "maxLength": 512}
        }, "required": ["action", "id", "pid", "title"], "additionalProperties": False
    }, "EXTERNAL", False, manage_window, summary=lambda config, args: describe_action(args)),
    Plugin("window_input", "Type exact text or send a limited hotkey to a listed window after exact confirmation", {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": ["type", "hotkey"]},
            "id": {"type": "string", "maxLength": 18},
            "pid": {"type": "integer"},
            "title": {"type": "string", "minLength": 1, "maxLength": 512},
            "text": {"type": "string", "minLength": 1, "maxLength": 500},
            "keys": {"type": "array", "items": {"type": "string", "maxLength": 5}}
        }, "required": ["action", "id", "pid", "title"], "additionalProperties": False
    }, "EXTERNAL", False, send_window_input, summary=lambda config, args: describe_input(args)),
]
