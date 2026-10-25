"""Voice routing and local API conversation tests without microphone or downloads."""
from __future__ import annotations
import json
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from primus.api import make_server
from primus.voice import LocalSpeech, confirmed_wake, inside_root, wake_command


class PhaseThreeTests(unittest.TestCase):
    def test_observed_premus_spelling_requires_direct_greeting_and_two_passes(self):
        for phrase in ('Hey, Premus.', 'hey premus', 'Okay, Premus, open YouTube'):
            self.assertTrue(wake_command(phrase)[0])
        for phrase in ('Premus', 'A premus stove', 'open Premus', 'Hey promise', 'Hey premise', 'Hey primers'):
            self.assertEqual(wake_command(phrase),(False,''))
        speech=Mock()
        speech.transcribe.side_effect=['Hey, Premus.','Hey, Premus.']
        self.assertEqual(confirmed_wake(speech,b'audio'),(True,''))
        speech.transcribe.side_effect=['Hey, Premus.','Hey promise']
        self.assertEqual(confirmed_wake(speech,b'audio'),(False,''))

    def test_wake_phrase_and_same_turn_command(self):
        self.assertEqual(wake_command("Primus, list my files"), (True, "list my files"))
        self.assertEqual(wake_command("Hey PRIMUS!"), (True, ""))
        self.assertEqual(wake_command("Prime us, list my files"), (False, ""))
        self.assertEqual(wake_command("Pronus, list my files"), (False, ""))
        self.assertEqual(wake_command("Open Primus"), (False, ""))
        self.assertEqual(wake_command("open Google Chrome"), (False, ""))
        self.assertEqual(wake_command("A primus stove"), (False, ""))
        self.assertEqual(wake_command("Just listening"), (False, ""))

    def test_wake_requires_two_decodes_before_dispatch(self):
        speech = Mock()
        speech.transcribe.side_effect = ["open Chrome"]
        self.assertEqual(confirmed_wake(speech, b"audio"), (False, ""))
        self.assertEqual(speech.transcribe.call_count, 1)

        speech.transcribe.reset_mock(side_effect=True)
        speech.transcribe.side_effect = ["Primus, open Chrome", "open Chrome"]
        self.assertEqual(confirmed_wake(speech, b"audio"), (False, ""))

        speech.transcribe.reset_mock(side_effect=True)
        speech.transcribe.side_effect = ["Primus", "Primus, open Chrome"]
        self.assertEqual(confirmed_wake(speech, b"audio"), (True, ""))

        speech.transcribe.reset_mock(side_effect=True)
        speech.transcribe.side_effect = ["Hey Primus, open Chrome", "Primus, open Chrome"]
        self.assertEqual(confirmed_wake(speech, b"audio"), (True, "open Chrome"))
        self.assertEqual([call.kwargs["beam_size"] for call in speech.transcribe.call_args_list], [1, 5])

    def test_wake_transcription_is_not_primed_with_wake_word(self):
        class FakeArray:
            def astype(self, _dtype):
                return self
            def __truediv__(self, _number):
                return self

        fake_numpy = SimpleNamespace(int16="int16", float32="float32",
                                     frombuffer=Mock(return_value=FakeArray()))
        speech = LocalSpeech.__new__(LocalSpeech)
        speech.stt = Mock()
        speech.stt.transcribe.return_value = ([SimpleNamespace(text="open Chrome")], None)
        with patch.dict(sys.modules, {"numpy": fake_numpy}):
            self.assertEqual(speech.transcribe(b"audio", beam_size=5), "open Chrome")
        self.assertNotIn("initial_prompt", speech.stt.transcribe.call_args.kwargs)
        self.assertEqual(speech.stt.transcribe.call_args.kwargs["beam_size"], 5)

    def test_voice_model_paths_cannot_escape_root(self):
        root = PROJECT.resolve()
        self.assertEqual(inside_root(root, "models/stt"), root / "models" / "stt")
        with self.assertRaises(ValueError):
            inside_root(root, "../outside")
        with self.assertRaises(ValueError):
            inside_root(root, str(root.parent / "outside"))

    def test_api_reuses_session_runner_and_denies_invalid_session(self):
        class FakeRunner:
            created = 0
            confirmations = []
            def __init__(self, *_args, **_kwargs):
                FakeRunner.created += 1
                FakeRunner.confirmations.append(_kwargs.get("confirm"))
                self.turns = []
            def run(self, task):
                self.turns.append(task)
                return f"turn {len(self.turns)}"

        with tempfile.TemporaryDirectory(dir=PROJECT / "temp") as path:
            root = Path(path)
            (root / "plugins").mkdir()
            (root / "logs").mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            config = {"_root": root, "network_plugins_enabled": False,
                      "local_api": {"host": "127.0.0.1", "port": port},
                      "scheduler": {"poll_seconds": 1}}
            with patch("primus.api.api_token", return_value="test-token"), patch("primus.api.TaskRunner", FakeRunner):
                server, _ = make_server(config)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    def post(session):
                        body = json.dumps({"task": "hello", "session": session}).encode()
                        request = Request(f"http://127.0.0.1:{port}/tasks", data=body,
                                          headers={"Authorization": "Bearer test-token",
                                                   "Content-Type": "application/json"}, method="POST")
                        with urlopen(request, timeout=2) as response:
                            return json.load(response)["result"]
                    session = "a" * 32
                    self.assertEqual(post(session), "turn 1")
                    self.assertEqual(post(session), "turn 2")
                    self.assertEqual(FakeRunner.created, 1)
                    self.assertTrue(callable(FakeRunner.confirmations[0]))
                    with self.assertRaises(HTTPError) as invalid:
                        post("../bad")
                    self.assertEqual(invalid.exception.code, 400)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
