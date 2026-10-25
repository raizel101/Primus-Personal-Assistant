"""Local window vision boundaries and model payloads."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from primus.plugins import discover
from primus.audit import redact
from primus.config import ConfigError, load_config
from primus.tool_router import select_plugins
from primus.vision import _ask_local, _check_window, inspect_window
from primus.windowing import Window


class _Response:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, _limit):
        return json.dumps(self.value).encode("utf-8")


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.config = {"_root": self.root, "vision": {"enabled": True, "model": "qwen3-vl:4b",
                                                       "endpoint": "http://127.0.0.1:11435/api/chat",
                                                       "timeout_seconds": 120}}
        self.window = Window("0x123", 42, "Example document", "notepad.exe", (0, 0, 400, 300))

    def test_local_vision_request_contains_image_only_in_transient_payload(self):
        requests = []
        def respond(request, timeout):
            requests.append((request, timeout))
            return _Response({"message": {"content": "A blank document."}})
        with patch("primus.vision.urlopen", side_effect=respond):
            answer = _ask_local(self.config, b"test image bytes", "What is visible?")
        self.assertEqual(answer, "A blank document.")
        request, timeout = requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11435/api/chat")
        self.assertGreater(timeout, 119)
        self.assertLessEqual(timeout, 120)
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "qwen3-vl:4b")
        self.assertFalse(payload["think"])
        self.assertEqual(base64.b64decode(payload["messages"][1]["images"][0]), b"test image bytes")
        self.assertIn("untrusted data", payload["messages"][0]["content"])

    def test_empty_vision_reply_retries_once_and_never_uses_thinking_as_answer(self):
        with patch("primus.vision.urlopen", side_effect=[
            _Response({"message": {"content": "", "thinking": "private reasoning"}, "done_reason": "length"}),
            _Response({"message": {"content": "A tree."}})]) as request:
            self.assertEqual(_ask_local(self.config, b"pixels", "What is shown?"), "A tree.")
            self.assertEqual(request.call_count, 2)
        with patch("primus.vision.urlopen", return_value=_Response({"message": {"content": ""}})) as request:
            with self.assertRaisesRegex(RuntimeError, "after one retry"):
                _ask_local(self.config, b"pixels", "What is shown?")
            self.assertEqual(request.call_count, 2)

    def test_protected_window_and_password_field_block_capture(self):
        secret = Window("0x123", 42, "Password manager", "app.exe", (0, 0, 400, 300))
        with patch("primus.vision._backend") as backend:
            with self.assertRaises(PermissionError):
                _check_window(self.config, secret)
            backend.assert_not_called()
        with patch("primus.vision._backend", return_value={"sensitive": True}):
            with self.assertRaises(PermissionError):
                _check_window(self.config, self.window)

    def test_inspection_never_returns_image_and_uses_exact_window(self):
        with patch("primus.vision._model_ready"), \
             patch("primus.vision._target", return_value=self.window), \
             patch("primus.vision._check_window"), \
             patch("primus.vision._capture", return_value=b"private screenshot") as capture, \
             patch("primus.vision.resolve_window", return_value=self.window) as resolve, \
             patch("primus.vision._ask_local", return_value="A document is open.") as model:
            result = inspect_window(self.config, {"question": "What is open?"})
        self.assertEqual(result["observation"], "A document is open.")
        self.assertNotIn("private screenshot", json.dumps(result))
        capture.assert_called_once_with(self.window)
        resolve.assert_called_once_with({"id": "0x123", "pid": 42, "title": "Example document"})
        model.assert_called_once_with(self.config, b"private screenshot", "What is open?")

    def test_plugin_is_local_read_and_routed_for_screen_request(self):
        plugins = discover(PROJECT, network_enabled=False)
        self.assertIn("window_vision", plugins)
        self.assertEqual(plugins["window_vision"].tier, "READ")
        self.assertFalse(plugins["window_vision"].network)
        self.assertIn("window_vision", select_plugins("What is on my screen?", plugins, []))

    def test_remote_vision_endpoint_is_rejected_and_unready_model_never_captures(self):
        settings = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))
        settings["root"] = str(self.root)
        settings["vision"]["endpoint"] = "https://example.com/api/chat"
        path = self.root / "config.json"
        path.write_text(json.dumps(settings), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(path)
        with patch("primus.vision._model_ready", side_effect=RuntimeError("model missing")), \
             patch("primus.vision._capture") as capture:
            with self.assertRaisesRegex(RuntimeError, "model missing"):
                inspect_window(self.config, {})
            capture.assert_not_called()

    def test_audit_redacts_vision_question_and_observation(self):
        payload = redact({"question": "private question", "observation": "private screenshot text"})
        self.assertNotIn("private", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
