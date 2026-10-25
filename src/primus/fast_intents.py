"""Conservative local shortcuts for exact, unambiguous desktop requests."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .audit import append_audit
from .plugins import validate
from .security import authorize


APP_ALIASES = {"google chrome": "chrome", "chrome": "chrome", "microsoft edge": "edge",
               "edge": "edge", "mozilla firefox": "firefox", "firefox": "firefox",
               "notepad": "notepad", "text editor": "notepad", "calculator": "calculator",
               "calc": "calculator", "file explorer": "file_manager", "file manager": "file_manager",
               "explorer": "file_manager", "finder": "file_manager", "terminal": "terminal",
               "command prompt": "terminal"}

WEBSITE_ALIASES = {
    "google": "https://www.google.com/", "youtube": "https://www.youtube.com/",
    "you tube": "https://www.youtube.com/", "wikipedia": "https://www.wikipedia.org/",
    "github": "https://github.com/", "git hub": "https://github.com/",
    "gmail": "https://mail.google.com/", "reddit": "https://www.reddit.com/",
    "stackoverflow": "https://stackoverflow.com/", "stack overflow": "https://stackoverflow.com/",
}
WEBSITE_REQUEST = re.compile(
    r"^(?:please\s+)?(?:open|visit|go\s+to|navigate\s+to)\s+(?:the\s+)?(?:website\s+|site\s+)?"
    r"(.+?)(?:\s+in\s+(?:google\s+)?chrome[.!?]?)?(?:\s+please)?$", re.I)
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?::[0-9]{1,5})?(?:[/?#][^\s]*)?", re.I)


def website_request(task: str) -> tuple[str, dict] | None:
    """Resolve a single explicit website, preserving URL path/query case."""
    match = WEBSITE_REQUEST.fullmatch(task.strip())
    if not match:
        return None
    target = match.group(1).strip()
    alias = target.lower().rstrip(".!?")
    if alias in WEBSITE_ALIASES:
        return "browser_navigate", {"url": WEBSITE_ALIASES[alias]}
    if not any(c in target for c in "/?#:"):
        target = re.sub(r"\s+dot\s+", ".", target, flags=re.I).rstrip(".!?")
    if not target.lower().startswith("https://"):
        if not DOMAIN.fullmatch(target):
            return None
        target = "https://" + target
    if len(target) > 2048 or any(c.isspace() or ord(c) < 32 for c in target) or "\\" in target:
        return None
    try:
        parts = urlsplit(target)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or not DOMAIN.fullmatch(parts.netloc)
                or (parts.port is not None and not 1 <= parts.port <= 65535)):
            return None
    except ValueError:
        return None
    return "browser_navigate", {"url": target}


def recognize(task: str) -> tuple[str, dict] | None:
    website = website_request(task)
    if website is not None:
        return website
    plain = task.strip().lower().rstrip(".!?").strip()
    plain = re.sub(r"^please\s+", "", plain)
    match = re.fullmatch(r"(?:open|launch|start)\s+(?:the\s+)?(.+?)(?:\s+please)?", plain)
    if match and match.group(1) in APP_ALIASES:
        return "app_open", {"name": APP_ALIASES[match.group(1)]}
    if plain in {"list apps", "list applications", "what apps can you open", "what applications can you open"}:
        return "app_list", {}
    if plain in {"list installed apps", "show installed apps", "what apps are installed"}:
        return "installed_app_search", {"query": ""}
    if plain in {"list my outputs folder", "list my primus outputs folder", "list primus outputs folder"}:
        return "file_list", {"path": "outputs"}
    if plain in {"list my reminders", "list reminders", "what reminders are scheduled", "what reminders are scheduled for me"}:
        return "schedule_list", {}
    if plain in {"list open windows", "list visible windows", "what windows are open", "show open windows"}:
        return "window_list", {}
    return None


def execute(config: dict, plugins: dict, task: str, confirm, on_success=None,
            audit_context: dict | None = None) -> str | None:
    action = recognize(task)
    if action is None:
        return None
    name, args = action
    if name not in plugins:
        return None
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    plugin = plugins[name]
    validate(plugin.input_schema, args)
    tier = plugin.effective_tier(config, args) if plugin.effective_tier else plugin.tier
    summary = plugin.summary(config, args) if plugin.summary else plugin.description
    authorize(config, tier, name, args, summary, confirm)
    try:
        result = plugin.run(config, args)
        append_audit(config["_root"], name, {**args, **(audit_context or {})}, {"ok": True, **result})
        if on_success is not None:
            on_success(name, args, result)
    except Exception as exc:
        append_audit(config["_root"], name, {**args, **(audit_context or {})}, {"ok": False, "error": str(exc)})
        raise
    if name == "app_list":
        available = [row["name"] for row in result["applications"] if row["available"]]
        return "Quick apps: " + (", ".join(available) if available else "none found") + ". Search installed apps by name for more."
    if name == "installed_app_search":
        matches = result["matches"]
        names = ", ".join(row["name"] for row in matches[:15])
        more = f", and {result['count'] - 15} more" if result["count"] > 15 else ""
        return (f"{result['count']} installed apps match; first results: {names}{more}."
                if matches else "No installed apps match.")
    if name == "file_list":
        entries = result["entries"]
        names = ", ".join(row["name"] for row in entries[:12])
        more = f", and {len(entries) - 12} more" if len(entries) > 12 else ""
        return f"{result['path']}: {names}{more}." if entries else f"{result['path']} is empty."
    if name == "schedule_list":
        active = [row for row in result["schedules"] if row["status"] == "pending"]
        if not active:
            return "No active reminders or scheduled tasks."
        summary = "; ".join(f"{row['content']} ({row['due_utc']})" for row in active[:5])
        more = f"; and {len(active) - 5} more" if len(active) > 5 else ""
        return f"Scheduled: {summary}{more}."
    if name == "window_list":
        windows = result["windows"]
        if not windows:
            return "No visible desktop windows found."
        summary = "; ".join(f"{row['title']} ({row['application']})" for row in windows[:8])
        more = f"; and {len(windows) - 8} more" if len(windows) > 8 else ""
        return f"{len(windows)} visible windows: {summary}{more}."
    if name == "browser_navigate":
        # Report the actual final URL, including redirects, not a guessed result.
        final = result.get("url")
        if not isinstance(final, str) or not final.startswith("https://"):
            return "Task needs review; Chrome navigation returned no verified HTTPS page URL."
        return f"Opened {final} in Google Chrome (PRIMUS profile)."
    display = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "firefox": "Firefox",
               "notepad": "Notepad", "calculator": "Calculator", "file_manager": "File Manager",
               "terminal": "Terminal"}[args["name"]]
    if result.get("window_verified"):
        return f"Opened {display}."
    return f"Task needs review; I attempted to open {display}, but could not verify a visible window."
