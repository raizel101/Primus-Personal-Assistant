"""Run approved scripts in a filesystem and network namespace."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

from .security import guard_path


def script_source(config: dict, raw: str) -> Path:
    path = guard_path(raw, config)
    if not path.is_file() or path.suffix.lower() not in {".py", ".sh"}:
        raise ValueError("Choose an existing .py or .sh script in an approved folder")
    if path.stat().st_size > 100_000:
        raise ValueError("Script exceeds the 100 KB limit")
    return path


def new_run_id() -> str:
    return secrets.token_hex(8)


def project_manifest(config: dict, raw: str, entrypoint: Path) -> list[dict]:
    """Review a small explicit project without following links or including hidden credentials."""
    lexical_root = Path(raw).expanduser()
    root = guard_path(raw, config)
    if lexical_root.is_symlink() or getattr(lexical_root, "is_junction", lambda: False)() or not root.is_dir():
        raise ValueError("Project root must be a regular approved directory")
    if not entrypoint.resolve().is_relative_to(root):
        raise PermissionError("The entry point must be inside the approved project")
    result, names, directory_count = [], set(), 0
    for directory, directories, files in os.walk(root, followlinks=False):
        directory_count += 1
        if directory_count > 64:
            raise ValueError("Project directory limit is 64")
        for name in sorted([*directories, *files]):
            path = Path(directory) / name
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise PermissionError("Project links and junctions are not allowed")
            resolved = guard_path(str(path), config)
            if not resolved.is_relative_to(root):
                raise PermissionError("Project file escaped its approved root")
            relative = path.relative_to(root).as_posix()
            folded = relative.casefold()
            if folded in names:
                raise ValueError("Project paths must be unique ignoring case")
            names.add(folded)
            if path.is_file():
                if len(result) >= 12 or path.stat().st_size > 100_000:
                    raise ValueError("Projects support at most 12 files of 100 KB each")
                if path.stat().st_nlink > 1:
                    raise PermissionError("Project hard links are not allowed")
                result.append({"name": relative, "bytes": path.stat().st_size,
                               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            elif not path.is_dir():
                raise PermissionError("Project contains a non-regular file")
    if entrypoint.relative_to(root).as_posix() not in {item["name"] for item in result}:
        raise ValueError("Project entry point is missing")
    return sorted(result, key=lambda item: item["name"])


def _remove_stage(stage: Path, root: Path) -> None:
    staging_root = (root / "temp" / "script-staging").resolve()
    if stage.resolve().is_relative_to(staging_root) and stage.is_dir():
        shutil.rmtree(stage)


def input_files(config: dict, values: list[str]) -> list[Path]:
    if len(values) > 10:
        raise ValueError("At most 10 input files are allowed")
    files = [guard_path(value, config) for value in values]
    if any(not item.is_file() or item.stat().st_size > 10_000_000 for item in files):
        raise ValueError("Each input must be an existing approved file of at most 10 MB")
    return files


def _wsl_path(distro: str, path: Path) -> str:
    result = subprocess.run(["wsl.exe", "-d", distro, "--exec", "wslpath", "-u", str(path)],
                            capture_output=True, text=True, timeout=15, check=True)
    return result.stdout.strip()


def _sandbox_command(config: dict, script: Path, output: Path, arguments: list[str], inputs: list[Path], project=None) -> list[str]:
    if sys.platform == "win32":
        distro = config.get("script_execution", {}).get("wsl_distro", "Ubuntu")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", distro):
            raise ValueError("Invalid WSL distribution name")
        if not shutil.which("wsl.exe"):
            raise RuntimeError("WSL is required for sandboxed scripts on Windows")
        source, destination = _wsl_path(distro, project["root"] if project else script), _wsl_path(distro, output)
        sources = [_wsl_path(distro, item) for item in inputs]
        prefix = ["wsl.exe", "-d", distro, "--exec"]
    elif sys.platform.startswith("linux"):
        if not shutil.which("bwrap"):
            raise RuntimeError("Bubblewrap is required for sandboxed scripts on Linux")
        source, destination = str(project["root"] if project else script), str(output)
        sources = [str(item) for item in inputs]
        prefix = []
    else:
        raise RuntimeError("No enforced script sandbox is configured for this platform")
    script_name = "/work/script" + script.suffix.lower()
    interpreter = ["/usr/bin/python3", "-I", "-B", script_name] if script.suffix.lower() == ".py" else ["/bin/sh", script_name]
    if project:
        script_name = "/work/project/" + project["entrypoint"]
        # -I discards PYTHONPATH; insert only the explicitly approved snapshot paths.
        parts = project["entrypoint"][:-3].split("/")
        module = ".".join(parts) if len(parts) > 1 and all(part.isidentifier() for part in parts) else None
        search = ["/work/project", str(Path(script_name).parent).replace("\\", "/")] if module else [str(Path(script_name).parent).replace("\\", "/"), "/work/project"]
        launch = ("runpy.run_module(" + json.dumps(module) + ",run_name='__main__',alter_sys=True)" if module else
                  "runpy.run_path(" + json.dumps(script_name) + ",run_name='__main__')")
        bootstrap = "import runpy,sys;sys.path[:0]=" + json.dumps(search) + ";" + launch
        interpreter = ["/usr/bin/python3", "-I", "-B", "-c", bootstrap] if script.suffix.lower() == ".py" else ["/bin/sh", script_name]
    return [*prefix, "/usr/bin/timeout", "-s", "KILL", "25",
            "/usr/bin/prlimit", "--as=1073741824", "--cpu=20", "--fsize=10485760", "--nofile=64", "--",
            "bwrap", "--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts",
            "--die-with-parent", "--new-session",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
            "--dir", "/etc", "--dir", "/home", "--dir", "/mnt", "--dir", "/run",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--dir", "/work", "--dir", "/work/output", "--dir", "/work/inputs",
            *(["--dir", "/work/project", "--ro-bind", source, "/work/project"] if project else ["--ro-bind", source, script_name]),
            "--bind", destination, "/work/output",
            *[part for index, item in enumerate(sources) for part in ("--ro-bind", item, f"/work/inputs/{index}")],
            "--chdir", "/work/project" if project else "/work", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "HOME", "/tmp", "--setenv", "PRIMUS_OUTPUT_DIR", "/work/output", *interpreter, *arguments]


def run_script(config: dict, args: dict) -> dict:
    if not config.get("script_execution", {}).get("enabled", False):
        raise RuntimeError("Script execution is disabled in config")
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    cancellation = config.get("_cancel_event")
    if cancellation is not None and cancellation.is_set():
        raise RuntimeError("Mission cancelled")
    script = script_source(config, args["path"])
    arguments = args.get("arguments", [])
    inputs = input_files(config, args.get("inputs", []))
    project_root = guard_path(args["project_root"], config) if args.get("project_root") else None
    reviewed_project = args.get("_project_manifest")
    if project_root is not None:
        if not isinstance(reviewed_project, list) or project_manifest(config, args["project_root"], script) != reviewed_project:
            raise ValueError("Project files changed after approval; review the complete project again")
    expected_hashes = args.get("_expected_hashes")
    if not isinstance(expected_hashes, list) or len(expected_hashes) != len(inputs) + 1:
        raise ValueError("Script and inputs need reviewed SHA-256 hashes")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in expected_hashes):
        raise ValueError("Invalid reviewed SHA-256 hash")
    if len(arguments) > 20 or any(not isinstance(item, str) or len(item) > 200 or "\0" in item for item in arguments):
        raise ValueError("Use at most 20 arguments of 200 characters each")
    run_id = args.get("_run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{16}", run_id):
        raise ValueError("Script run needs a reviewed output directory")
    output = config["_root"] / "outputs" / "script-runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    stage = config["_root"] / "temp" / "script-staging" / run_id
    stage.mkdir(parents=True, exist_ok=False)
    sources = [script, *inputs]
    snapshots = [stage / ("script" + script.suffix.lower()),
                 *[stage / f"input-{index}" for index in range(len(inputs))]]
    project = None
    try:
        if project_root is not None:
            staged_root = stage / "project"
            staged_root.mkdir()
            for row in reviewed_project:
                source = guard_path(str(project_root / row["name"]), config)
                snapshot = staged_root / row["name"]
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as reader, snapshot.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                if hashlib.sha256(snapshot.read_bytes()).hexdigest() != row["sha256"]:
                    raise RuntimeError("Project file changed during staging; request fresh approval")
            if project_manifest(config, str(project_root), script) != reviewed_project:
                raise RuntimeError("Project file set changed during staging; request fresh approval")
            relative_entry = script.relative_to(project_root).as_posix()
            project = {"root": staged_root, "entrypoint": relative_entry}
        for source, snapshot, expected in zip(sources, snapshots, expected_hashes):
            with source.open("rb") as reader, snapshot.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != expected:
                raise RuntimeError("Script or input changed after approval; run again")
        argv = (_sandbox_command(config, snapshots[0], output, arguments, snapshots[1:], project=project) if project else
                _sandbox_command(config, snapshots[0], output, arguments, snapshots[1:]))
        if cancellation is not None and cancellation.is_set():
            raise RuntimeError("Mission cancelled")
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   shell=False, cwd=config["_root"])
    except BaseException:
        _remove_stage(stage, config["_root"])
        output.rmdir()
        raise
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    captured_bytes = {"stdout": 0, "stderr": 0}
    def drain(stream, key):
        while chunk := stream.read(4096):
            captured_bytes[key] += len(chunk)
            remaining = 4000 - len(captured[key])
            if remaining > 0:
                captured[key].extend(chunk[:remaining])
    readers = [threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        deadline = time.monotonic() + 35
        size_check = 0.0
        while True:
            if (config["_root"] / "data" / "STOP").exists():
                process.kill()
                raise RuntimeError("Global kill switch is active")
            if cancellation is not None and cancellation.is_set():
                process.kill()
                raise RuntimeError("Mission cancelled")
            if time.monotonic() > deadline:
                process.kill()
                raise TimeoutError("Sandboxed script exceeded 35 seconds")
            if time.monotonic() >= size_check:
                size_check = time.monotonic() + 1
                if sum(item.stat(follow_symlinks=False).st_size for item in output.rglob("*") if item.is_file()) > 50_000_000:
                    process.kill()
                    raise RuntimeError("Script output exceeded 50 MB")
            try:
                process.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for reader, stream in zip(readers, (process.stdout, process.stderr)):
            reader.join(timeout=2)
            if not reader.is_alive():
                stream.close()
        _remove_stage(stage, config["_root"])
    stdout = bytes(captured["stdout"]).decode("utf-8", "replace")
    stderr = bytes(captured["stderr"]).decode("utf-8", "replace")
    files = []
    for item in sorted(output.rglob("*")):
        if item.is_symlink():
            raise RuntimeError("Script created a symbolic link; output needs review")
        if item.is_file():
            files.append({"path": str(item), "bytes": item.stat().st_size,
                          "sha256": hashlib.sha256(item.read_bytes()).hexdigest() if item.stat().st_size <= 1_000_000 else None})
        if len(files) > 100:
            raise RuntimeError("Script created more than 100 output files")
    return {"script": str(script), "output_dir": str(output), "exit_code": process.returncode,
            "stdout": stdout[:4000], "stderr": stderr[:4000], "files": files,
            "stdout_truncated": captured_bytes["stdout"] > 4000, "stderr_truncated": captured_bytes["stderr"] > 4000,
            "project_files": reviewed_project if project_root is not None else [],
            "verified": process.returncode == 0 and all(row["sha256"] is not None for row in files)}
