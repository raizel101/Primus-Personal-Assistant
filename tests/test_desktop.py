"""Desktop actions and voice indicator tests without moving the real desktop."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from primus.agent import Agent
from primus.auth import local_session_id
from primus.desktop import app_command
from primus.fast_intents import recognize
from primus.plugins import discover
from primus.service import TaskRunner
from primus.storage import Store
from primus.voice import SpeechInput
from primus.voice import _console
from primus.voice_indicator import draw_orb, indicator_visible, voice_state


class ScriptedProvider:
    def __init__(self, *messages):
        self.messages = list(messages)
    def chat(self, messages, tools):
        return self.messages.pop(0)


def call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}


class FakeCanvas:
    def __init__(self):
        self.calls = []
    def delete(self, *args):
        self.calls.append("delete")
    def create_oval(self, *args, **kwargs):
        self.calls.append("oval")
    def create_line(self, *args, **kwargs):
        self.calls.append("line")
    def create_text(self, *args, **kwargs):
        self.calls.append(kwargs["text"])


class DesktopTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "temp").mkdir(exist_ok=True)
        self.workspace = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        (self.root / "logs").mkdir()
        (self.root / "data").mkdir()
        self.config = {"_root": self.root, "approved_folders": [], "max_steps": 4,
                       "max_invalid_responses": 2}
        self.plugins = discover(ROOT)

    def test_catalog_rejects_unknown_app_and_discovers_plugins(self):
        self.assertIsNone(app_command("arbitrary-command"))
        self.assertIn("app_open", self.plugins)
        self.assertIn("desktop_input", self.plugins)
        self.assertEqual(self.plugins["desktop_input"].tier, "EXTERNAL")

    def test_app_launch_reaches_agent_and_audit(self):
        plugin = self.plugins["app_open"]
        provider = ScriptedProvider(call("app_open", {"name": "chrome"}),
                                    {"role": "assistant", "content": "Chrome launch requested; check the window."})
        with patch.dict(plugin.run.__globals__, {"open_app": lambda config, name:
                        {"application": name, "process_running": True, "window_verified": True, "pid": 42}}):
            result = Agent(self.config, self.plugins, provider).run("Open Google Chrome")
        self.assertIn("Chrome launch requested", result)
        audit = (self.root / "logs" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertIn("app_open", audit)
        self.assertIn('"window_verified":true', audit)

    def test_voice_api_cannot_send_keyboard_input_without_consent(self):
        provider = ScriptedProvider(call("desktop_input", {"action": "type", "text": "hello"}),
                                    {"role": "assistant", "content": "Could not type."})
        result = Agent(self.config, self.plugins, provider, confirm=None).run("Type hello")
        self.assertIn("needs review", result)
        self.assertIn("Explicit interactive confirmation", result)

    def test_input_shape_rejected_before_prompt(self):
        provider = ScriptedProvider(call("desktop_input", {"action": "click", "x": 5}),
                                    {"role": "assistant", "content": "Click failed."})
        prompts = []
        Agent(self.config, self.plugins, provider, confirm=lambda prompt: prompts.append(prompt) or True).run("Click")
        self.assertEqual(prompts, [])

    def test_voice_status_and_projected_orb(self):
        path = self.root / "data" / "voice-status.json"
        path.write_text(json.dumps({"state": "processing", "pid": 1}), encoding="utf-8")
        self.assertEqual(voice_state(self.config), "processing")
        canvas = FakeCanvas()
        draw_orb(canvas, "processing", 1.2)
        self.assertIn("PROCESSING", canvas.calls)
        self.assertIn("line", canvas.calls)
        self.assertIn("oval", canvas.calls)
        for state in ("starting", "listening", "checking", "stopped"):
            self.assertFalse(indicator_visible(state))
        for state in ("heard", "processing", "speaking", "error"):
            self.assertTrue(indicator_visible(state))

    def test_fast_intent_is_exact_and_keeps_agent_context(self):
        self.assertEqual(recognize("Please open Google Chrome!"), ("app_open", {"name": "chrome"}))
        self.assertIsNone(recognize("open Google Chrome and send an email"))
        class NeverUseModel:
            def chat(self, *_):
                raise AssertionError("Exact app request should skip the model")
        plugin = self.plugins["app_open"]
        with patch.dict(plugin.run.__globals__, {"open_app": lambda config, name:
                        {"application": name, "window_verified": True, "pid": 42}}):
            runner = TaskRunner(self.config, self.plugins, Store(self.root), provider=NeverUseModel())
            result = runner.run("Open Google Chrome")
        self.assertEqual(result, "Opened Google Chrome.")
        self.assertEqual(runner.agent.history[-1]["content"], result)
        self.assertIn("app_open", (self.root / "logs" / "audit.jsonl").read_text(encoding="utf-8"))

    def test_speech_energy(self):
        self.assertEqual(SpeechInput.energy(b"\0\0" * 10), 0)
        self.assertEqual(SpeechInput.energy((1000).to_bytes(2, "little", signed=True) * 10), 1000)

    def test_voice_console_encoding_does_not_stop_listener(self):
        class NarrowOutput:
            encoding = "ascii"
            def __init__(self):
                self.output = ""
            def write(self, value):
                value.encode("ascii")
                self.output += value
            def flush(self):
                pass
        output = NarrowOutput()
        with patch("primus.voice.sys.stdout", output):
            _console("PRIMUS: café")
        self.assertIn("caf?", output.output)

    def test_voice_noise_calibration_preserves_initial_audio(self):
        class FakeStream:
            def start(self):
                pass
            def read(self, count):
                return (700).to_bytes(2, "little", signed=True) * count, False
            def stop(self):
                pass
            def close(self):
                pass
        class FakeSoundDevice:
            def RawInputStream(self, **kwargs):
                return FakeStream()
        speech = SpeechInput(FakeSoundDevice(), {"voice": {"energy_threshold": 500}})
        with speech:
            self.assertAlmostEqual(speech.noise_floor, 700)
            self.assertEqual(len(speech.prefetched), 4)

    def test_shared_local_session_is_stable_and_opaque(self):
        with patch("primus.auth.api_token", return_value="test-secret"):
            identifier = local_session_id()
            self.assertEqual(identifier, local_session_id())
            self.assertEqual(len(identifier), 32)
            self.assertNotIn("test-secret", identifier)


if __name__ == "__main__":
    unittest.main()
