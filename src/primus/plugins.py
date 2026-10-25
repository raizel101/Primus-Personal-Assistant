"""Plugin contracts, discovery, and a small JSON-schema validator."""
from __future__ import annotations
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import re
from typing import Callable, Any

@dataclass(frozen=True)
class Plugin:
    name: str
    description: str
    input_schema: dict
    tier: str
    network: bool
    run: Callable[[dict, dict], dict]
    effective_tier: Callable[[dict, dict], str] | None = None
    summary: Callable[[dict, dict], str] | None = None

class PluginError(ValueError):
    pass

def validate(schema: dict, value: Any, path: str = "$") -> None:
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
    if kind in types and (not isinstance(value, types[kind]) or (kind in {"integer", "number"} and isinstance(value, bool))):
        raise PluginError(f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise PluginError(f"{path} must be one of {schema['enum']}")
    if kind == "object":
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise PluginError(f"{path}.{key} is required")
        for key, item in value.items():
            if key in properties:
                validate(properties[key], item, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise PluginError(f"{path}.{key} is not allowed")
    if kind == "array":
        for index, item in enumerate(value):
            validate(schema.get("items", {}), item, f"{path}[{index}]")
    if kind == "string":
        if len(value) > schema.get("maxLength", 1000000):
            raise PluginError(f"{path} is too long")
        if len(value) < schema.get("minLength", 0):
            raise PluginError(f"{path} is too short")

def discover(root: Path, *, network_enabled: bool = False, enabled_network_plugins: set[str] | None = None) -> dict[str, Plugin]:
    found: dict[str, Plugin] = {}
    for file in sorted((root / "plugins").glob("*.py")):
        if file.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"primus_ext_{file.stem}", file)
        if spec is None or spec.loader is None:
            raise PluginError(f"Cannot load {file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for plugin in getattr(module, "PLUGINS", []):
            if not isinstance(plugin, Plugin):
                raise PluginError(f"{file}: PLUGINS must contain Plugin objects")
            if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", plugin.name):
                raise PluginError(f"Invalid plugin name: {plugin.name}")
            if plugin.name in found:
                raise PluginError(f"Duplicate plugin name: {plugin.name}")
            if plugin.tier not in {"READ", "WRITE", "DESTRUCTIVE", "EXTERNAL"}:
                raise PluginError(f"Invalid tier: {plugin.tier}")
            if plugin.input_schema.get("type") != "object":
                raise PluginError(f"{plugin.name}: input schema must be an object")
            if not plugin.network or network_enabled or plugin.name in (enabled_network_plugins or set()):
                found[plugin.name] = plugin
    return found

def model_tools(plugins: dict[str, Plugin]) -> list[dict]:
    return [{"type": "function", "function": {"name": p.name, "description": p.description, "parameters": p.input_schema}} for p in plugins.values()]
