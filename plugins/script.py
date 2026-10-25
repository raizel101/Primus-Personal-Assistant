"""Sandboxed Python and shell scripts from approved folders."""
from __future__ import annotations

import hashlib
from primus.plugins import Plugin
from primus.script_runner import input_files, new_run_id, run_script, script_source, project_manifest


def summary(config: dict, args: dict) -> str:
    if not config.get("script_execution", {}).get("enabled", False):
        raise RuntimeError("Script execution is disabled in config")
    script = script_source(config, args["path"])
    arguments = args.get("arguments", [])
    inputs = input_files(config, args.get("inputs", []))
    if len(arguments) > 20 or any(len(item) > 200 or "\0" in item for item in arguments):
        raise ValueError("Use at most 20 arguments of 200 characters each")
    args["_run_id"] = new_run_id()
    args["_expected_hashes"] = [hashlib.sha256(path.read_bytes()).hexdigest() for path in [script, *inputs]]
    output = config["_root"] / "outputs" / "script-runs" / args["_run_id"]
    project_description = ""
    if args.get("project_root"):
        args["_project_manifest"] = project_manifest(config, args["project_root"], script)
        project_description = (f"Project root {args['project_root']!r} is snapshotted read-only at /work/project; "
                               f"all project files and SHA-256 hashes: {args['_project_manifest']!r}. "
                               "Working directory is /work/project; relative imports and data paths are preserved. "
                               "Write generated output only under PRIMUS_OUTPUT_DIR=/work/output. ")
    return (f"Run {script} (SHA-256 {args['_expected_hashes'][0]}) with arguments {arguments!r} "
            "inside an offline filesystem sandbox. "
            + project_description +
            f"Input files are mounted read-only as {[(str(path), '/work/inputs/' + str(index)) for index, path in enumerate(inputs)]!r}; "
            f"input SHA-256 hashes are {args['_expected_hashes'][1:]!r}; "
            f"generated files go to {output}. "
            "Time limit 25 seconds, memory limit 1 GB, per-file size limit 10 MB.")


PLUGINS = [Plugin("script_run", "Run a Python or shell script from an approved folder in an offline Bubblewrap sandbox; exact approval required", {
    "type": "object", "properties": {
        "path": {"type": "string", "minLength": 1, "maxLength": 4096},
        "arguments": {"type": "array", "items": {"type": "string", "maxLength": 200}},
        "inputs": {"type": "array", "items": {"type": "string", "maxLength": 4096}},
        "project_root": {"type": "string", "minLength": 1, "maxLength": 4096}
    }, "required": ["path"], "additionalProperties": False
}, "EXTERNAL", False, run_script, summary=summary)]
