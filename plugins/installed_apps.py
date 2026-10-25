"""Search and launch current-user Windows Start apps by exact identity."""
from __future__ import annotations

from primus.installed_apps import launch_installed, launch_summary, search_installed
from primus.plugins import Plugin


PLUGINS = [
    Plugin("installed_app_search", "Search installed Windows Start apps by name; excludes web and protocol shortcuts", {
        "type": "object", "properties": {"query": {"type": "string", "maxLength": 100}},
        "required": ["query"], "additionalProperties": False
    }, "READ", False, lambda config, args: search_installed(config, args["query"])),
    Plugin("installed_app_open", "Open an installed app by exact name and Start app id after confirmation", {
        "type": "object", "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 160},
            "id": {"type": "string", "minLength": 1, "maxLength": 512}
        }, "required": ["name", "id"], "additionalProperties": False
    }, "EXTERNAL", False, launch_installed, summary=launch_summary),
]
