"""Fixed read-only shell commands; no user-supplied shell syntax."""
from __future__ import annotations
import subprocess
import time
from primus.platform import adapter
from primus.plugins import Plugin
from primus.security import guard_path

def run(config: dict, args: dict) -> dict:
    path = guard_path(args.get("path", "."), config)
    if not path.is_dir():
        raise ValueError("Working directory does not exist")
    command = args["command"]
    if command not in {"pwd", "list", "python_version"}:
        raise ValueError("Unsupported shell command")
    argv = adapter().shell_argv(command)
    process = subprocess.Popen(argv, cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False)
    try:
        deadline = time.monotonic() + 15
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("Shell inspection timed out after 15 seconds")
            if (config["_root"] / "data" / "STOP").exists():
                process.kill()
                process.communicate()
                raise RuntimeError("Global kill switch is active")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    if process.returncode != 0:
        raise OSError(f"Command exited {process.returncode}: {stderr[:300]}")
    return {"command": command, "cwd": str(path), "exit_code": process.returncode, "stdout": stdout[:20000], "stderr": stderr[:20000]}

PLUGINS = [
    Plugin("shell_run", "Run one fixed read-only inspection command in an approved folder", {
        "type": "object",
        "properties": {
            "command": {"type": "string", "enum": ["pwd", "list", "python_version"]},
            "path": {"type": "string", "maxLength": 4096}
        },
        "required": ["command"],
        "additionalProperties": False
    }, "READ", False, run)
]
