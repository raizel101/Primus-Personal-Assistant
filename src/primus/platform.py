"""Thin platform adapters for shell inspection and local notifications."""
from __future__ import annotations
import subprocess
import sys
import threading

class BaseAdapter:
    def shell_argv(self, command: str) -> list[str]:
        raise NotImplementedError
    def notify(self, title: str, message: str) -> None:
        raise NotImplementedError

class WindowsAdapter(BaseAdapter):
    def shell_argv(self, command: str) -> list[str]:
        if command == "pwd":
            return ["cmd.exe", "/d", "/c", "cd"]
        if command == "list":
            return ["cmd.exe", "/d", "/c", "dir", "/b"]
        return [sys.executable, "--version"]
    def notify(self, title: str, message: str) -> None:
        import ctypes
        threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(None, message, title, 0x40), daemon=True).start()

class MacAdapter(BaseAdapter):
    def shell_argv(self, command: str) -> list[str]:
        if command == "pwd":
            return ["/bin/pwd"]
        if command == "list":
            return ["/bin/ls", "-1"]
        return [sys.executable, "--version"]
    def notify(self, title: str, message: str) -> None:
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display notification "{safe_message}" with title "{safe_title}"'],
                       timeout=10, check=True)

class LinuxAdapter(BaseAdapter):
    def shell_argv(self, command: str) -> list[str]:
        if command == "pwd":
            return ["/bin/pwd"]
        if command == "list":
            return ["/bin/ls", "-1"]
        return [sys.executable, "--version"]
    def notify(self, title: str, message: str) -> None:
        subprocess.run(["notify-send", title, message], timeout=10, check=True)

def adapter() -> BaseAdapter:
    if sys.platform == "win32":
        return WindowsAdapter()
    if sys.platform == "darwin":
        return MacAdapter()
    if sys.platform.startswith("linux"):
        return LinuxAdapter()
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
