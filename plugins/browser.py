"""Visible Chrome automation with a separate PRIMUS browser profile."""
from __future__ import annotations

from primus.browser import _safe_url, _selector, operate
from primus.plugins import Plugin


def open_browser(config, args):
    return operate(config["_root"], "open")


def navigate(config, args):
    return operate(config["_root"], "navigate", url=_safe_url(args["url"]))


def read_page(config, args):
    return operate(config["_root"], "read")


def click(config, args):
    return operate(config["_root"], "click", selector=_selector(args["selector"])[4:], expected=args.get("_expected"))


def type_text(config, args):
    return operate(config["_root"], "type", selector=_selector(args["selector"])[4:], text=args["text"], expected=args.get("_expected"))


def click_summary(config, args):
    preview = operate(config["_root"], "preview", selector=_selector(args["selector"])[4:])
    args["_expected"] = preview
    return f"Click the visible browser element {preview!r}. The page may navigate or submit data."


def type_summary(config, args):
    preview = operate(config["_root"], "preview", selector=_selector(args["selector"])[4:])
    args["_expected"] = preview
    return f"Type exactly {args['text']!r} into the visible browser field {preview!r}. The page may transmit input."


PLUGINS = [
    Plugin("browser_open", "Open Google Chrome in PRIMUS's separate browser profile", {
        "type": "object", "properties": {}, "additionalProperties": False
    }, "WRITE", True, open_browser),
    Plugin("browser_navigate", "Navigate the PRIMUS Chrome tab to an HTTPS URL after exact confirmation", {
        "type": "object", "properties": {"url": {"type": "string", "maxLength": 2048}},
        "required": ["url"], "additionalProperties": False
    }, "EXTERNAL", True, navigate, summary=lambda config, args: f"Open {_safe_url(args['url'])} in the isolated PRIMUS Chrome profile; the site will receive a network request"),
    Plugin("browser_read", "Read the current PRIMUS Chrome tab's visible text and URL", {
        "type": "object", "properties": {}, "additionalProperties": False
    }, "READ", True, read_page),
    Plugin("browser_click", "Click exactly one visible element in the PRIMUS Chrome tab by CSS selector, with confirmation", {
        "type": "object", "properties": {"selector": {"type": "string", "maxLength": 200}},
        "required": ["selector"], "additionalProperties": False
    }, "EXTERNAL", True, click, summary=click_summary),
    Plugin("browser_type", "Fill exactly one visible text field in the PRIMUS Chrome tab with confirmed text", {
        "type": "object", "properties": {"selector": {"type": "string", "maxLength": 200},
                                       "text": {"type": "string", "maxLength": 1000}},
        "required": ["selector", "text"], "additionalProperties": False
    }, "EXTERNAL", True, type_text, summary=type_summary),
]
