"""One audited entry point for direct CLI and API image requests."""
from __future__ import annotations

import json

from .audit import append_audit
from .plugins import validate
from .security import authorize


def run_image(config: dict, plugins: dict, store, args: dict) -> dict:
    plugin = plugins["image_analyze"]
    validate(plugin.input_schema, args)
    authorize(config, plugin.tier, plugin.name, args, plugin.description, None)
    task_id = store.start_task("image_analyze:" + json.dumps(args, sort_keys=True))
    try:
        result = plugin.run(config, args)
        append_audit(config["_root"], plugin.name, args, {"ok": True, **result})
        store.finish_task(task_id, "done", result["observation"])
        return result
    except Exception as exc:
        append_audit(config["_root"], plugin.name, args, {"ok": False, "error": str(exc)})
        store.finish_task(task_id, "failed", str(exc))
        raise
