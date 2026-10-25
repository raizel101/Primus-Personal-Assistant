"""Local application launch and consent-gated keyboard/mouse controls."""
from __future__ import annotations

from primus.desktop import APP_NAMES, list_apps, open_app
from primus.plugins import Plugin


def available(config: dict, args: dict) -> dict:
    return {"applications": list_apps()}


def launch(config: dict, args: dict) -> dict:
    return open_app(config, args["name"])


def _check_input(args: dict) -> None:
    action = args["action"]
    required = {"type": {"action", "text"}, "hotkey": {"action", "keys"},
                "click": {"action", "x", "y"}}[action]
    if set(args) != required:
        raise ValueError(f"{action} requires exactly {sorted(required)}")
    if action == "type" and not args["text"]:
        raise ValueError("Text cannot be empty")
    if action == "hotkey":
        names = args["keys"]
        allowed = {"ctrl", "alt", "shift", "tab", "enter", "esc"}
        if not 1 <= len(names) <= 3 or len(set(names)) != len(names):
            raise ValueError("Hotkey must contain one to three distinct keys")
        if any(name not in allowed and not (len(name) == 1 and name.isascii() and name.isalnum()) for name in names):
            raise ValueError("Unsupported hotkey key")
    if action == "click" and (abs(args["x"]) > 20000 or abs(args["y"]) > 20000):
        raise ValueError("Click coordinates are outside the supported desktop range")


def input_action(config: dict, args: dict) -> dict:
    _check_input(args)
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    from pynput.keyboard import Controller as Keyboard, Key
    from pynput.mouse import Button, Controller as Mouse
    kind = args["action"]
    if kind == "type":
        Keyboard().type(args["text"])
    elif kind == "hotkey":
        keys = {"ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift,
                "tab": Key.tab, "enter": Key.enter, "esc": Key.esc}
        names = args["keys"]
        keyboard = Keyboard()
        pressed = []
        try:
            for name in names:
                key = keys.get(name, name)
                keyboard.press(key)
                pressed.append(key)
        finally:
            for key in reversed(pressed):
                keyboard.release(key)
    elif kind == "click":
        mouse = Mouse()
        mouse.position = (args["x"], args["y"])
        mouse.click(Button.left, 1)
    else:
        raise ValueError("Unknown desktop action")
    return {"action": kind, "sent": True, "verified": False,
            "note": "Input was sent to the foreground desktop; check the target app for the result."}


def input_summary(config: dict, args: dict) -> str:
    _check_input(args)
    action = args["action"]
    if action == "type":
        return f"Type exactly {args['text']!r} into the currently focused window"
    if action == "hotkey":
        return f"Press {'+'.join(args['keys'])} in the currently focused window"
    return f"Click screen coordinate ({args['x']}, {args['y']}) in the currently focused desktop"


PLUGINS = [
    Plugin("app_list", "List supported local desktop apps and whether each is installed", {
        "type": "object", "properties": {}, "additionalProperties": False
    }, "READ", False, available),
    Plugin("app_open", "Open a supported local desktop app, such as Google Chrome; use app_list if unsure", {
        "type": "object", "properties": {"name": {"type": "string", "enum": list(APP_NAMES)}},
        "required": ["name"], "additionalProperties": False
    }, "WRITE", False, launch),
    Plugin("desktop_input", "Send an exact mouse or keyboard action to the visible desktop only after interactive confirmation", {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": ["type", "hotkey", "click"]},
            "text": {"type": "string", "maxLength": 500},
            "keys": {"type": "array", "items": {"type": "string", "maxLength": 5}},
            "x": {"type": "integer"}, "y": {"type": "integer"}},
        "required": ["action"], "additionalProperties": False
    }, "EXTERNAL", False, input_action, summary=input_summary),
]
