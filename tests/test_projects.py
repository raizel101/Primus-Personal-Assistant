"""Project snapshot integrity, imports, data paths, sandbox boundaries and approvals."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "vendor")]
from primus.plugins import discover
from primus.script_runner import project_manifest, _sandbox_command


class ProjectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT / "temp")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.config = {"_root": self.root, "approved_folders": [], "script_execution": {"enabled": True}}
        self.plugin = discover(ROOT)["script_run"]
        self.main = self.project / "main.py"
        self.main.write_text("from helper import value\nprint(value)\n")
        (self.project / "helper.py").write_text("value=42\n")

    def args(self):
        args = {"path": str(self.main), "project_root": str(self.project)}
        description = self.plugin.summary(self.config, args)
        self.assertIn("all project files and SHA-256", description)
        self.assertIn("helper.py", description)
        return args

    def test_modified_added_or_removed_helper_invalidates_approval_before_process(self):
        for action in ("change", "add", "remove"):
            with self.subTest(action=action):
                helper = self.project / "helper.py"
                helper.write_text("value=42\n")
                args = self.args()
                extra = self.project / "extra.py"
                if action == "change": helper.write_text("value=99\n")
                if action == "add": extra.write_text("value=7\n")
                if action == "remove": helper.unlink()
                with patch("primus.script_runner.subprocess.Popen") as process:
                    with self.assertRaisesRegex(ValueError, "changed after approval"):
                        self.plugin.run(self.config, args)
                    process.assert_not_called()
                if extra.exists(): extra.unlink()

    def test_changed_helper_during_staging_is_rejected(self):
        args = self.args()
        import shutil
        original = shutil.copyfileobj
        def change_after_copy(reader, writer):
            original(reader, writer)
            if Path(reader.name).name == "helper.py":
                (self.project / "helper.py").write_text("value=99\n")
        with patch("primus.script_runner.shutil.copyfileobj", side_effect=change_after_copy), patch("primus.script_runner.subprocess.Popen") as process:
            with self.assertRaisesRegex(RuntimeError, "changed during staging"):
                self.plugin.run(self.config, args)
            process.assert_not_called()

    def test_project_is_readonly_network_isolated_and_package_aware(self):
        with patch("primus.script_runner.sys.platform", "linux"), patch("primus.script_runner.shutil.which", return_value="/usr/bin/bwrap"):
            argv = _sandbox_command({}, Path("/staged/main.py"), Path("/out"), [], [],
                                    project={"root": Path("/staged/project"), "entrypoint": "app/main.py"})
        self.assertIn("--unshare-net", argv)
        self.assertIn("--unshare-pid", argv)
        self.assertIn("--ro-bind", argv)
        self.assertIn("PRIMUS_OUTPUT_DIR", argv)
        self.assertIn('runpy.run_module("app.main"', argv[-1])
        self.assertEqual(argv[argv.index("--chdir")+1], "/work/project")
        self.assertNotIn(str(self.project), argv)

    def test_real_staging_and_capture_support_package_imports_and_relative_data(self):
        app = self.project / "app"
        app.mkdir()
        (app / "__init__.py").write_text("")
        self.main = app / "main.py"
        self.main.write_text("from .helper import answer\nprint(answer())\n")
        (app / "helper.py").write_text("import json\ndef answer():\n    return json.load(open('data/values.json'))['answer']\n")
        (self.project / "data").mkdir()
        (self.project / "data/values.json").write_text('{"answer":42}')
        args = self.args()
        def fixture_command(config, staged, output, arguments, inputs, project):
            # Test only: fixed harmless fixtures, no production host fallback.
            with patch("primus.script_runner.sys.platform", "linux"), patch("primus.script_runner.shutil.which", return_value="/usr/bin/bwrap"):
                code = _sandbox_command(config, staged, output, arguments, inputs, project=project)[-1]
            local = str(project["root"]).replace("\\", "/")
            code = "import os;os.chdir(" + json.dumps(local) + ");" + code.replace("/work/project", local)
            return [sys.executable, "-I", "-B", "-c", code]
        with patch("primus.script_runner._sandbox_command", side_effect=fixture_command):
            result = self.plugin.run(self.config, args)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["stdout"].splitlines(), ["42"])
        self.assertEqual(result["project_files"], args["_project_manifest"])

    def test_credentials_outside_entry_hardlinks_and_file_limit_are_blocked(self):
        with self.assertRaises(PermissionError):
            project_manifest(self.config, str(self.project), self.root / "outside.py")
        secret = self.project / ".env"
        secret.write_text("fixture only")
        with self.assertRaises(PermissionError): self.args()
        secret.unlink()
        original = self.root / "outside.txt"
        original.write_text("fixture")
        linked = self.project / "linked.txt"
        os.link(original, linked)
        with self.assertRaisesRegex(PermissionError, "hard links"): self.args()
        linked.unlink()
        for i in range(11): (self.project / f"extra{i}.txt").write_text("fixture")
        with self.assertRaisesRegex(ValueError, "at most 12"): self.args()


if __name__ == "__main__":
    unittest.main()
