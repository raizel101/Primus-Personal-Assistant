"""Inspect and activate Windows app controls through the local accessibility API."""
from __future__ import annotations

from primus.accessibility import activate, controls, describe_activation, describe_text, set_text
from primus.plugins import Plugin


WINDOW = {
    "id": {"type": "string", "maxLength": 18},
    "pid": {"type": "integer"},
    "title": {"type": "string", "minLength": 1, "maxLength": 512},
}

PLUGINS = [
    Plugin("ui_controls", "List visible accessibility controls in a specific listed Windows app window; skip password fields and do not request field values", {
        "type": "object", "properties": {**WINDOW,
            "query": {"type": "string", "maxLength": 100}},
        "required": ["id", "pid", "title"], "additionalProperties": False,
    }, "READ", False, controls),
    Plugin("ui_activate", "Activate one control returned by ui_controls after exact user confirmation; report the app result as unverified", {
        "type": "object", "properties": {**WINDOW,
            "element_id": {"type": "string", "minLength": 1, "maxLength": 160}},
        "required": ["id", "pid", "title", "element_id"], "additionalProperties": False,
    }, "EXTERNAL", False, activate, summary=describe_activation),
    Plugin("ui_set_text", "Replace text in a non-password Edit field returned by ui_controls after exact user confirmation", {
        "type": "object", "properties": {**WINDOW,
            "element_id": {"type": "string", "minLength": 1, "maxLength": 160},
            "text": {"type": "string", "minLength": 1, "maxLength": 500}},
        "required": ["id", "pid", "title", "element_id", "text"], "additionalProperties": False,
    }, "EXTERNAL", False, set_text, summary=describe_text),
]
