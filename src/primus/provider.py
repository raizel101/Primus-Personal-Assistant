"""Local Ollama model adapter; no cloud connection in Phase 1."""
from __future__ import annotations
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Protocol

class ModelProvider(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> dict: ...

class ProviderError(RuntimeError):
    pass

class OllamaProvider:
    def __init__(self, config: dict, *, think: bool | None = None, attempts: int = 3,
                 options: dict | None = None):
        self.model = config["model"]["name"]
        self.endpoint = config["model"]["endpoint"]
        self.timeout = int(config["model"].get("timeout_seconds", 120))
        self.keep_alive = config["model"].get("keep_alive", "30m")
        self.think = think
        self.attempts = attempts
        self.options = options

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        data = {"model": self.model, "messages": messages, "tools": tools,
                "stream": False, "keep_alive": self.keep_alive}
        if self.think is not None:
            data["think"] = self.think
        if self.options:
            data["options"] = self.options
        payload = json.dumps(data).encode("utf-8")
        request = Request(self.endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        last_error = None
        for attempt in range(self.attempts):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ProviderError("Ollama response exceeded 2 MB")
                parsed = json.loads(body)
                message = parsed.get("message")
                if not isinstance(message, dict) or message.get("role") != "assistant":
                    raise ProviderError("Ollama returned an invalid message")
                return message
            except HTTPError as exc:
                last_error = exc
                if exc.code < 500:
                    break
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < self.attempts - 1:
                time.sleep(0.4 * (attempt + 1))
        raise ProviderError(f"Local Ollama request failed: {last_error}")
