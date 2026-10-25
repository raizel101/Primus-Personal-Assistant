"""Path and permission gates used before every plugin operation."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Callable

BLOCKED_PARTS = {
    ".ssh", ".aws", ".gnupg", ".config", ".kube", ".docker", ".azure", ".gcloud",
    "keychain", "keychains", "keyrings", ".password-store",
    "credentials", "credential", "secrets", "browser", "browsers", "browser-automation",
    "user data", "profiles", "login data", "cookies", ".env",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "private_key", "private-key",
    "chrome", "chromium", "firefox", "safari", "mozilla", "brave-browser", "opera", "vivaldi"
}
BLOCKED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}
BLOCKED_BASENAMES = {".npmrc", ".netrc", ".pypirc", ".git-credentials", "secrets.json", "credentials.json", "token.json", "auth.json"}

class SecurityError(PermissionError):
    pass

def guard_path(raw: str, config: dict, *, write: bool = False) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise SecurityError("A non-empty path is required")
    root: Path = config["_root"]
    candidate = Path(raw).expanduser()
    approved = [Path(p).expanduser().resolve() for p in config.get("approved_folders", [])]
    if not candidate.is_absolute():
        # A bare user-folder name is an alias only when that folder was
        # explicitly approved. Other relative paths remain inside PRIMUS.
        first = candidate.parts[0].casefold() if candidate.parts else ""
        matches = ([base for base in approved if base.name.casefold() == first]
                   if first in {"documents", "downloads", "desktop"} else [])
        if len(matches) > 1:
            raise SecurityError("Approved folder alias is ambiguous; use an absolute path")
        candidate = matches[0].joinpath(*candidate.parts[1:]) if matches else root / candidate
    candidate = candidate.resolve(strict=False)
    lowered = [part.lower() for part in candidate.parts]
    if (any(part in BLOCKED_PARTS or part.startswith(".env") for part in lowered)
            or candidate.name.lower() in BLOCKED_BASENAMES
            or candidate.suffix.lower() in BLOCKED_SUFFIXES):
        raise SecurityError("Credential stores, keys, browser profiles, and secret files are blocked")
    allowed = [root, *approved]
    if not any(candidate == base or candidate.is_relative_to(base) for base in allowed):
        raise SecurityError("Path is outside the PRIMUS root and approved folders")
    if write and candidate.is_relative_to(root):
        relative = candidate.relative_to(root)
        protected = {"src", "plugins", "scripts", "tests", "logs", "data", "models", "vendor", "temp"}
        protected_files = {"config.json", "decision.md", "primus.py", "readme.md", "plugin_guide.md", "requirements-phase2.txt"}
        if not relative.parts or relative.parts[0].lower() in protected or relative.name.lower() in protected_files or (len(relative.parts) == 1 and relative.suffix.lower() in {".py", ".pth"}):
            raise SecurityError("PRIMUS source, config, audit, and runtime files are protected from tool writes")
    if write and candidate.exists() and candidate.is_dir():
        raise SecurityError("Cannot write file content to a directory")
    return candidate

def authorize(config: dict, tier: str, tool: str, arguments: dict, summary: str, confirm: Callable[[str], bool] | None) -> None:
    policy = config.get("permissions", {})
    if tier not in {"READ", "WRITE", "DESTRUCTIVE", "EXTERNAL"}:
        raise SecurityError(f"Unknown permission tier: {tier}")
    if policy.get("auto_approve") is True:
        from .audit import append_audit
        from datetime import datetime, timezone, timedelta
        import hashlib
        if ((config["_root"] / "data" / "STOP").exists()
                or (config.get("_cancel_event") and config["_cancel_event"].is_set())):
            raise SecurityError("Task stopped; automatic approval was not granted")
        digest = hashlib.sha256(json.dumps(
            {"tool": tool, "tier": tier, "arguments": arguments, "summary": summary},
            sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        append_audit(config["_root"], "automatic_approval", {"tool": tool, "tier": tier, "arguments": arguments},
                     {"approved": True, "approval_mode": "automatic", "action_sha256": digest})
        stamp = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M")
        with (config["_root"] / "decision.md").open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n## [{stamp}] Automatic tool approval\n"
                f"- Change: Configured automatic mode authorized {tier} tool {tool}; action SHA-256 {digest}.\n"
                "- Reason: User requested automatic approval for all tool actions. This was not an individual manual review.\n"
                f"- Files affected: Tool targets recorded in {config['_root'] / 'logs/audit.jsonl'}; {config['_root'] / 'decision.md'}.\n"
                "- Decided by: Primus (with my approval)\n"
                "- Impact: This action proceeds without a prompt; its effects may not be reversible. Set permissions.auto_approve=false and restart to restore manual confirmation for subsequent actions.\n")
        return
    if tier == "READ" and policy.get("read", "auto") == "auto":
        return
    if tier == "WRITE" and policy.get("write", "approved_folders") == "approved_folders":
        return
    if tier not in {"READ", "WRITE", "DESTRUCTIVE", "EXTERNAL"}:
        raise SecurityError(f"Unknown permission tier: {tier}")
    if confirm is None:
        raise SecurityError("Explicit interactive confirmation is required")
    prompt = f"{tier} action\nTool: {tool}\nAction: {summary}\nArguments: {json.dumps(arguments, ensure_ascii=False)}\nApprove this exact action? [y/N] "
    approved = confirm(prompt)
    if tool.startswith(("knowledge_", "mcp_")):
        from datetime import datetime, timezone, timedelta
        import hashlib
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        stamp = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M")
        entry = (f"\n## [{stamp}] {'Approved' if approved else 'Not approved'} {tool}\n"
                 f"- Change: Exact {tool} action was {'approved' if approved else 'not approved'}; reviewed prompt SHA-256 {digest}.\n"
                 "- Reason: Explicit consent is required for persistent knowledge changes and MCP tool calls.\n"
                 f"- Files affected: {config['_root'] / 'data'}; {config['_root'] / 'logs/audit.jsonl'}; configured external MCP tool target if applicable.\n"
                 + ("- Decided by: me\n" if approved else "- Decided by: Primus (with my approval)\n") +
                 "- Impact: One action only; no future action is authorized. Index removal preserves the original source; reversal of external changes needs fresh review.\n")
        with (config["_root"] / "decision.md").open("a", encoding="utf-8") as handle:
            handle.write(entry)
    if not approved:
        if tool.startswith(("knowledge_", "mcp_")):
            raise SecurityError("Action not approved; nothing was executed")
        raise SecurityError("Action rejected by user")
