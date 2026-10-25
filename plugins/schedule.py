"""Schedule one-time or recurring reminders and safe assistant tasks."""
from __future__ import annotations
from primus.plugins import Plugin
from primus.storage import Store

def add_schedule(config, args):
    identifier = Store(config["_root"]).add_schedule(args["kind"], args["content"], args["when"], args.get("repeat_seconds"))
    return {"id": identifier, "scheduled": True, "when": args["when"]}

def list_schedules(config, args):
    return {"schedules": Store(config["_root"]).list_schedules()}

def disable_schedule(config, args):
    Store(config["_root"]).disable_schedule(args["id"])
    return {"id": args["id"], "disabled": True}

TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
PLUGINS = [
    Plugin("schedule_add", "Schedule a reminder or task using an ISO 8601 time with UTC offset; confirmation is required", {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["reminder", "agent"]},
        "content": TEXT,
        "when": {"type": "string", "minLength": 20, "maxLength": 40},
        "repeat_seconds": {"type": "integer"}
    }, "required": ["kind", "content", "when"], "additionalProperties": False}, "DESTRUCTIVE", False, add_schedule),
    Plugin("schedule_list", "List local scheduled reminders and tasks", {"type": "object", "properties": {}, "additionalProperties": False}, "READ", False, list_schedules),
    Plugin("schedule_disable", "Disable a scheduled reminder or task after confirmation", {"type": "object", "properties": {"id": {"type": "string", "minLength": 32, "maxLength": 32}}, "required": ["id"], "additionalProperties": False}, "DESTRUCTIVE", False, disable_schedule),
]
