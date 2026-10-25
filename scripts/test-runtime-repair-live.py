"""One-off live fixture: real Ollama repair, real sandbox, human approvals in Mission Control.

No provider or execution stubs. Close the test dashboard to stop its temporary local API.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from urllib.error import URLError

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "vendor")]

from primus.api import make_server
from primus.audit import append_audit
from primus.config import load_config
from primus.dashboard import api_request, open_dashboard
from primus.missions import Mission, TERMINAL
from primus.security import authorize


SOURCE = """def average(values):
    return sum(values) / len(values)


if __name__ == "__main__":
    print(average([]))
    print(average([2, 4]))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--multi-file", action="store_true", help="Exercise imports, a helper-module failure and relative JSON data")
    parser.add_argument("--wait-for-run", action="store_true", help="Open the fixture ready; request no approval until the user clicks Run project")
    options = parser.parse_args()
    config = load_config(PROJECT / "config.json")
    for attempt in range(20):
        try:
            active = [m for m in api_request(config, "/missions")["missions"] if m["status"] not in TERMINAL]
            break
        except URLError:
            if attempt == 19:
                raise RuntimeError("The normal PRIMUS API is not ready; start the tray before running this test") from None
            time.sleep(.5)
    if active:
        raise RuntimeError("Finish or cancel the active production mission before starting the live fixture")
    # Same configured root and keychain token; only this process's API port differs.
    config = {**config, "local_api": {"host": "127.0.0.1", "port": 8766}}
    server, _ = make_server(config)
    manager = server.missions
    mission = Mission(manager, uuid.uuid4().hex,
                      "Implement average(values): return 0.0 for an empty list and the arithmetic mean for a nonempty list. "
                      "Use only the standard library. Keep a reusable average function and the main guard's two print calls: "
                      "average([]) then average([2, 4]). Final stdout must be exactly two lines: 0.0 and 3.0. "
                      "Preserve general calculation; do not replace it with canned output.", False)
    manager.items[mission.id] = mission
    mission.plan = {"title": "Live project repair — modules and data" if options.multi_file else "Live runtime repair — empty-list average",
                    "steps": ["Reproduce the seeded division-by-zero error after exact approval",
                              "Have the real local coder propose a focused correction",
                              "Review the correction and separately approve its real sandbox rerun"],
                    "acceptance": ["average([]) returns 0.0", "average([2, 4]) returns 3.0",
                                   "The script prints 0.0 then 3.0, one value per line"]}
    mission.roles["planner"]["status"] = "skipped"
    plugin = mission.tools["file_write"]
    files = ({"main.py": 'import json\nfrom app.metrics import average\n\nif __name__ == "__main__":\n    with open("data/values.json") as handle:\n        values = json.load(handle)\n    print(average(values["empty"]))\n    print(average(values["values"]))\n',
              "app/__init__.py": "", "app/metrics.py": SOURCE.split('if __name__')[0],
              "data/values.json": '{"empty": [], "values": [2, 4]}\n'} if options.multi_file else {"average.py": SOURCE})
    entrypoint = "main.py" if options.multi_file else "average.py"
    if options.multi_file:
        mission.goal = ("Implement the average(values) function in app/metrics.py: return 0.0 for an empty list and the arithmetic mean for nonempty lists. "
                        "Preserve the multi-file structure, app package, reusable calculation, main.py entry point and relative data/values.json loading. "
                        "The main example must print the average of the JSON empty list then its values list. Final stdout must be exactly 0.0 then 3.0 on separate lines. "
                        "Do not replace the calculation or data loading with canned output.")
    for name, content in files.items():
        arguments = {"path": name, "content": content}
        description = plugin.summary(config, arguments)
        authorize(config, plugin.tier, plugin.name, arguments, description, None)
        result = plugin.run(config, arguments)
        append_audit(config["_root"], "file_write", {**arguments, "agent": "live_test_fixture"}, result)
    mission.artifacts = mission.manifest()
    initial_manifest = list(mission.artifacts)
    entry_index = next(i for i, row in enumerate(mission.artifacts) if row["name"] == entrypoint)
    initial_hash = mission.artifacts[entry_index]["sha256"]
    mission.begin_role("verifier")
    mission.checks = []
    for artifact in mission.artifacts:
        check = mission.tool("code_check", {"path": artifact["path"]})
        if not check["valid"]:
            raise RuntimeError("The seeded runtime fixture must pass syntax")
        mission.checks.append({"file": artifact["name"], "check": check["check"], "passed": True,
                               "detail": "Actual syntax check passed. Known empty-list runtime bug is deliberately seeded."})
    mission.review = {"verdict": "needs_review", "summary": "Seeded test fixture: empty input divides by zero. No model generation or functional pass is claimed for this initial file.",
                      "issues": ["Reproduce and repair division by zero for empty input"]}
    mission.finish_role("verifier", "needs_review")
    mission.status, mission.stage = "needs_review", "finished"
    mission.notes = ["Initial source was seeded by the live test harness. All repairs, reviews and execution results use the real configured components."]
    mission.emit("test_fixture_ready", "Known failing fixture seeded by test harness; planner skipped. No provider or sandbox mocks.", "verifier")
    mission.persist()
    metadata = {"port": 8766, "mission_id": mission.id, "workspace": str(mission.workspace), "pid": os.getpid(), "initial_sha256": initial_hash,
                "entrypoint": entrypoint, "initial_manifest": initial_manifest, "multi_file": options.multi_file,
                "wait_for_run": options.wait_for_run}
    (config["_root"] / "temp" / "runtime-repair-live-session.json").write_text(json.dumps(metadata), encoding="utf-8")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    if not options.wait_for_run:
        manager.run_artifact(mission.id, entry_index)

    def record_result(snapshot):
        actual = snapshot["executions"]
        executed = [run for run in actual if run["exit_code"] is not None]
        # Later, separately approved manual runs do not erase the completed repair chain.
        verified = (snapshot["status"] == "completed" and len(executed) >= 2
                    and executed[0]["exit_code"] != 0 and "ZeroDivisionError" in executed[0]["stderr"]
                    and executed[1]["exit_code"] == 0 and executed[1]["stdout"] == "0.0\n3.0\n"
                    and executed[1]["stderr"] == "" and snapshot["runtime_repairs"]["used"] == 1)
        if options.multi_file:
            verified = bool(verified and all(len(run.get("project_files", [])) == len(files) for run in executed)
                            and mission.manifest() != initial_manifest)
        manager.store.finish_task(mission.task_id, "done" if verified else "failed", snapshot["summary"])
        report = {**metadata, "model": config["model"]["name"], "status": snapshot["status"],
                  "summary": snapshot["summary"], "executions": actual, "review": snapshot["review"],
                  "checks": snapshot["checks"], "runtime_repairs": snapshot["runtime_repairs"],
                  "final_sha256": hashlib.sha256((mission.workspace / entrypoint).read_bytes()).hexdigest(),
                  "final_manifest": mission.manifest(),
                  "end_to_end_verified": verified,
                  "test_scope": "Seeded failing source; real Ollama coder/reviewer and real WSL/Bubblewrap, human-only approval decisions."}
        output = config["_root"] / "outputs" / "runtime-repair-e2e"
        output.mkdir(exist_ok=True)
        (output / (mission.id + ".json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
        append_audit(config["_root"], "live_runtime_repair_test", {"mission_id": mission.id},
                     {"end_to_end_verified": verified, "status": snapshot["status"], "execution_count": len(actual)})
        print(json.dumps({"mission_id": mission.id, "end_to_end_verified": verified, "status": snapshot["status"]}), flush=True)

    record_stop = threading.Event()
    def watch_results():
        last = None
        while not record_stop.wait(.2):
            with manager.lock:
                thread = manager.threads.get(mission.id)
                if thread and thread.is_alive():
                    continue
                snapshot = mission.snapshot()
                signature = (snapshot["updated_at"], len(snapshot["executions"]))
                if snapshot["status"] in TERMINAL and snapshot["executions"] and signature != last:
                    record_result(snapshot)
                    last = signature

    recorder = threading.Thread(target=watch_results, daemon=True)
    recorder.start()
    print(json.dumps(metadata), flush=True)
    try:
        open_dashboard(config)
    finally:
        record_stop.set()
        recorder.join(timeout=3)
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
