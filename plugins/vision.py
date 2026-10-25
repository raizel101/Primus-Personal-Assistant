"""Describe a selected app window with a local vision model."""
from primus.plugins import Plugin
from primus.vision import inspect_window

PLUGINS = [Plugin(
    "window_vision",
    "Inspect the active or an exactly listed app window visually with a local vision model; no screenshot is saved. Never use on credential screens",
    {"type": "object", "properties": {
        "id": {"type": "string", "maxLength": 18},
        "pid": {"type": "integer"},
        "title": {"type": "string", "minLength": 1, "maxLength": 512},
        "question": {"type": "string", "minLength": 1, "maxLength": 300}},
     "additionalProperties": False},
    "READ", False, inspect_window,
)]
