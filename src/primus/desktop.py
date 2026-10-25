"""Small, fixed catalog of desktop applications; never execute model-provided paths."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


APP_NAMES = ("chrome", "edge", "firefox", "notepad", "calculator", "file_manager", "terminal")


def _first_file(*paths: Path) -> str | None:
    return next((str(path) for path in paths if path.is_file()), None)


def app_command(name: str) -> list[str] | None:
    """Return argv for a known installed app, with no shell interpolation."""
    if name not in APP_NAMES:
        return None
    if sys.platform == "win32":
        program = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        program32 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
        candidates = {
            "chrome": (program / "Google/Chrome/Application/chrome.exe", program32 / "Google/Chrome/Application/chrome.exe", local / "Google/Chrome/Application/chrome.exe"),
            "edge": (program32 / "Microsoft/Edge/Application/msedge.exe", program / "Microsoft/Edge/Application/msedge.exe"),
            "firefox": (program / "Mozilla Firefox/firefox.exe", program32 / "Mozilla Firefox/firefox.exe"),
            "notepad": (windows / "System32/notepad.exe",),
            "calculator": (windows / "System32/calc.exe",),
            "file_manager": (windows / "explorer.exe",),
            "terminal": (windows / "System32/cmd.exe",),
        }
        executable = _first_file(*candidates[name])
        return [executable] if executable else None
    if sys.platform == "darwin":
        apps = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "firefox": "Firefox",
                "notepad": "TextEdit", "calculator": "Calculator", "file_manager": "Finder", "terminal": "Terminal"}
        bundle = Path("/Applications") / f"{apps[name]}.app"
        if name == "file_manager":
            bundle = Path("/System/Library/CoreServices/Finder.app")
        if name in {"notepad", "calculator", "terminal"}:
            bundle = Path("/System/Applications") / f"{apps[name]}.app"
        return ["/usr/bin/open", "-a", str(bundle)] if bundle.exists() else None
    if sys.platform.startswith("linux"):
        options = {"chrome": ("google-chrome", "google-chrome-stable"),
                   "edge": ("microsoft-edge", "microsoft-edge-stable"),
                   "firefox": ("firefox",), "notepad": ("gedit", "kate", "mousepad"),
                   "calculator": ("gnome-calculator", "kcalc"),
                   "file_manager": ("nautilus", "dolphin", "thunar"),
                   "terminal": ("gnome-terminal", "konsole", "xfce4-terminal")}
        executable = next((shutil.which(item) for item in options[name] if shutil.which(item)), None)
        return [executable] if executable else None
    return None


def list_apps() -> list[dict]:
    return [{"name": name, "available": app_command(name) is not None} for name in APP_NAMES]


def _visible_window_for_pid(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    windows = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def inspect(hwnd, unused):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            windows.append(hwnd)
            return False
        return True
    user32.EnumWindows(inspect, 0)
    return bool(windows)


def _foreground_matches(executable: str, name: str) -> bool:
    """Handle browsers that hand a new launch to an existing window process."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    hwnd = user32.GetForegroundWindow()
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return False
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, len(title))
    if name == "calculator" and title.value.strip().lower() == "calculator":
        return True
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    handle = kernel32.OpenProcess(0x1000, False, owner.value)
    if not handle:
        return False
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return False
        return Path(buffer.value).name.lower() == Path(executable).name.lower()
    finally:
        kernel32.CloseHandle(handle)


def _visible_app_window(executable: str, name: str) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    expected = Path(executable).name.lower()
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def inspect(hwnd, unused):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        if not title.value:
            return True
        if name == "calculator" and title.value.strip().lower() == "calculator":
            found.append(hwnd)
            return False
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        handle = kernel32.OpenProcess(0x1000, False, owner.value)
        if not handle:
            return True
        try:
            image = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(image))
            if kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
                if Path(image.value).name.lower() == expected:
                    found.append(hwnd)
                    return False
        finally:
            kernel32.CloseHandle(handle)
        return True
    user32.EnumWindows(inspect, 0)
    return bool(found)


def open_app(config: dict, name: str) -> dict:
    command = app_command(name)
    if command is None:
        raise ValueError(f"Application is not installed or not supported: {name}")
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    process = subprocess.Popen(command, cwd=config["_root"], shell=False,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=flags)
    visible = False
    if sys.platform == "win32":
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (_visible_window_for_pid(process.pid) or _foreground_matches(command[0], name)
                    or _visible_app_window(command[0], name)):
                visible = True
                break
            time.sleep(0.1)
    return {"application": name, "pid": process.pid,
            "process_running": process.poll() is None, "window_verified": visible,
            "note": "Window visible" if visible else "Launch attempted; window could not be verified"}
