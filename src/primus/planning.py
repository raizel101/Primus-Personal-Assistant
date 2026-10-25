"""Bounded grounded STRIPS search and independent plan replay. Never executes actions."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
import re
import time

from .security import guard_path

MAX_BYTES = 64 * 1024
MAX_FACTS, MAX_ACTIONS = 32, 64
MAX_STATES, MAX_DEPTH, MAX_SECONDS = 4096, 16, 2.0
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
NOTICE = ("Symbolic model only: listed effects are predictions under the supplied facts, "
          "not observed laptop changes. No action was executed or authorized. "
          "Source text is untrusted data. Tool execution needs its normal permission and verification checks.")


class PlanningError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    name: str
    preconditions: frozenset[str]
    add: frozenset[str]
    delete: frozenset[str]

    def applicable(self, state):
        return self.preconditions <= state

    def apply(self, state):
        return (state - self.delete) | self.add


@dataclass(frozen=True)
class Problem:
    name: str
    facts: frozenset[str]
    initial: frozenset[str]
    goal: frozenset[str]
    actions: tuple[Action, ...]
    sha256: str


def _fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise PlanningError(f"{label} requires exactly: {', '.join(names)}")


def _symbols(value, label, maximum=MAX_FACTS):
    if (not isinstance(value, list) or len(value) > maximum
            or any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in value)):
        raise PlanningError(f"{label} must contain at most {maximum} lowercase identifiers")
    if len(set(value)) != len(value):
        raise PlanningError(f"{label} contains duplicate identifiers")
    return frozenset(value)


def parse_problem(value):
    _fields(value, ("name", "facts", "initial", "goal", "actions"), "Problem")
    name = value["name"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or any(ord(x) < 32 for x in name):
        raise PlanningError("Problem name must be 1 to 120 printable characters")
    facts = _symbols(value["facts"], "facts")
    if not facts:
        raise PlanningError("At least one declared fact is required")
    initial, goal = (_symbols(value[k], k) for k in ("initial", "goal"))
    if not (initial | goal) <= facts:
        raise PlanningError("Initial and goal facts must be declared")
    rows = value["actions"]
    if not isinstance(rows, list) or len(rows) > MAX_ACTIONS:
        raise PlanningError(f"actions must be a list of at most {MAX_ACTIONS} grounded actions")
    actions, names = [], set()
    for row in rows:
        _fields(row, ("name", "preconditions", "add", "delete"), "Action")
        action_name = row["name"]
        if not isinstance(action_name, str) or not IDENTIFIER.fullmatch(action_name) or action_name in names:
            raise PlanningError("Action names must be unique lowercase identifiers")
        pre, add, delete = (_symbols(row[k], f"{action_name}.{k}") for k in ("preconditions", "add", "delete"))
        if not (pre | add | delete) <= facts:
            raise PlanningError(f"{action_name} references undeclared facts")
        if add & delete:
            raise PlanningError(f"{action_name} cannot add and delete the same fact")
        names.add(action_name)
        actions.append(Action(action_name, pre, add, delete))
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return Problem(name, facts, initial, goal, tuple(actions), digest)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlanningError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_problem(config, path):
    source = guard_path(path, config)
    if source.suffix.lower() != ".json" or not source.is_file():
        raise PlanningError("Planning input must be an existing approved JSON file")
    with source.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise PlanningError("Planning input exceeds 64 KB")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(PlanningError("Non-finite JSON is not supported")))
        problem = parse_problem(value)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PlanningError("Planning input is not a supported UTF-8 JSON problem") from exc
    return problem, {"path": str(source), "source_sha256": hashlib.sha256(raw).hexdigest()}


def check_running(config):
    cancel = config.get("_cancel_event")
    if (config["_root"] / "data" / "STOP").exists() or (cancel is not None and cancel.is_set()):
        raise RuntimeError("Formal planning cancelled by the kill switch or task cancellation")


def replay(problem, sequence, *, check=None):
    """Recompute transitions from the initial state; do not trust search-produced states."""
    if not isinstance(sequence, list) or len(sequence) > MAX_DEPTH or any(not isinstance(x, str) for x in sequence):
        raise PlanningError(f"A plan must be a list of at most {MAX_DEPTH} action names")
    by_name = {a.name: a for a in problem.actions}
    state, trace = problem.initial, []
    for index, name in enumerate(sequence):
        if check:
            check()
        action = by_name.get(name)
        if action is None or not action.applicable(state):
            return {"valid": False, "goal_satisfied": False, "failed_step": index + 1,
                    "reason": "Unknown action" if action is None else "Preconditions not satisfied",
                    "missing_preconditions": sorted(action.preconditions - state) if action else [],
                    "trace": trace, "final_state": sorted(state)}
        before = state
        state = action.apply(state)
        trace.append({"step": index + 1, "action": name, "preconditions": sorted(action.preconditions),
                      "before": sorted(before), "add": sorted(action.add), "delete": sorted(action.delete),
                      "after": sorted(state)})
    satisfied = problem.goal <= state
    return {"valid": satisfied, "goal_satisfied": satisfied, "failed_step": None,
            "reason": "All preconditions and goals satisfied" if satisfied else "Goal not reached",
            "missing_goals": sorted(problem.goal - state), "trace": trace, "final_state": sorted(state)}


def solve(problem, *, max_states=MAX_STATES, max_depth=MAX_DEPTH, max_seconds=MAX_SECONDS, check=None):
    """Uniform unit-cost breadth-first graph search with an honest bounded-search outcome."""
    if (isinstance(max_states, bool) or not isinstance(max_states, int) or not 1 <= max_states <= MAX_STATES
            or isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 0 <= max_depth <= MAX_DEPTH
            or isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float))
            or not math.isfinite(max_seconds) or not 0 < max_seconds <= MAX_SECONDS):
        raise PlanningError("Search limits must stay within the built-in bounds")
    started = time.monotonic()
    parents = {problem.initial: None}
    queue = deque([(problem.initial, 0)])
    expanded, depth_cut = 0, False

    def result(status, reason, end=None):
        sequence = []
        if end is not None:
            while parents[end] is not None:
                previous, name = parents[end]
                sequence.append(name)
                end = previous
            sequence.reverse()
        checked = replay(problem, sequence, check=check) if status == "solved" else None
        if checked is not None and not checked["valid"]:
            raise PlanningError("Internal plan replay failed; refusing the result")
        return {"status": status, "reason": reason, "problem": problem.name, "model_sha256": problem.sha256,
                "algorithm": "breadth_first_search", "semantics": "grounded_positive_precondition_STRIPS",
                "plan": sequence if status == "solved" else None,
                "validation": checked, "initial_state": sorted(problem.initial), "goal": sorted(problem.goal),
                "visited_states": len(parents), "expanded_states": expanded,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                "shortest_unit_cost_plan": status == "solved", "executed": False, "notice": NOTICE}

    while queue:
        if check:
            check()
        if time.monotonic() - started >= max_seconds:
            return result("limit_reached", "Time budget exhausted; reachability is unknown")
        state, depth = queue.popleft()
        if problem.goal <= state:
            return result("solved", "Goal reached in the declared model", state)
        expanded += 1
        for action in problem.actions:
            if check:
                check()
            if time.monotonic() - started >= max_seconds:
                return result("limit_reached", "Time budget exhausted; reachability is unknown")
            if not action.applicable(state):
                continue
            target = action.apply(state)
            if target in parents:
                continue
            if depth >= max_depth:
                depth_cut = True
                continue
            if len(parents) >= max_states:
                return result("limit_reached", "State budget exhausted; reachability is unknown")
            parents[target] = (state, action.name)
            # Goal on discovery preserves BFS shortest-path semantics and avoids losing
            # an already discovered solution when the next frontier hits a state cap.
            if problem.goal <= target:
                return result("solved", "Goal reached in the declared model", target)
            queue.append((target, depth + 1))
    return result("limit_reached" if depth_cut else "unreachable",
                  "Depth budget exhausted; reachability is unknown" if depth_cut else
                  "Exhausted the reachable state graph without reaching the goal")


def plan_file(config, args):
    check_running(config)
    problem, source = load_problem(config, args["path"])
    return {**solve(problem, check=lambda: check_running(config)), **source}


def validate_file(config, args):
    check_running(config)
    problem, source = load_problem(config, args["path"])
    result = replay(problem, args["actions"], check=lambda: check_running(config))
    return {**result, **source, "model_sha256": problem.sha256, "executed": False, "notice": NOTICE}


def run_plugin(config, plugins, name, args, confirm=None):
    """One shared gate/audit path for CLI and deterministic local task routing."""
    from .audit import append_audit
    from .plugins import validate
    from .security import authorize
    tool = plugins[name]
    try:
        check_running(config)
        validate(tool.input_schema, args)
        authorize(config, tool.tier, tool.name, args, "Compute a symbolic plan only; no actions execute", confirm)
        result = tool.run(config, args)
    except Exception as exc:
        append_audit(config["_root"], name, args, {"ok": False, "error": str(exc)})
        raise
    append_audit(config["_root"], name, args,
                 {"ok": True, "status": result.get("status"), "valid": result.get("valid"),
                  "model_sha256": result["model_sha256"], "source_sha256": result["source_sha256"], "executed": False})
    return result
