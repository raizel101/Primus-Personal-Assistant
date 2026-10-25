"""Conservative tool hints for simple requests; ambiguous tasks keep every tool."""
from __future__ import annotations

import re

from .plugins import Plugin


GROUPS = {
    "planning": {"formal_plan", "formal_validate"},
    "knowledge": {"knowledge_index", "knowledge_list", "knowledge_search", "knowledge_read", "knowledge_forget"},
    "mcp": {"mcp_servers", "mcp_list", "mcp_call"},
    "files": {"file_read", "file_list", "file_write", "file_overwrite"},
    "coding": {"file_read", "file_list", "file_write", "file_overwrite", "code_patch", "code_check", "script_run"},
    "schedule": {"schedule_add", "schedule_list", "schedule_disable"},
    "memory": {"memory_add", "memory_list", "memory_edit", "memory_delete", "context_list", "context_clear"},
    "browser": {"browser_open", "browser_navigate", "browser_read", "browser_click", "browser_type"},
    "apps": {"app_list", "app_open", "installed_app_search", "installed_app_open"},
    "desktop": {"desktop_input"},
    "windows": {"window_list", "window_manage", "window_input", "ui_controls", "ui_activate", "ui_set_text", "window_vision"},
    "images": {"image_analyze"},
    "scripts": {"script_run"},
    "shell": {"shell_run"},
}
KNOWN = set().union(*GROUPS.values())
CUES = {
    "planning": r"\b(?:strips|formal plan|formal planning|symbolic plan)\b",
    "knowledge": r"\b(?:knowledge|indexed|rag)\b|\bmy (?:notes|documents)\b",
    "mcp": r"\bmcp\b",
    "files": r"\b(?:file|files|folder|folders|directory|directories|downloads|documents|desktop folder|outputs)\b",
    "coding": r"\b(?:code|coding|program|programming|function|class|debug|bug|refactor|javascript|typescript|html|css|json)\b|\b(?:write|create|generate|edit|modify|fix)\s+(?:a\s+)?python\b|\.(?:py|js|ts|html|css|json)\b",
    "schedule": r"\b(?:remind|reminder|reminders|schedule|scheduled)\b",
    "memory": r"\b(?:memory|memories|remember|forget|context|preference|preferences)\b",
    "browser": r"\b(?:browser|chrome|website|webpage|url|https?://)\b",
    "apps": r"\b(?:app|apps|application|applications|calculator|notepad|explorer|finder)\b",
    "desktop": r"\b(?:mouse|keyboard|keystroke)\b",
    "windows": r"\b(?:windows?|screen|screenshot|buttons?|fields?|controls?|click|press)\b",
    "images": r"\b(?:image|images|picture|pictures|photo|photos|gesture|gestures|\.jpe?g|\.png|\.webp)\b",
    "scripts": r"\b(?:script|scripts)\b|\.(?:py|sh)\b",
    "shell": r"\b(?:shell|terminal|command prompt|python version)\b",
}
COMPOUND = re.compile(r"\b(?:and|then|after|before|while|also)\b|[;\n]", re.I)
FOLLOW_UP = re.compile(r"\b(?:it|that|there|this|previous|again)\b", re.I)
CODE_REQUEST = re.compile(
    r"\b(?:build|create|write|implement|fix|refactor|debug)\b.*"
    r"\b(?:code|script|program|function|class|website|web\s*app|landing page|html|css|javascript|python|json)\b",
    re.I,
)
OTHER_ACTION = re.compile(
    r"\b(?:open|launch|click|browser|chrome|email|send|remind|schedule|window|screen)\b", re.I
)


def select_plugins(task: str, plugins: dict[str, Plugin], history: list[dict]) -> dict[str, Plugin]:
    """Reduce prompt size only when the request clearly names one known ability.

    All discovered tools remain available for multi-step, contextual, or unfamiliar
    requests so adding a plugin never requires changing the agent's core loop.
    """
    if set(plugins) - KNOWN or (history and FOLLOW_UP.search(task)):
        return plugins
    if CODE_REQUEST.search(task) and not OTHER_ACTION.search(task):
        names = GROUPS["coding"]
        return {name: plugin for name, plugin in plugins.items() if name in names}
    if COMPOUND.search(task):
        return plugins
    groups = {group for group, pattern in CUES.items() if re.search(pattern, task, re.I)}
    if "images" in groups and groups <= {"images", "files"}:
        groups = {"images"}
    if "coding" in groups and groups <= {"coding", "files", "scripts"}:
        groups = {"coding"}
    if len(groups) != 1:
        return plugins
    names = GROUPS[next(iter(groups))]
    selected = {name: plugin for name, plugin in plugins.items() if name in names}
    return selected or plugins
