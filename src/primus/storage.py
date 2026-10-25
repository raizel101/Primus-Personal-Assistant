"""Local SQLite memory, task history, and scheduled jobs."""
from __future__ import annotations
import hashlib
from contextlib import contextmanager
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SECRET_MARKERS = re.compile(r"(?i)\b(?:password|passphrase|api[_ -]?key|secret|bearer|private[_ -]?key|recovery[_ -]?code|otp|token)\b")
CONTEXT_KEYS = {"last_app", "last_file", "last_folder", "last_reminder_id", "last_target"}
UTC = timezone.utc

def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")

def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def assert_no_secret(value: str) -> None:
    if SECRET_MARKERS.search(value):
        raise ValueError("Possible credential or secret text must not be stored in PRIMUS memory or schedules")

class Store:
    def __init__(self, root: Path):
        self.path = root / "data" / "primus.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('fact','preference')),
                    content TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_history (
                    id TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    request_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    result_sha256 TEXT,
                    started_utc TEXT NOT NULL,
                    finished_utc TEXT,
                    parent_task_id TEXT,
                    agent_label TEXT
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('reminder','agent')),
                    content TEXT NOT NULL,
                    due_utc TEXT NOT NULL,
                    repeat_seconds INTEGER,
                    status TEXT NOT NULL CHECK (status IN ('pending','running','done','disabled')),
                    last_run_utc TEXT,
                    last_status TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(status, due_utc);
                CREATE TABLE IF NOT EXISTS task_context (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(task_history)")}
            if "parent_task_id" not in columns:
                db.execute("ALTER TABLE task_history ADD COLUMN parent_task_id TEXT")
            if "agent_label" not in columns:
                db.execute("ALTER TABLE task_history ADD COLUMN agent_label TEXT")

    def add_memory(self, kind: str, content: str, *, approved: bool = False) -> str:
        if not approved:
            raise PermissionError("Explicit user approval is required before saving memory")
        if kind not in {"fact", "preference"} or not content.strip():
            raise ValueError("Memory requires kind fact/preference and non-empty content")
        assert_no_secret(content)
        identifier, timestamp = uuid.uuid4().hex, now_utc()
        with self.connect() as db:
            db.execute("INSERT INTO memories VALUES (?,?,?,?,?)", (identifier, kind, content.strip(), timestamp, timestamp))
        return identifier

    def list_memories(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM memories ORDER BY created_utc, id").fetchall()
        return [dict(row) for row in rows]

    def edit_memory(self, identifier: str, content: str, *, approved: bool = False) -> None:
        if not approved:
            raise PermissionError("Explicit user approval is required before editing memory")
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        assert_no_secret(content)
        with self.connect() as db:
            changed = db.execute("UPDATE memories SET content=?, updated_utc=? WHERE id=?", (content.strip(), now_utc(), identifier)).rowcount
        if not changed:
            raise KeyError("Memory not found")

    def delete_memory(self, identifier: str, *, approved: bool = False) -> None:
        if not approved:
            raise PermissionError("Explicit user approval is required before deleting memory")
        with self.connect() as db:
            changed = db.execute("DELETE FROM memories WHERE id=?", (identifier,)).rowcount
        if not changed:
            raise KeyError("Memory not found")

    def start_task(self, request: str, *, parent_task_id: str | None = None,
                   agent_label: str | None = None) -> str:
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO task_history (id,request_sha256,request_bytes,status,started_utc,parent_task_id,agent_label) VALUES (?,?,?,?,?,?,?)",
                       (identifier, digest(request), len(request.encode("utf-8")), "running", now_utc(), parent_task_id, agent_label))
        return identifier

    def finish_task(self, identifier: str, status: str, result: str) -> None:
        if status not in {"done", "failed", "stopped"}:
            raise ValueError("Invalid task status")
        with self.connect() as db:
            db.execute("UPDATE task_history SET status=?, result_sha256=?, finished_utc=? WHERE id=?",
                       (status, digest(result), now_utc(), identifier))

    def list_tasks(self, limit: int = 30) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM task_history ORDER BY started_utc DESC, id DESC LIMIT ?", (min(max(limit, 1), 200),)).fetchall()
        return [dict(row) for row in rows]

    def update_context(self, values: dict[str, str]) -> None:
        """Keep only short, structured references from successful tool calls."""
        for key, value in values.items():
            if key not in CONTEXT_KEYS or not isinstance(value, str) or not value or len(value) > 300:
                raise ValueError("Invalid task context")
            assert_no_secret(value)
        timestamp = now_utc()
        with self.connect() as db:
            for key, value in values.items():
                db.execute("INSERT INTO task_context(key,value,updated_utc) VALUES (?,?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_utc=excluded.updated_utc",
                           (key, value, timestamp))

    def get_context(self, retention_days: int = 30) -> dict[str, str]:
        if not 1 <= retention_days <= 365:
            raise ValueError("Invalid task context retention")
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(timespec="seconds")
        with self.connect() as db:
            db.execute("DELETE FROM task_context WHERE updated_utc<?", (cutoff,))
            rows = db.execute("SELECT key,value FROM task_context WHERE updated_utc>=? ORDER BY key", (cutoff,)).fetchall()
        return {row["key"]: row["value"] for row in rows if row["key"] in CONTEXT_KEYS}

    def clear_context(self, *, approved: bool = False) -> None:
        if not approved:
            raise PermissionError("Explicit user approval is required to clear task context")
        with self.connect() as db:
            db.execute("DELETE FROM task_context")

    def add_schedule(self, kind: str, content: str, due_utc: str, repeat_seconds: int | None = None) -> str:
        if kind not in {"reminder", "agent"} or not content.strip():
            raise ValueError("Schedule requires kind reminder/agent and non-empty content")
        assert_no_secret(content)
        due = datetime.fromisoformat(due_utc)
        if due.tzinfo is None:
            raise ValueError("Schedule time needs an explicit UTC offset")
        if due.astimezone(UTC) <= datetime.now(UTC):
            raise ValueError("Schedule time must be in the future")
        if repeat_seconds is not None and not (60 <= repeat_seconds <= 31_536_000):
            raise ValueError("Repeat interval must be between one minute and one year")
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO schedules (id,kind,content,due_utc,repeat_seconds,status) VALUES (?,?,?,?,?,?)",
                       (identifier, kind, content.strip(), due.astimezone(UTC).isoformat(timespec="seconds"), repeat_seconds, "pending"))
        return identifier

    def list_schedules(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM schedules ORDER BY due_utc, id").fetchall()
        return [dict(row) for row in rows]

    def disable_schedule(self, identifier: str) -> None:
        with self.connect() as db:
            changed = db.execute("UPDATE schedules SET status='disabled' WHERE id=? AND status!='done'", (identifier,)).rowcount
        if not changed:
            raise KeyError("Active schedule not found")

    def recover_running(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE schedules SET status='pending' WHERE status='running'")

    def claim_due(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM schedules WHERE status='pending' AND due_utc<=? ORDER BY due_utc LIMIT 1", (now_utc(),)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE schedules SET status='running' WHERE id=?", (row["id"],))
            return dict(row)

    def finish_schedule(self, job: dict, status: str) -> None:
        timestamp = now_utc()
        if job["repeat_seconds"]:
            due = datetime.fromisoformat(job["due_utc"])
            while due <= datetime.now(UTC):
                due += timedelta(seconds=job["repeat_seconds"])
            next_due = due.isoformat(timespec="seconds")
            next_status = "pending"
        else:
            next_due = job["due_utc"]
            next_status = "done"
        with self.connect() as db:
            db.execute("UPDATE schedules SET due_utc=?,status=?,last_run_utc=?,last_status=? WHERE id=? AND status='running'",
                       (next_due, next_status, timestamp, status, job["id"]))
