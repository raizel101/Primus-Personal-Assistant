"""Local reminder and recurring-task scheduler."""
from __future__ import annotations
import threading
from typing import Callable
from .audit import append_audit
from .platform import adapter
from .service import TaskRunner
from .storage import Store

class Notifier:
    def __init__(self, callback: Callable[[str, str], None] | None = None):
        self.callback = callback

    def notify(self, title: str, message: str) -> None:
        if self.callback:
            self.callback(title, message)
        else:
            adapter().notify(title, message)

class Scheduler:
    def __init__(self, config: dict, store: Store, plugins: dict, *, notify: Notifier | None = None):
        self.config, self.store, self.plugins = config, store, plugins
        self.notify = notify or Notifier()

    def run_once(self) -> bool:
        if (self.config["_root"] / "data" / "STOP").exists():
            return False
        job = self.store.claim_due()
        if job is None:
            return False
        status = "done"
        try:
            if job["kind"] == "reminder":
                self.notify.notify("PRIMUS reminder", job["content"])
            else:
                runner = TaskRunner(self.config, self.plugins, self.store, confirm=None)
                answer = runner.run(job["content"])
                self.notify.notify("PRIMUS scheduled task", answer[:300])
                if answer.startswith("Task needs review"):
                    status = "needs_review"
        except Exception as exc:
            status = "failed"
            self.notify.notify("PRIMUS schedule failed", str(exc)[:300])
        finally:
            self.store.finish_schedule(job, status)
            append_audit(self.config["_root"], "schedule_execute", {"id": job["id"], "kind": job["kind"]}, {"status": status})
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        self.store.recover_running()
        while not stop_event.is_set():
            self.run_once()
            stop_event.wait(float(self.config.get("scheduler", {}).get("poll_seconds", 1)))
