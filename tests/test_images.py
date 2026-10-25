"""Approved local image input, transient model payload, and audit boundaries."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "vendor"))
sys.path.insert(0, str(PROJECT / "src"))

from PIL import Image, PngImagePlugin
from primus.image_input import _prepare, analyze_image, checked_region, image_source
from primus.image_service import run_image
from primus.plugins import discover
from primus.storage import Store
from primus.tool_router import select_plugins


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PROJECT / "temp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("data", "logs", "outputs"):
            (self.root / folder).mkdir()
        self.config = {"_root": self.root, "approved_folders": [],
                       "permissions": {"read": "auto"},
                       "vision": {"enabled": True, "model": "qwen3-vl:4b",
                                  "endpoint": "http://127.0.0.1:11435/api/chat"}}
        self.path = self.root / "outputs" / "scene.png"
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "PRIVATE SAMPLE METADATA")
        with Image.new("RGB", (200, 100), "red") as picture:
            picture.save(self.path, pnginfo=metadata)

    def test_plugin_is_local_read_and_selected_for_picture(self):
        plugins = discover(PROJECT, network_enabled=False)
        self.assertIn("image_analyze", plugins)
        self.assertEqual(plugins["image_analyze"].tier, "READ")
        self.assertFalse(plugins["image_analyze"].network)
        self.assertEqual(set(select_plugins("Describe this photo", plugins, [])), {"image_analyze"})

    def test_preparation_strips_metadata_and_limits_dimensions(self):
        prepared, digest = _prepare(self.path)
        self.assertEqual(len(digest), 64)
        self.assertNotIn(b"PRIVATE SAMPLE METADATA", prepared)
        with Image.open(BytesIO(prepared)) as picture:
            self.assertEqual(picture.format, "JPEG")
            self.assertEqual(picture.size, (200, 100))

    def test_disallowed_paths_and_invalid_images_fail_before_model(self):
        with self.assertRaises(PermissionError):
            image_source(self.config, str(PROJECT / "README.md"))
        wrong = self.root / "outputs" / "not-image.png"
        wrong.write_text("not an image", encoding="utf-8")
        with patch("primus.image_input._model_ready"):
            with self.assertRaisesRegex(ValueError, "decoded"):
                analyze_image(self.config, {"path": str(wrong)})
        with self.assertRaisesRegex(ValueError, "existing JPEG"):
            image_source(self.config, str(self.root / "outputs" / "missing.gif"))

    def test_kill_switch_prevents_model_call(self):
        (self.root / "data" / "STOP").write_text("stop", encoding="utf-8")
        with patch("primus.image_input._model_ready") as model:
            with self.assertRaisesRegex(RuntimeError, "kill switch"):
                analyze_image(self.config, {"path": str(self.path)})
            model.assert_not_called()

    def test_direct_request_audited_without_image_or_description(self):
        plugins = discover(PROJECT, network_enabled=False)
        store = Store(self.root)
        with patch("primus.image_input._model_ready"), \
             patch("primus.image_input._ask_local", return_value="A red scene.") as model:
            result = run_image(self.config, plugins, store,
                               {"path": str(self.path), "question": "What is shown?"})
        self.assertEqual(result["observation"], "A red scene.")
        self.assertIn("User question: What is shown?", model.call_args.args[2])
        audit = (self.root / "logs" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("A red scene.", audit)
        self.assertNotIn("PRIVATE SAMPLE METADATA", audit)
        self.assertNotIn("What is shown?", audit)
        self.assertEqual(json.loads(audit)["tool"], "image_analyze")
        self.assertEqual(store.list_tasks()[0]["status"], "done")

    def test_region_bounds_and_transparency(self):
        for invalid in ([0, 0, 0, 1], [-1, 0, 1, 1], [0, 0, 1, float("nan")], [0, 1], [False, 0, 1, 1]):
            with self.assertRaises(ValueError):
                checked_region(invalid)
        with Image.new("RGBA", (800, 600), (0, 0, 0, 0)) as transparent:
            transparent.save(self.path)
        prepared, _ = _prepare(self.path, "detail", [0.25, 0.25, 0.75, 0.75])
        with Image.open(BytesIO(prepared)) as image:
            self.assertEqual(image.size, (400, 300))
            self.assertGreater(min(image.getpixel((100, 100))), 250)
        with self.assertRaisesRegex(ValueError, "32 by 32"):
            _prepare(self.path, region=[0, 0, 0.001, 0.001])

    def test_text_mode_prompts_uncertainty_and_gesture_mode_skips_llm(self):
        with patch("primus.image_input._model_ready"), patch("primus.image_input._ask_local", return_value="[unclear]") as model:
            result = analyze_image(self.config, {"path": str(self.path), "mode": "text"})
        self.assertIn("Do not guess", model.call_args.args[2])
        self.assertTrue(result["image_info"]["notes"])
        with patch("primus.image_input._model_ready") as llm, \
             patch("primus.gestures.recognize_image", return_value={"observation": "A hand", "hands": []}):
            self.assertEqual(analyze_image(self.config, {"path": str(self.path), "mode": "gestures"})["observation"], "A hand")
        llm.assert_not_called()


if __name__ == "__main__":
    unittest.main()
