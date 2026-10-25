"""Long-term facts and preferences, each mutation confirmed by the user."""
from __future__ import annotations
import json
import hashlib
from primus.plugins import Plugin
from primus.storage import Store

def list_memory(config, args):
    return {"memories": Store(config["_root"]).list_memories()}

def add_memory(config, args):
    identifier = Store(config["_root"]).add_memory(args["kind"], args["content"], approved=True)
    return {"id": identifier, "saved": True}

def edit_memory(config, args):
    Store(config["_root"]).edit_memory(args["id"], args["content"], approved=True)
    return {"id": args["id"], "edited": True}

def delete_memory(config, args):
    Store(config["_root"]).delete_memory(args["id"], approved=True)
    return {"id": args["id"], "deleted": True}


def list_context(config, args):
    settings = config.get("memory", {})
    return {"context": Store(config["_root"]).get_context(settings.get("context_retention_days", 30))}


def clear_context(config, args):
    store = Store(config["_root"])
    current = store.get_context(config.get("memory", {}).get("context_retention_days", 30))
    if _context_digest(current) != args.get("_expected_context_sha256"):
        raise ValueError("Task context changed during approval; review it again")
    store.clear_context(approved=True)
    return {"cleared": True}


def clear_context_summary(config, args):
    context = list_context(config, {})["context"]
    args["_expected_context_sha256"] = _context_digest(context)
    return ("Delete these recent task context entries: " + json.dumps(context, ensure_ascii=False) +
            ". Approved facts and task history remain")


def _context_digest(context):
    payload = json.dumps(context, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
ID = {"type": "string", "minLength": 32, "maxLength": 32}
PLUGINS = [
    Plugin("memory_list", "View explicitly approved local facts and preferences", {"type": "object", "properties": {}, "additionalProperties": False}, "READ", False, list_memory),
    Plugin("memory_add", "Save a fact or preference only after explicit user approval", {"type": "object", "properties": {"kind": {"type": "string", "enum": ["fact", "preference"]}, "content": TEXT}, "required": ["kind", "content"], "additionalProperties": False}, "DESTRUCTIVE", False, add_memory),
    Plugin("memory_edit", "Edit an approved memory after explicit user approval", {"type": "object", "properties": {"id": ID, "content": TEXT}, "required": ["id", "content"], "additionalProperties": False}, "DESTRUCTIVE", False, edit_memory),
    Plugin("memory_delete", "Delete an approved memory after explicit user approval", {"type": "object", "properties": {"id": ID}, "required": ["id"], "additionalProperties": False}, "DESTRUCTIVE", False, delete_memory),
    Plugin("context_list", "View brief recent task context saved without requests or transcripts", {"type": "object", "properties": {}, "additionalProperties": False}, "READ", False, list_context),
    Plugin("context_clear", "Forget all brief task context after explicit user approval", {"type": "object", "properties": {}, "additionalProperties": False}, "DESTRUCTIVE", False, clear_context, summary=clear_context_summary),
]
