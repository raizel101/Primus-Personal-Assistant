"""Offline formal planning tools; READ computations never invoke planned actions."""
from primus.planning import plan_file, validate_file
from primus.plugins import Plugin

PATH = {"type": "string", "minLength": 1, "maxLength": 2048}

PLUGINS = [
    Plugin("formal_plan", "Solve an approved JSON grounded STRIPS problem using preconditions and add/delete effects. Returns a replay-validated symbolic plan, not executed actions or permissions.",
           {"type": "object", "properties": {"path": PATH}, "required": ["path"], "additionalProperties": False},
           "READ", False, plan_file),
    Plugin("formal_validate", "Replay a proposed list of action names against an approved JSON STRIPS problem. Checks every precondition, effect and final goal. Never executes actions.",
           {"type": "object", "properties": {"path": PATH, "actions": {"type": "array", "items": {"type": "string", "maxLength": 48}}},
            "required": ["path", "actions"], "additionalProperties": False}, "READ", False, validate_file),
]
