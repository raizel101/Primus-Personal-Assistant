"""PRIMUS command-line interface."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import shutil
import sys
import threading
from urllib.error import URLError
from urllib.request import urlopen
from .audit import append_audit
from .config import load_config, initialize
from .plugins import discover
from .scheduler import Scheduler
from .security import authorize
from .service import TaskRunner
from .storage import Store

def confirm(prompt: str) -> bool:
    return input(prompt).strip().lower() == "y"

def doctor(config: dict) -> int:
    print(f"Root: {config['_root']}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Ollama executable: {shutil.which('ollama') or 'not found'}")
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as response:
            tags = json.load(response)
        models = [item.get("name") for item in tags.get("models", [])]
        print(f"Local Ollama: reachable; configured model installed: {config['model']['name'] in models}")
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        print(f"Local Ollama: unavailable ({exc})")
    if config.get("vision", {}).get("enabled"):
        try:
            with urlopen("http://127.0.0.1:11435/api/tags", timeout=2) as response:
                vision_models = json.load(response)
            installed = config["vision"]["model"] in {item.get("name") for item in vision_models.get("models", [])}
            print(f"Local vision server: reachable; configured model installed: {installed}")
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"Local vision server: unavailable ({exc})")
    if config.get("fast_answer", {}).get("enabled"):
        try:
            with urlopen("http://127.0.0.1:11435/api/tags", timeout=2) as response:
                fast_models = json.load(response)
            installed = config["fast_answer"]["name"] in {item.get("name") for item in fast_models.get("models", [])}
            print(f"Local fast-answer model: reachable; configured model installed: {installed}")
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"Local fast-answer model: unavailable ({exc}); standard model fallback remains available")
    print(f"Network plugins: {'enabled' if config['network_plugins_enabled'] else 'disabled'}")
    return 0

def gated(config, tier, name, arguments, summary, action):
    arguments = {key: value for key, value in arguments.items() if key not in {"config", "command"}}
    authorize(config, tier, name, arguments, summary, confirm)
    try:
        result = action()
        append_audit(config["_root"], name, arguments, {"ok": True, "result": result})
        return result
    except Exception as exc:
        append_audit(config["_root"], name, arguments, {"ok": False, "error": str(exc)})
        raise

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRIMUS local assistant")
    parser.add_argument("--config", type=Path, help="config.json in the PRIMUS root")
    subs = parser.add_subparsers(dest="command", required=True)
    for command, help_text in [
        ("init", "Create data directories inside the configured root"),
        ("doctor", "Check local runtime and model"),
        ("plugins", "List loaded plugins"),
        ("chat", "Interactive local session"),
        ("stop", "Activate global kill switch"),
        ("resume", "Clear global kill switch after confirmation"),
        ("history", "List private task metadata"),
        ("serve", "Run authenticated localhost API and scheduler"),
        ("mcp-serve", "Run the configured authenticated localhost status MCP server"),
        ("scheduler", "Run scheduler without API"),
        ("tray", "Run optional system tray and hotkey"),
        ("prompt", "Open a local task prompt"),
        ("dashboard", "Open local Mission Control in the PRIMUS browser profile"),
        ("voice", "Listen for Primus and speak local replies"),
        ("voice-doctor", "Check offline speech packages and model files"),
        ("gestures", "Open local gesture preview; camera requires Start Camera"),
        ("gesture-doctor", "Check local gesture engine and models without opening camera"),
        ("voice-status", "Show local voice process state without transcripts"),
        ("indicator", "Show the local animated voice status orb"),
        ("orb-open", "Show the existing voice orb while listening"),
        ("api-token-init", "Create local API keychain token after confirmation"),
        ("memory-list", "View approved memory"),
        ("context-list", "View brief saved task context"),
        ("context-clear", "Forget brief task context after confirmation"),
        ("schedule-list", "View schedules"),
        ("google-connect", "Authorize read-only Google access after confirmation"),
    ]:
        subs.add_parser(command, help=help_text)
    ask = subs.add_parser("ask", help="Run one natural-language task")
    ask.add_argument("task", nargs="+")
    planning = subs.add_parser("planning", help="Offline grounded STRIPS plans; no action execution")
    planning_actions = planning.add_subparsers(dest="planning_action", required=True)
    planning_solve = planning_actions.add_parser("solve")
    planning_solve.add_argument("path", help="Approved JSON planning problem path")
    planning_validate = planning_actions.add_parser("validate")
    planning_validate.add_argument("path")
    planning_validate.add_argument("--actions", nargs="*", default=[], help="Ordered symbolic action names to replay")
    knowledge = subs.add_parser("knowledge", help="Approved local document RAG memory")
    actions = knowledge.add_subparsers(dest="knowledge_action", required=True)
    actions.add_parser("list")
    index = actions.add_parser("index", help="Review and approve persisting one document")
    index.add_argument("path")
    search = actions.add_parser("search")
    search.add_argument("query")
    question = actions.add_parser("ask", help="Retrieve first, then answer with source citations")
    question.add_argument("question")
    read = actions.add_parser("read")
    read.add_argument("id"); read.add_argument("chunk", type=int)
    forget = actions.add_parser("forget")
    forget.add_argument("id")
    mcp = subs.add_parser("mcp", help="Explicitly configured localhost MCP servers")
    actions = mcp.add_subparsers(dest="mcp_action", required=True)
    actions.add_parser("servers")
    tools = actions.add_parser("tools"); tools.add_argument("server")
    invoke = actions.add_parser("call")
    invoke.add_argument("server"); invoke.add_argument("tool")
    invoke.add_argument("--arguments", default="{}", help="Tool arguments as a JSON object, or - to read JSON from stdin")
    mission = subs.add_parser("mission", help="Start a planner/coder/verifier mission on the local API")
    mission.add_argument("goal", nargs="+")
    mission.add_argument("--run-tests", action="store_true", help="Request sandbox tests; each execution asks for exact approval")
    image = subs.add_parser("image", help="Describe an approved JPEG, PNG, or WebP using the local vision model")
    image.add_argument("path", help="Image path in PRIMUS or an approved folder")
    image.add_argument("--question", help="Optional question about the image")
    image.add_argument("--mode", choices=["scene", "detail", "text", "gestures"], default="scene")
    image.add_argument("--region", type=float, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                       help="Focus region in normalized coordinates from 0 to 1")
    team = subs.add_parser("team", help="Plan independent read-only tasks for parallel agents")
    team.add_argument("request", nargs="*", help="Natural-language request to split")
    team.add_argument("--task", action="append", dest="subtasks", help="Explicit independent subtask; repeat 2-3 times")
    add = subs.add_parser("memory-add")
    add.add_argument("kind", choices=["fact", "preference"])
    add.add_argument("content")
    edit = subs.add_parser("memory-edit")
    edit.add_argument("id")
    edit.add_argument("content")
    delete = subs.add_parser("memory-delete")
    delete.add_argument("id")
    schedule = subs.add_parser("schedule-add")
    schedule.add_argument("kind", choices=["reminder", "agent"])
    schedule.add_argument("when", help="ISO 8601 with UTC offset")
    schedule.add_argument("content")
    schedule.add_argument("--repeat-seconds", type=int)
    secret = subs.add_parser("secret-set")
    secret.add_argument("name", help="brave-api-key or mcp-<name>-token; values are stored in the OS keychain")
    disable = subs.add_parser("schedule-disable")
    disable.add_argument("id")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        initialize(config)
        root = config["_root"]
        if args.command == "init":
            print(f"Initialized: {root}")
            return 0
        if args.command == "doctor":
            return doctor(config)
        if args.command == "planning":
            from .planning import run_plugin
            args_for_tool = {"path": args.path}
            if args.planning_action == "validate":
                args_for_tool["actions"] = args.actions
            tool = "formal_plan" if args.planning_action == "solve" else "formal_validate"
            result = run_plugin(config, discover(root), tool, args_for_tool, confirm)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result.get("status") == "solved" or result.get("valid") is True else 2
        if args.command == "mcp-serve":
            from .mcp_server import serve
            serve(config)
            return 0
        if args.command == "dashboard":
            from .dashboard import open_dashboard
            open_dashboard(config)
            return 0
        if args.command == "mission":
            from .dashboard import api_request
            result = api_request(config, "/missions", {"goal": " ".join(args.goal), "run_tests": args.run_tests})
            print(f"Mission: {result['id']}\nWorkspace: {result['workspace']}\nOpen Mission Control from the tray to follow progress and approvals.")
            return 0
        if args.command == "voice-doctor":
            from .voice import voice_status
            status = voice_status(config)
            print(json.dumps(status, indent=2))
            return 0 if all(status.values()) else 1
        if args.command == "voice-status":
            status_file = config["_root"] / "data" / "voice-status.json"
            print(status_file.read_text(encoding="utf-8") if status_file.is_file() else '{"state":"not-started"}')
            return 0
        if args.command == "gesture-doctor":
            from .gestures import model_paths
            import cv2
            print(json.dumps({"enabled": config.get("gestures", {}).get("enabled", False),
                              "opencv": cv2.__version__, "models_verified": bool(model_paths(config)),
                              "camera_opened": False}, indent=2))
            return 0
        if args.command == "gestures":
            from .gesture_ui import run_gesture_preview
            run_gesture_preview(config)
            return 0
        if args.command == "indicator":
            from .voice_indicator import run_indicator
            run_indicator(config)
            return 0
        if args.command == "orb-open":
            marker = root / "data" / "orb-open"
            marker.write_text("Open the listening orb until dismissed.\n", encoding="utf-8")
            print("Orb requested. Close its window to dismiss it.")
            return 0
        if args.command == "stop":
            (root / "data" / "STOP").write_text("Stopped by user\n", encoding="utf-8")
            print("Kill switch active.")
            return 0
        if args.command == "resume":
            marker = root / "data" / "STOP"
            if marker.exists() and confirm(f"Remove kill switch marker {marker} and allow tasks again? [y/N] "):
                marker.unlink()
                print("Kill switch cleared.")
                return 0
            print("Kill switch unchanged.")
            return 1
        store = Store(root)
        if args.command == "history":
            print(json.dumps(store.list_tasks(), indent=2))
            return 0
        if args.command == "memory-list":
            print(json.dumps(store.list_memories(), indent=2, ensure_ascii=False))
            return 0
        if args.command == "context-list":
            print(json.dumps(store.get_context(config.get("memory", {}).get("context_retention_days", 30)),
                             indent=2, ensure_ascii=False))
            return 0
        if args.command == "context-clear":
            retention = config.get("memory", {}).get("context_retention_days", 30)
            reviewed = store.get_context(retention)
            def clear_reviewed_context():
                if store.get_context(retention) != reviewed:
                    raise ValueError("Task context changed during approval; review it again")
                store.clear_context(approved=True)
            gated(config, "DESTRUCTIVE", "context_clear", {},
                  "Forget these recent task context entries: " +
                  json.dumps(reviewed, ensure_ascii=False) + " (approved facts and task history remain)",
                  clear_reviewed_context)
            print("Recent task context cleared.")
            return 0
        if args.command == "memory-add":
            result = gated(config, "DESTRUCTIVE", "memory_add", vars(args), f"Save {args.kind}: {args.content}",
                           lambda: store.add_memory(args.kind, args.content, approved=True))
            print(f"Saved memory: {result}")
            return 0
        if args.command == "memory-edit":
            gated(config, "DESTRUCTIVE", "memory_edit", vars(args), f"Replace memory {args.id} with: {args.content}",
                  lambda: store.edit_memory(args.id, args.content, approved=True))
            print("Memory edited.")
            return 0
        if args.command == "memory-delete":
            gated(config, "DESTRUCTIVE", "memory_delete", vars(args), f"Delete memory {args.id}",
                  lambda: store.delete_memory(args.id, approved=True))
            print("Memory deleted.")
            return 0
        if args.command == "schedule-list":
            print(json.dumps(store.list_schedules(), indent=2, ensure_ascii=False))
            return 0
        if args.command == "schedule-add":
            result = gated(config, "DESTRUCTIVE", "schedule_add", vars(args),
                           f"Schedule {args.kind} at {args.when}: {args.content}; repeat seconds={args.repeat_seconds}",
                           lambda: store.add_schedule(args.kind, args.content, args.when, args.repeat_seconds))
            print(f"Scheduled: {result}")
            return 0
        if args.command == "schedule-disable":
            gated(config, "DESTRUCTIVE", "schedule_disable", vars(args), f"Disable schedule {args.id}",
                  lambda: store.disable_schedule(args.id))
            print("Schedule disabled.")
            return 0
        if args.command == "api-token-init":
            from .auth import api_token, get_secret
            if get_secret("api-token"):
                print("Local API token already exists in OS keychain.")
                return 0
            gated(config, "DESTRUCTIVE", "api_token_init", {},
                  "Create a random PRIMUS API token in the OS keychain",
                  lambda: api_token(create=True) and "created")
            print("Local API token created in OS keychain.")
            return 0
        if args.command == "secret-set":
            from getpass import getpass
            from .auth import put_secret
            if args.name != "brave-api-key" and not re.fullmatch(r"mcp-[a-z0-9_-]{1,30}-token", args.name):
                raise ValueError("Unsupported secret name")
            value = getpass(f"Enter {args.name} (hidden): ").strip()
            if not value:
                raise ValueError("Empty secret")
            gated(config, "DESTRUCTIVE", "mcp_secret_set" if args.name.startswith("mcp-") else "secret_set", {"name": args.name},
                  f"Store {args.name} in OS keychain", lambda: put_secret(args.name, value) or "stored")
            print("Secret stored in OS keychain.")
            return 0
        if args.command == "google-connect":
            from .google import enabled, connect
            enabled(config)
            gated(config, "EXTERNAL", "google_connect", {},
                  "Open Google OAuth in browser, request Gmail read-only and Calendar events read-only scopes, and store refresh token in OS keychain",
                  lambda: connect(config) or "connected")
            print("Google connected with read-only scopes.")
            return 0
        browser_tools = {"browser_open", "browser_navigate", "browser_read", "browser_click", "browser_type"} if config.get("browser", {}).get("enabled") else set()
        plugins = discover(root, network_enabled=config["network_plugins_enabled"], enabled_network_plugins=browser_tools)
        if args.command in {"knowledge", "mcp"}:
            if args.command == "knowledge" and args.knowledge_action == "ask":
                from .knowledge import answer
                print(json.dumps(answer(config, args.question), ensure_ascii=False, indent=2))
                return 0
            if args.command == "mcp" and args.mcp_action == "servers":
                from .mcp_client import settings
                print(json.dumps(settings(config), ensure_ascii=False, indent=2))
                return 0
            if args.command == "knowledge":
                action = args.knowledge_action
                name = {"list": "knowledge_list", "index": "knowledge_index", "search": "knowledge_search", "read": "knowledge_read", "forget": "knowledge_forget"}[action]
                inputs = {key: getattr(args, key) for key in {"list": (), "index": ("path",), "search": ("query",), "read": ("id", "chunk"), "forget": ("id",)}[action]}
            else:
                name = "mcp_list" if args.mcp_action == "tools" else "mcp_call"
                inputs = {"server": args.server}
                if args.mcp_action == "call":
                    payload = sys.stdin.read(16001) if args.arguments == "-" else args.arguments
                    if len(payload.encode("utf-8")) > 16000:
                        raise ValueError("MCP arguments exceed 16 KB")
                    inputs.update(tool=args.tool, arguments=json.loads(payload))
            from .plugins import validate
            plugin = plugins[name]
            validate(plugin.input_schema, inputs)
            summary = plugin.summary(config, inputs) if plugin.summary else plugin.description
            result = gated(config, plugin.tier, name, inputs, summary, lambda: plugin.run(config, inputs))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "plugins":
            for plugin in plugins.values():
                print(f"{plugin.name} [{plugin.tier}; network={plugin.network}] {plugin.description}")
            return 0
        if args.command == "serve":
            from .api import serve
            serve(config)
            return 0
        if args.command == "scheduler":
            Scheduler(config, store, plugins).run_forever(threading.Event())
            return 0
        if args.command in {"tray", "prompt"}:
            from .tray import prompt_once, run_tray
            (run_tray if args.command == "tray" else prompt_once)(config)
            return 0
        if args.command == "voice":
            from .voice import run_voice
            run_voice(config)
            return 0
        if args.command == "image":
            from .image_service import run_image
            image_args = {"path": args.path, "mode": args.mode}
            if args.region:
                image_args["region"] = args.region
            if args.question:
                image_args["question"] = args.question
            print(json.dumps(run_image(config, plugins, store, image_args), ensure_ascii=False, indent=2))
            return 0
        runner = TaskRunner(config, plugins, store, confirm=confirm)
        if args.command == "ask":
            from .team import TeamRunner, team_request
            task = " ".join(args.task)
            from .missions import mission_request
            mission_goal = mission_request(task)
            if mission_goal is not None:
                from .dashboard import api_request
                result = api_request(config, "/missions", {"goal": mission_goal})
                print(f"Mission {result['id']} started. Open Mission Control from the tray.")
                return 0
            team_task = team_request(task)
            print(TeamRunner(config, plugins, store).run(team_task) if team_task is not None else runner.run(task))
            return 0
        if args.command == "team":
            from .team import TeamRunner
            if args.subtasks and args.request:
                raise ValueError("Use either a request or repeated --task options")
            print(TeamRunner(config, plugins, store).run(" ".join(args.request) if args.request else None,
                                                          tasks=args.subtasks))
            return 0
        print("PRIMUS local chat. Type /quit to exit.")
        while True:
            try:
                task = input("primus> ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return 0
            if task == "/quit":
                return 0
            if task:
                try:
                    from .team import TeamRunner, team_request
                    team_task = team_request(task)
                    print(TeamRunner(config, plugins, store).run(team_task) if team_task is not None else runner.run(task))
                except Exception as exc:
                    print(f"Task failed: {exc}", file=sys.stderr)
    except Exception as exc:
        if sys.stderr is not None:
            print(f"PRIMUS error: {exc}", file=sys.stderr)
        return 1
    return 0
