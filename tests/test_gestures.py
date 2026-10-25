"""Hand geometry, temporal filtering, model integrity, and camera lifetime."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "vendor"))
sys.path.insert(0, str(PROJECT / "src"))

import numpy as np
from primus.audit import redact
from primus.config import ConfigError, load_config
from primus.gestures import HandRecognizer, StableGesture, camera_frames, classify_landmarks, model_paths


def example_landmarks(extended):
    points = np.zeros((21, 3), dtype=float)
    points[1:5] = [[-.4, -.15, 0], [-.75, -.4, 0], [-1, -.5, 0], [-1.25, -.6, 0]]
    for index, base in enumerate((5, 9, 13, 17)):
        x = -.6 + index * .4
        points[base] = [x, -.8, 0]
        points[base + 1] = [x, -1.2, 0]
        points[base + 2] = [x, -1.5, 0] if extended[index] else [x + .1, -.9, 0]
        points[base + 3] = [x, -1.8, 0] if extended[index] else [x + .1, -.7, 0]
    return points


class GestureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("data", "logs"):
            (self.root / name).mkdir()
        self.config = {"_root": self.root, "gestures": {"enabled": True, "camera_index": 0, "max_fps": 12}}

    def test_static_geometry_and_rotations(self):
        for extended, label in (([1, 1, 1, 1], "open_palm"), ([1, 1, 0, 0], "victory"), ([1, 0, 0, 0], "pointing")):
            points = example_landmarks(extended)
            self.assertEqual(classify_landmarks(points, points), label)
            rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
            self.assertEqual(classify_landmarks(points @ rotation, points @ rotation), label)
        self.assertEqual(classify_landmarks(np.zeros((21, 3)), np.zeros((21, 3))), "unknown")
        self.assertEqual(classify_landmarks(np.full((21, 3), float("nan")), np.zeros((21, 3))), "unknown")

    def test_stability_rejects_flicker_and_clears_on_hand_loss(self):
        stable = StableGesture(600)
        self.assertIsNone(stable.update("open_palm", 0))
        self.assertIsNone(stable.update("victory", 300))
        for stamp in (400, 600, 800):
            self.assertIsNone(stable.update("open_palm", stamp))
        self.assertEqual(stable.update("open_palm", 1000), "open_palm")
        self.assertIsNone(stable.update("no_hand", 1100))
        self.assertIsNone(stable.update("open_palm", 1200))
        self.assertIsNone(stable.update("open_palm", 2500))

    def test_camera_release_on_stop_failure_and_kill_switch(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("Optional OpenCV is not installed")
        camera = MagicMock()
        camera.isOpened.return_value = True
        camera.read.return_value = (True, np.zeros((40, 40, 3), dtype=np.uint8))
        engine = MagicMock()
        engine.recognize.return_value = []
        stop = threading.Event()
        with patch("primus.gestures.HandRecognizer", return_value=engine), patch.object(cv2, "VideoCapture", return_value=camera):
            stream = camera_frames(self.config, stop)
            self.assertEqual(next(stream)[1], [])
            stop.set()
            with self.assertRaises(StopIteration):
                next(stream)
        camera.release.assert_called_once()
        camera.reset_mock()
        camera.read.return_value = (False, None)
        with patch("primus.gestures.HandRecognizer", return_value=engine), patch.object(cv2, "VideoCapture", return_value=camera):
            with self.assertRaisesRegex(RuntimeError, "frame unavailable"):
                next(camera_frames(self.config, threading.Event()))
        camera.release.assert_called_once()
        (self.root / "data" / "STOP").write_text("stop")
        with patch("primus.gestures.HandRecognizer", return_value=engine), patch.object(cv2, "VideoCapture") as open_camera:
            self.assertEqual(list(camera_frames(self.config, threading.Event())), [])
            open_camera.assert_not_called()

    def test_integrity_config_and_private_results(self):
        with self.assertRaisesRegex(RuntimeError, "missing"):
            model_paths(self.config)
        settings = json.loads((PROJECT / "config.json").read_text(encoding="utf-8"))
        settings["root"] = str(self.root)
        settings["gestures"] = {"enabled": True, "camera_index": "https://camera.example"}
        file = self.root / "config.json"
        file.write_text(json.dumps(settings), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(file)
        payload = json.dumps(redact({"hands": [{"gesture": "private label"}], "landmarks": "private coordinates"}))
        self.assertNotIn("private", payload)

    def test_installed_models_on_reference_photos(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("Optional OpenCV is not installed")
        config = load_config()
        samples = PROJECT / "outputs" / "image-demos"
        if not config.get("gestures", {}).get("enabled") or not all((samples / (n + ".jpg")).is_file() for n in ("thumb_up", "pointing_up", "victory")):
            self.skipTest("Optional reference photos and gesture setup unavailable")
        engine = HandRecognizer(config)
        for name, label in (("thumb_up", "thumb_up"), ("pointing_up", "pointing"), ("victory", "victory")):
            hands = engine.recognize(cv2.imread(str(samples / (name + ".jpg"))))
            self.assertEqual([h["gesture"] for h in hands], [label])


if __name__ == "__main__":
    unittest.main()
