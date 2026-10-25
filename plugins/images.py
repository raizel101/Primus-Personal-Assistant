"""Analyze one approved local image with PRIMUS's offline vision model."""
from primus.image_input import analyze_image
from primus.plugins import Plugin

PLUGINS = [Plugin(
    "image_analyze", "Analyze an approved image: scene overview, detail, legible text, or local hand gestures; optional normalized focus region",
    {"type": "object", "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 4096},
        "question": {"type": "string", "minLength": 1, "maxLength": 300},
        "mode": {"type": "string", "enum": ["scene", "detail", "text", "gestures"]},
        "region": {"type": "array", "items": {"type": "number"}}},
     "required": ["path"], "additionalProperties": False},
    "READ", False, analyze_image,
)]
