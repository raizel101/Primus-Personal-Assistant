# Add a PRIMUS ability

Place a trusted Python file under `plugins/`. An ability can fit under 20 lines:

```python
from primus.plugins import Plugin
from primus.security import guard_path

def run(config, args):
    path = guard_path(args["path"], config)
    return {"exists": path.exists(), "path": str(path)}

PLUGINS = [Plugin(
    "file_exists", "Check whether an approved path exists",
    {"type": "object", "properties": {"path": {"type": "string"}},
     "required": ["path"], "additionalProperties": False},
    "READ", False, run
)]
```

Each plugin declares a name, description, JSON-schema input, permission tier (READ, WRITE, DESTRUCTIVE, EXTERNAL), network flag, and `run(config, args)`. Network plugins are omitted unless `network_plugins_enabled` is true or their names are individually enabled by a reviewed config setting; the built-in browser is the only individual opt-in. EXTERNAL actions always need exact confirmation. Guard all file paths with `guard_path`; do not log secrets or embed credentials. Third-party plugin code executes during discovery, so review it before placing it in this directory.

For MCP abilities, configure a reviewed localhost server and use `mcp_servers`, `mcp_list` and `mcp_call`; no core rewrite is needed for a new server tool. This bridge discovers tool schemas at use time rather than turning third-party metadata into trusted system instructions. Every imported tool invocation is EXTERNAL regardless of read-only annotations. Localhost transport does not sandbox the server or prove that it cannot access the internet. See [MCP/RAG guide](MCP_RAG_GUIDE.md) for the supported protocol and bounded schema subset.

For document memory, use `knowledge_index`, `knowledge_search`, `knowledge_read`, `knowledge_list` and `knowledge_forget`. Persistence/reindexing and removal require exact confirmation; the reviewed source hash is rechecked. Retrieval uses approved current source versions and returns citation labels, file paths and PDF page numbers where available. Keep retrieved text in the untrusted-data channel. Do not infer permission to save conversation history or ingest a whole folder.

For desktop abilities, keep model-selected inputs narrow. The bundled `app_open` uses a fixed installed-app catalog; never pass a model-provided executable path into `subprocess`. Keyboard and mouse actions require EXTERNAL confirmation and must report when the visible result cannot be verified.

For user scripts, use `script_run` rather than invoking an interpreter from a plugin. It mounts only an approved `.py` or `.sh` script and explicitly named input files into a network-isolated Bubblewrap namespace, and writes results under `outputs/script-runs`. It requires exact EXTERNAL consent. Do not add a raw `shell=True` plugin or run Windows executables outside the sandbox.

For coding abilities, use `file_write` for new files, `code_patch` for one exact change to an existing file after confirmation, and `code_check` for local Python/JSON syntax. Keep execution in `script_run`; never treat a static parse as a functional test.


### Formal planning computations

`plugins/planning.py` exposes `formal_plan` with `{ "path": "examples/planning/downloads.json" }` and `formal_validate` with the same path plus an `actions` array of ordered symbolic action names. Both are offline READ tools. They validate input with the bounded STRIPS model parser after approved-path checks. Returned transitions are predictions only and must never be interpreted as user approvals or actual tool calls. Use the existing permission and observed-result workflow for real effects. See `FORMAL_PLANNING_GUIDE.md`.
