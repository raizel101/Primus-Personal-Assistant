"""Always-on startup behavior without changing OS startup settings."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from primus.config import ConfigError, load_config
from primus.process_lock import ProcessLock
from primus.tray import run_tray
from primus.voice import run_voice, write_voice_status
from primus.voice_indicator import indicator_visible


class AlwaysOnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        (self.root / "temp").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_only_one_voice_process_can_hold_lock(self):
        path = self.root / "data" / "voice.lock"
        with ProcessLock(path):
            with self.assertRaises(RuntimeError):
                with ProcessLock(path):
                    pass
        with ProcessLock(path):
            pass

    def test_voice_status_contains_no_transcript(self):
        config = {"_root": self.root}
        write_voice_status(config, "listening")
        data = json.loads((self.root / "data" / "voice-status.json").read_text(encoding="utf-8"))
        self.assertEqual(data["state"], "listening")
        self.assertEqual(set(data), {"state", "pid", "updated_at"})
        write_voice_status(config, "processing", stt_device="cuda")
        data = json.loads((self.root / "data" / "voice-status.json").read_text(encoding="utf-8"))
        self.assertEqual(data["stt_device"], "cuda")
        self.assertEqual(set(data), {"state", "pid", "updated_at", "stt_device"})

    def test_manual_orb_open_shows_listening_without_fake_wake_state(self):
        self.assertFalse(indicator_visible("listening"))
        self.assertTrue(indicator_visible("listening", manual=True))
        self.assertTrue(indicator_visible("processing"))

    def test_voice_status_retries_when_indicator_reads_file(self):
        original_replace = os.replace
        attempts = 0
        def occasionally_blocked(source, target):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError("status file temporarily open")
            original_replace(source, target)
        with patch("primus.voice.os.replace", side_effect=occasionally_blocked), \
             patch("primus.voice.time.sleep") as sleep:
            write_voice_status({"_root": self.root}, "heard")
        self.assertEqual(attempts, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(json.loads((self.root / "data" / "voice-status.json").read_text())["state"], "heard")

    def test_voice_status_reports_clean_stop_and_failure(self):
        config = {"_root": self.root}
        with patch("primus.voice._run_voice"):
            run_voice(config)
        status_file = self.root / "data" / "voice-status.json"
        self.assertEqual(json.loads(status_file.read_text(encoding="utf-8"))["state"], "stopped")
        with patch("primus.voice._run_voice", side_effect=RuntimeError("private transcript")):
            with self.assertRaises(RuntimeError):
                run_voice(config)
        status = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["error_type"], "RuntimeError")
        self.assertNotIn("private transcript", status_file.read_text(encoding="utf-8"))

    def test_invalid_auto_start_setting_is_rejected(self):
        config = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))
        config["root"] = str(self.root)
        config["tray"]["auto_start_voice"] = "yes"
        path = self.root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_tray_starts_and_stops_voice_when_configured(self):
        child = Mock()
        child.poll.return_value = None
        listener = Mock()
        icon = Mock()
        fake_pystray = SimpleNamespace(Icon=Mock(return_value=icon), Menu=Mock(), MenuItem=Mock())
        fake_pynput = SimpleNamespace(keyboard=SimpleNamespace(GlobalHotKeys=Mock(return_value=listener)))
        config = {"_root": self.root, "tray": {"hotkey": "<ctrl>+<alt>+p", "auto_start_voice": True}}
        with patch.dict(sys.modules, {"pystray": fake_pystray, "pynput": fake_pynput}), \
             patch("primus.tray._start_local_core", return_value=(None, None, None)), \
             patch("primus.tray._image", return_value=object()), \
             patch("primus.tray.subprocess.Popen", return_value=child) as launch:
            run_tray(config)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(launch.call_args.args[0][-1], "voice")
        self.assertEqual(launch.call_args.kwargs["cwd"], self.root)
        child.terminate.assert_called_once()
        listener.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
