"""Three real offline HTTP/tool demos. Does not execute any modeled action."""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from urllib.request import Request, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "src"))
from primus.api import make_server
from primus.auth import api_token
from primus.config import load_config
from primus.planning import run_plugin
from primus.plugins import discover


def main():
    config = load_config(ROOT / "config.json")
    # A temporary owned test service reads the existing token; the running tray,
    # scheduled task, on-disk config and local MCP server are not changed.
    config = {**config, "local_api": {**config["local_api"], "port": 8766}}
    server, _ = make_server(config)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    tools = discover(ROOT)
    opener = build_opener(ProxyHandler({}))
    expected = {
        "downloads": ["create_folder", "stage_download", "finish_organization"],
        "coding-workflow": ["plan_task", "create_code", "review_code", "finish_task"],
        "unreachable": None,
    }
    report = {"demos": [], "scope": "Real plugin and authenticated HTTP requests over a temporary localhost test API; modeled actions never executed. No model or cloud call needed.", "port": 8766}
    try:
        for name, actions in expected.items():
            path = ROOT / "examples" / "planning" / f"{name}.json"
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            data = json.dumps({"task": f'planning: "{path}"'}).encode()
            request = Request("http://127.0.0.1:8766/tasks", data=data,
                              headers={"Authorization": "Bearer " + api_token(), "Content-Type": "application/json"})
            start = time.monotonic()
            with opener.open(request, timeout=10) as response:
                result = json.loads(json.load(response)["result"])
            assert result["status"] == ("solved" if actions is not None else "unreachable"), result
            assert result["plan"] == actions, result
            assert result["executed"] is False
            assert before == hashlib.sha256(path.read_bytes()).hexdigest() == result["source_sha256"]
            replayed = None
            if actions is not None:
                replayed = run_plugin(config, tools, "formal_validate", {"path": str(path), "actions": actions})
                assert replayed["valid"] and not replayed["executed"]
            report["demos"].append({"example": name, "http_roundtrip_ms": round((time.monotonic()-start)*1000, 2), "result": result, "independent_plugin_replay": replayed, "source_unchanged": True})
        rejected = run_plugin(config, tools, "formal_validate", {"path": str(ROOT / "examples/planning/downloads.json"), "actions": ["finish_organization"]})
        assert not rejected["valid"] and rejected["failed_step"] == 1
        report["invalid_order_rejected"] = rejected
        env = {**os.environ, "TEMP": str(ROOT / "temp"), "TMP": str(ROOT / "temp"), "PYTHONPYCACHEPREFIX": str(ROOT / "temp/pycache")}
        cli = subprocess.run([sys.executable, str(ROOT / "primus.py"), "planning", "solve", "examples/planning/downloads.json"], env=env, capture_output=True, text=True, timeout=10)
        assert cli.returncode == 0, cli.stderr
        assert json.loads(cli.stdout)["plan"] == expected["downloads"]
        report["real_cli_verified"] = True
        report["all_passed"] = True
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
    report["temporary_server_closed"] = not worker.is_alive()
    output = ROOT / "outputs" / "formal-planning-verification.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"all_passed": report["all_passed"], "examples": [{"name": d["example"], "status": d["result"]["status"], "steps": len(d["result"]["plan"] or [])} for d in report["demos"]], "real_cli_verified": True, "test_server_closed": report["temporary_server_closed"], "evidence": str(output)}, indent=2))


if __name__ == "__main__":
    main()
