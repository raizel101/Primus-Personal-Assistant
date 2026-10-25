"""Task execution with approved memory context and private history metadata."""
from __future__ import annotations
import sqlite3
import re
from .agent import Agent
from .audit import append_audit
from .context_memory import context_from_tool
from .fast_answer import answer as answer_fast, route as fast_route, strip_prefix
from .fast_intents import execute as execute_fast
from .provider import OllamaProvider, ProviderError
from .storage import Store

class TaskRunner:
    def __init__(self, config: dict, plugins: dict, store: Store, *, confirm=None, provider=None,
                 parent_task_id: str | None = None, agent_label: str | None = None):
        self.config, self.plugins, self.store = config, plugins, store
        self.parent_task_id, self.agent_label = parent_task_id, agent_label
        self.successful_tools = 0
        self.last_task_id: str | None = None
        self.agent = Agent(config, plugins, provider or OllamaProvider(config), confirm=confirm,
                           agent_label=agent_label, parent_task_id=parent_task_id)

    def run(self, task: str) -> str:
        self.successful_tools = 0
        identifier = self.store.start_task(task, parent_task_id=self.parent_task_id, agent_label=self.agent_label)
        self.last_task_id = identifier
        self.agent.memory_items = [
            {"kind": row["kind"], "content": row["content"][:300]}
            for row in self.store.list_memories()[:20]
        ]
        fast_events: list[dict[str, str]] = []
        def remember_fast(name: str, args: dict, result: dict) -> None:
            self.successful_tools += 1
            event = context_from_tool(name, args, result, self.config)
            if event:
                fast_events.append(event)
        try:
            planning_request = re.match(r"^(?:planning|strips)\s*[:,]\s*(.+)$", task.strip(), re.I | re.S)
            if planning_request:
                import json
                from .planning import run_plugin
                path = planning_request[1].strip()
                if len(path) >= 2 and path[0] == path[-1] and path[0] in {'"', "'"}:
                    path = path[1:-1]
                result = run_plugin(self.config, self.plugins, "formal_plan", {"path": path}, self.agent.confirm)
                answer = json.dumps(result, indent=2, ensure_ascii=False)
                self.successful_tools = 1
                self.store.finish_task(identifier, "done" if result["status"] == "solved" else "failed", answer)
                return answer
            knowledge_request = re.match(r"^(?:knowledge|rag)\s*[:,]\s*(.+)$", task.strip(), re.I | re.S)
            if knowledge_request:
                from .knowledge import answer as knowledge_answer
                result = knowledge_answer(self.config, knowledge_request[1], provider=self.agent.provider)
                answer = result["answer"]
                if result["sources"]:
                    answer += "\nSources:\n" + "\n".join(p["citation"] + " " + p["path"] + (f" (page {p['page']})" if p["page"] else "") for p in result["sources"])
                self.store.finish_task(identifier, "done", answer)
                return answer
            memory_settings = self.config.get("memory", {})
            context_enabled = memory_settings.get("task_context_enabled", False)
            self.agent.task_context = (self.store.get_context(memory_settings.get("context_retention_days", 30))
                                       if context_enabled else {})
            working_task = strip_prefix(task)
            fast_settings = self.config.get("fast_answer", {})
            fast_question = (fast_route(task, auto_simple=fast_settings.get("auto_simple", True))
                             if fast_settings.get("enabled", False) else None)
            if fast_question is not None:
                try:
                    response = answer_fast(self.config, fast_question)
                    append_audit(self.config["_root"], "fast_answer", {"question": fast_question},
                                 {"ok": True, "model": fast_settings["name"]})
                    self.agent.history = (self.agent.history + [
                        {"role": "user", "content": fast_question},
                        {"role": "assistant", "content": response}])[-8:]
                    self.store.finish_task(identifier, "done", response)
                    return response
                except ProviderError as exc:
                    append_audit(self.config["_root"], "fast_answer", {"question": fast_question},
                                 {"ok": False, "error": str(exc), "fallback": "standard_model"})
            answer = execute_fast(self.config, self.plugins, working_task, self.agent.confirm,
                                  on_success=remember_fast,
                                  audit_context={"agent": self.agent_label, "team_id": self.parent_task_id}
                                  if self.agent_label else None)
            if answer is None:
                answer = self.agent.run(working_task)
                self.successful_tools = self.agent.successful_tools
                events = self.agent.context_events
            else:
                self.agent.history = (self.agent.history + [
                    {"role": "user", "content": working_task}, {"role": "assistant", "content": answer}])[-8:]
                events = fast_events
            status = "failed" if answer.startswith("Task needs review") else "done"
            if context_enabled and status == "done" and events:
                merged: dict[str, str] = {}
                for event in events:
                    merged.update(event)
                try:
                    self.store.update_context(merged)
                except (OSError, ValueError, sqlite3.Error):
                    answer += "\nNote: recent task context could not be saved."
            self.store.finish_task(identifier, status, answer)
            return answer
        except Exception as exc:
            status = "stopped" if (self.config["_root"] / "data" / "STOP").exists() else "failed"
            self.store.finish_task(identifier, status, str(exc))
            raise
