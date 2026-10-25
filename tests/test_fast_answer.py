"""Fast local answers stay tool-free and fall back safely."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from primus.fast_answer import answer, route, strip_prefix
from primus.config import ConfigError, load_config
from primus.provider import OllamaProvider, ProviderError
from primus.service import TaskRunner
from primus.storage import Store


class StandardModel:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        return {"role": "assistant", "content": "Standard model answer."}


class FastAnswerTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "temp").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "logs").mkdir()
        self.store = Store(self.root)
        self.config = {"_root": self.root, "memory": {"task_context_enabled": False},
                       "max_steps": 3, "max_invalid_responses": 1,
                       "fast_answer": {"enabled": True, "auto_simple": True,
                                       "name": "qwen3:1.7b", "endpoint": "http://127.0.0.1:11435/api/chat",
                                       "timeout_seconds": 20, "max_output_tokens": 128, "keep_alive": "10m"}}

    def test_only_static_general_questions_auto_route(self):
        self.assertEqual(route("What is two plus two?"), "What is two plus two?")
        self.assertEqual(route("Explain photosynthesis"), "Explain photosynthesis")
        self.assertEqual(route("Quick: tell me a joke"), "tell me a joke")
        for request in ("What is in my Downloads folder?", "What is the weather today?",
                        "What is the latest news?", "What time is it?", "What is on this screen?",
                        "Quick: open Chrome", "Quick: list reminders", "Quick: summarize emails",
                        "Team: list my outputs and reminders"):
            self.assertIsNone(route(request), request)
        self.assertEqual(strip_prefix("Quick: open Chrome"), "open Chrome")

    def test_quick_answer_uses_no_standard_model_and_redacts_question(self):
        standard = StandardModel()
        runner = TaskRunner(self.config, {}, self.store, provider=standard)
        with patch("primus.service.answer_fast", return_value="Four.") as fast:
            self.assertEqual(runner.run("What is two plus two?"), "Four.")
        self.assertEqual(standard.calls, 0)
        fast.assert_called_once_with(self.config, "What is two plus two?")
        self.assertEqual(runner.agent.history[-1]["content"], "Four.")
        self.assertEqual(self.store.list_tasks()[0]["status"], "done")
        audit = (self.root / "logs" / "audit.jsonl").read_text()
        self.assertIn('"tool":"fast_answer"', audit)
        self.assertNotIn("two plus two", audit)

    def test_unavailable_fast_model_falls_back_to_standard_agent(self):
        standard = StandardModel()
        runner = TaskRunner(self.config, {}, self.store, provider=standard)
        with patch("primus.service.answer_fast", side_effect=ProviderError("server unavailable")):
            self.assertEqual(runner.run("Quick: tell me a joke"), "Standard model answer.")
        self.assertEqual(standard.calls, 1)
        self.assertEqual(self.store.list_tasks()[0]["status"], "done")
        audit = [json.loads(line) for line in (self.root / "logs" / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(audit[0]["result"]["fallback"], "standard_model")

    def test_personal_question_stays_with_standard_agent(self):
        standard = StandardModel()
        runner = TaskRunner(self.config, {}, self.store, provider=standard)
        with patch("primus.service.answer_fast") as fast:
            self.assertEqual(runner.run("What is in my Downloads folder?"), "Standard model answer.")
        fast.assert_not_called()
        self.assertEqual(standard.calls, 1)

    def test_fast_provider_disables_thinking_and_advertises_no_tools(self):
        captured = []

        def open_request(request, timeout):
            captured.append(json.loads(request.data))
            return BytesIO(json.dumps({"message": {"role": "assistant", "content": "A loop repeats."}}).encode())

        with patch("primus.provider.urlopen", side_effect=open_request):
            self.assertEqual(answer(self.config, "Explain loops"), "A loop repeats.")
        self.assertEqual(captured[0]["model"], "qwen3:1.7b")
        self.assertEqual(captured[0]["tools"], [])
        self.assertIs(captured[0]["think"], False)
        self.assertEqual(captured[0]["options"]["num_predict"], 128)
        self.assertEqual(OllamaProvider({"model": self.config["fast_answer"]}, think=False).endpoint,
                         "http://127.0.0.1:11435/api/chat")

    def test_enabled_fast_endpoint_must_stay_on_private_local_server(self):
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        config["root"] = str(self.root)
        config["fast_answer"]["enabled"] = True
        config["fast_answer"]["endpoint"] = "https://example.com/api/chat"
        path = self.root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_kill_switch_blocks_fast_model_request(self):
        (self.root / "data" / "STOP").write_text("stop")
        model = StandardModel()
        with self.assertRaisesRegex(RuntimeError, "kill switch"):
            answer(self.config, "What is two plus two?", provider=model)
        self.assertEqual(model.calls, 0)


if __name__ == "__main__":
    unittest.main()
