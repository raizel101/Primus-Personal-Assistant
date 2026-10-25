"""Script permission and sandbox command tests."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from primus.agent import Agent
from primus.plugins import discover
from primus.script_runner import _sandbox_command, script_source


class Provider:
    def __init__(self, *messages):
        self.messages = list(messages)
    def chat(self, messages, tools):
        return self.messages.pop(0)


class ScriptTests(unittest.TestCase):
    def test_script_needs_exact_consent_and_rejects_executable(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder:
            root = Path(folder)
            (root / "logs").mkdir()
            (root / "data").mkdir()
            script = root / "example.py"
            script.write_text("print('hello')\n", encoding="utf-8")
            config = {"_root": root, "approved_folders": [], "script_execution": {"enabled": True},
                      "max_steps": 3, "max_invalid_responses": 2}
            plugins = discover(ROOT)
            self.assertEqual(plugins["script_run"].tier, "EXTERNAL")
            self.assertRaises(ValueError, script_source, config, str(root / "example.exe"))
            call = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "script_run", "arguments": {"path": str(script)}}}]}
            result = Agent(config, plugins, Provider(call, {"role": "assistant", "content": "Denied."}),
                           confirm=None).run("Run my script")
            self.assertIn("Explicit interactive confirmation", result)
            self.assertFalse((root / "outputs").exists())

    def test_sandbox_command_has_no_network_or_shell_interpolation(self):
        with patch("primus.script_runner.sys.platform", "linux"), \
             patch("primus.script_runner.shutil.which", return_value="/usr/bin/bwrap"):
            argv = _sandbox_command({}, Path("/approved/a.py"), Path("/work/out"),
                                    ["hello; rm -rf /"], [Path("/approved/data.txt")])
        self.assertIn("--unshare-net", argv)
        self.assertIn("--unshare-user", argv)
        self.assertIn("--ro-bind", argv)
        self.assertIn("/work/inputs/0", argv)
        self.assertEqual(argv[-1], "hello; rm -rf /")
        self.assertNotIn("/bin/sh", argv)

    def test_changed_script_is_rejected_after_consent_preview(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder:
            root = Path(folder)
            script = root / "approved.py"
            script.write_text("print('first')\n", encoding="utf-8")
            config = {"_root": root, "approved_folders": [],
                      "script_execution": {"enabled": True, "wsl_distro": "Ubuntu"}}
            plugin = discover(ROOT)["script_run"]
            arguments = {"path": str(script)}
            prompt = plugin.summary(config, arguments)
            self.assertIn("SHA-256", prompt)
            script.write_text("print('changed')\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed after approval"):
                plugin.run(config, arguments)
            self.assertFalse((root / "temp" / "script-staging" / arguments["_run_id"]).exists())
            self.assertFalse((root / "outputs" / "script-runs" / arguments["_run_id"]).exists())

    def test_captured_output_is_bounded_and_reports_truncation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder:
            root = Path(folder)
            script = root / "capture.py"
            script.write_text("import sys\nprint('x'*4500)\nsys.stderr.write('problem\\n')\nraise SystemExit(7)\n")
            config = {"_root": root, "approved_folders": [], "script_execution": {"enabled": True}}
            plugin = discover(ROOT)["script_run"]
            arguments = {"path": str(script)}
            plugin.summary(config, arguments)
            # Test capture/staging with a fixed harmless fixture; production has no host fallback.
            with patch("primus.script_runner._sandbox_command", side_effect=lambda _c, staged, _o, _a, _i: [sys.executable, "-I", "-S", "-B", str(staged)]):
                result = plugin.run(config, arguments)
            self.assertEqual(result["exit_code"], 7)
            self.assertEqual(result["stdout"], "x"*4000)
            self.assertEqual(result["stderr"].splitlines(), ["problem"])
            self.assertTrue(result["stdout_truncated"])
            self.assertFalse(result["stderr_truncated"])
            self.assertFalse(result["verified"])

    def test_unavailable_sandbox_never_falls_back_to_host_python(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder:
            root = Path(folder)
            script = root / "capture.py"
            script.write_text("print('must not execute')")
            config = {"_root": root, "approved_folders": [], "script_execution": {"enabled": True}}
            plugin = discover(ROOT)["script_run"]
            arguments = {"path": str(script)}
            plugin.summary(config, arguments)
            with patch("primus.script_runner._sandbox_command", side_effect=RuntimeError("Sandbox unavailable")), patch("primus.script_runner.subprocess.Popen") as process:
                with self.assertRaisesRegex(RuntimeError, "Sandbox unavailable"):
                    plugin.run(config, arguments)
                process.assert_not_called()
            self.assertFalse((root / "outputs/script-runs" / arguments["_run_id"]).exists())


if __name__ == "__main__":
    unittest.main()
