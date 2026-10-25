"""Configuration and root-only initialization."""
from __future__ import annotations
import json
import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DATA_DIRS = ("data", "logs", "outputs", "temp", "models")

class ConfigError(ValueError):
    pass

def load_config(path: Path | None = None) -> dict:
    config_path = Path(path) if path else PACKAGE_ROOT / "config.json"
    with config_path.open("r", encoding="utf-8-sig") as handle:
        config = json.load(handle)
    if config.get("schema_version") != 1:
        raise ConfigError("Unsupported config schema")
    root = Path(config["root"]).expanduser().resolve()
    if root != config_path.parent.resolve():
        raise ConfigError("Move the full project and update root together")
    knowledge = config.get("knowledge", {"enabled": False})
    if not isinstance(knowledge, dict) or set(knowledge) - {"enabled"} or not isinstance(knowledge.get("enabled", False), bool):
        raise ConfigError("knowledge requires boolean enabled")
    from .mcp_client import settings, MCPError
    from .mcp_server import validate_settings
    try:
        validate_settings(config)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        settings(config)
    except MCPError as exc:
        raise ConfigError(str(exc)) from exc
    if config["model"]["provider"] != "ollama":
        raise ConfigError("Phase 1 supports local Ollama only")
    if not re.fullmatch(r"[1-9][0-9]*[smh]", str(config["model"].get("keep_alive", "30m"))):
        raise ConfigError("model.keep_alive must be a positive duration such as 30m")
    if config.get("cloud_enabled"):
        raise ConfigError("Cloud model providers remain disabled")
    agents = config.get("agents", {"max_parallel": 2, "max_subtasks": 3})
    if not isinstance(agents, dict):
        raise ConfigError("agents must be an object")
    parallel, subtasks = agents.get("max_parallel", 2), agents.get("max_subtasks", 3)
    if (isinstance(parallel, bool) or not isinstance(parallel, int) or not 2 <= parallel <= 4
            or isinstance(subtasks, bool) or not isinstance(subtasks, int) or not 2 <= subtasks <= 4
            or parallel > subtasks):
        raise ConfigError("agents.max_parallel and max_subtasks must be 2 to 4, with max_parallel <= max_subtasks")
    if not isinstance(config.get("network_plugins_enabled"), bool):
        raise ConfigError("network_plugins_enabled must be a boolean")
    missions = config.get("missions", {})
    if not isinstance(missions, dict) or not isinstance(missions.get("enabled", False), bool):
        raise ConfigError("missions.enabled must be a boolean")
    mission_steps = missions.get("max_steps", 8)
    if isinstance(mission_steps, bool) or not isinstance(mission_steps, int) or not 3 <= mission_steps <= 12:
        raise ConfigError("missions.max_steps must be 3 to 12")
    memory = config.get("memory", {})
    if not isinstance(memory, dict) or not isinstance(memory.get("task_context_enabled", False), bool):
        raise ConfigError("memory.task_context_enabled must be a boolean")
    days = memory.get("context_retention_days", 30)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ConfigError("memory.context_retention_days must be 1 to 365")
    vision = config.get("vision", {})
    if not isinstance(vision, dict) or not isinstance(vision.get("enabled", False), bool):
        raise ConfigError("vision.enabled must be a boolean")
    if vision.get("enabled"):
        if vision.get("endpoint") != "http://127.0.0.1:11435/api/chat":
            raise ConfigError("Vision endpoint must be localhost port 11435")
        if vision.get("model") != "qwen3-vl:4b":
            raise ConfigError("Unsupported local vision model")
        timeout = vision.get("timeout_seconds", 120)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 10 <= timeout <= 300:
            raise ConfigError("vision.timeout_seconds must be 10 to 300")
    fast = config.get("fast_answer", {})
    gestures = config.get("gestures", {})
    if not isinstance(gestures, dict) or not isinstance(gestures.get("enabled", False), bool):
        raise ConfigError("gestures.enabled must be a boolean")
    for key, minimum, maximum, default in (("camera_index", 0, 9, 0), ("hold_ms", 300, 3000, 600), ("max_fps", 3, 30, 12)):
        value = gestures.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ConfigError(f"gestures.{key} must be {minimum} to {maximum}")
    score = gestures.get("min_hand_score", 0.8)
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0.5 <= score <= 0.99:
        raise ConfigError("gestures.min_hand_score must be 0.5 to 0.99")
    if not isinstance(fast, dict) or not isinstance(fast.get("enabled", False), bool):
        raise ConfigError("fast_answer.enabled must be a boolean")
    if not isinstance(fast.get("auto_simple", True), bool):
        raise ConfigError("fast_answer.auto_simple must be a boolean")
    if fast.get("enabled"):
        if fast.get("endpoint") != "http://127.0.0.1:11435/api/chat" or fast.get("name") != "qwen3:1.7b":
            raise ConfigError("Fast answer must use the approved local qwen3:1.7b server")
        timeout = fast.get("timeout_seconds", 45)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 5 <= timeout <= 120:
            raise ConfigError("fast_answer.timeout_seconds must be 5 to 120")
        output_tokens = fast.get("max_output_tokens", 128)
        if isinstance(output_tokens, bool) or not isinstance(output_tokens, int) or not 32 <= output_tokens <= 256:
            raise ConfigError("fast_answer.max_output_tokens must be 32 to 256")
        if not re.fullmatch(r"[1-9][0-9]*[smh]", str(fast.get("keep_alive", "30m"))):
            raise ConfigError("fast_answer.keep_alive must be a positive duration such as 30m")
    browser = config.get("browser", {})
    if not isinstance(browser, dict) or not isinstance(browser.get("enabled", False), bool):
        raise ConfigError("browser.enabled must be a boolean")
    installed_apps = config.get("installed_apps", {})
    if not isinstance(installed_apps, dict) or not isinstance(installed_apps.get("enabled", False), bool):
        raise ConfigError("installed_apps.enabled must be a boolean")
    scripts = config.get("script_execution", {})
    if not isinstance(scripts, dict) or not isinstance(scripts.get("enabled", False), bool):
        raise ConfigError("script_execution.enabled must be a boolean")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", scripts.get("wsl_distro", "Ubuntu")):
        raise ConfigError("script_execution.wsl_distro is invalid")
    api = config.get("local_api", {})
    if api.get("host") != "127.0.0.1" or not isinstance(api.get("port"), int) or not (1024 <= api["port"] <= 65535):
        raise ConfigError("Local API must bind to 127.0.0.1 on a valid nonprivileged port")
    scheduler = config.get("scheduler", {})
    if not (0.2 <= float(scheduler.get("poll_seconds", 0)) <= 60):
        raise ConfigError("Invalid scheduler poll interval")
    if config.get("google", {}).get("enabled") and not config.get("network_plugins_enabled"):
        raise ConfigError("Google connector requires network_plugins_enabled")
    if not isinstance(config.get("tray", {}).get("auto_start_voice", False), bool):
        raise ConfigError("tray.auto_start_voice must be a boolean")
    if not isinstance(config.get("tray", {}).get("show_voice_indicator", False), bool):
        raise ConfigError("tray.show_voice_indicator must be a boolean")

    if config["model"]["endpoint"] != "http://127.0.0.1:11434/api/chat":
        raise ConfigError("Ollama endpoint must be 127.0.0.1:11434")
    voice = config.get("voice", {})
    if voice:
        from .voice import inside_root
        for key in ("stt_model_dir", "tts_model"):
            if not isinstance(voice.get(key), str):
                raise ConfigError(f"Missing voice {key}")
            inside_root(root, voice[key])
        if voice.get("wake_word", "").lower() != "primus":
            raise ConfigError("Phase 3 wake word must be Primus")
        if voice.get("stt_device", "auto") not in {"auto", "cpu", "cuda"}:
            raise ConfigError("voice.stt_device must be auto, cpu, or cuda")
        if not 100 <= int(voice.get("energy_threshold", 0)) <= 5000:
            raise ConfigError("Invalid voice energy threshold")
        if not 0.3 <= float(voice.get("silence_seconds", 0)) <= 3:
            raise ConfigError("Invalid voice silence interval")
        if not 3 <= float(voice.get("command_timeout_seconds", 0)) <= 30:
            raise ConfigError("Invalid voice command timeout")
    policy = config.get("permissions", {})
    if not isinstance(policy, dict) or not isinstance(policy.get("auto_approve", False), bool):
        raise ConfigError("permissions.auto_approve must be a boolean")
    if policy.get("read") not in {"auto", "confirm"} or policy.get("write") not in {"approved_folders", "confirm"}:
        raise ConfigError("Invalid READ or WRITE permission setting")
    if policy.get("destructive") != "confirm" or policy.get("external") != "confirm":
        raise ConfigError("DESTRUCTIVE and EXTERNAL always require confirmation")
    config["_root"] = root
    return config

def initialize(config: dict) -> None:
    root = config["_root"]
    for name in DATA_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "plugins").mkdir(exist_ok=True)
