"""Browser opt-in and path scope without making network requests."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from primus.browser import BrowserError, _safe_url, _selector
from primus.config import load_config
from primus.plugins import discover
from primus.security import SecurityError, guard_path


class BrowserScopeTests(unittest.TestCase):
    def test_browser_opt_in_does_not_enable_other_network_plugins(self):
        names = discover(ROOT, network_enabled=False, enabled_network_plugins={
            "browser_open", "browser_navigate", "browser_read", "browser_click", "browser_type"})
        self.assertIn("browser_navigate", names)
        self.assertNotIn("web_search", names)
        self.assertNotIn("gmail_today", names)
        self.assertEqual(names["browser_navigate"].tier, "EXTERNAL")
        self.assertTrue(names["browser_navigate"].network)

    def test_approved_folders_and_profile_block(self):
        config = load_config(ROOT / "config.json")
        home = Path.home()
        for folder in ("Downloads", "Documents", "Desktop"):
            self.assertEqual(guard_path(str(home / folder / "example.txt"), config),
                             home / folder / "example.txt")
        with self.assertRaises(SecurityError):
            guard_path(str(home / ".ssh" / "id_rsa"), config)
        with self.assertRaises(SecurityError):
            guard_path(str(home / "AppData" / "Local" / "Google" / "Chrome" / "User Data"), config)
        with self.assertRaises(SecurityError):
            guard_path(str(ROOT / "data" / "browser-automation" / "Default" / "Cookies"), config)

    def test_navigation_rejects_unsafe_urls(self):
        self.assertEqual(_safe_url("https://example.org/path"), "https://example.org/path")
        for url in ("http://example.org", "file:///C:/Windows", "javascript:alert(1)",
                    "https://user:password@example.org"):
            with self.assertRaises(BrowserError):
                _safe_url(url)
        with self.assertRaises(BrowserError):
            _selector("body")


if __name__ == "__main__":
    unittest.main()
