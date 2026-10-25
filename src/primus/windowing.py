"""Visible desktop window inventory and consent-gated actions.

Windows uses native Win32 calls without a new dependency. Other OS backends fail
closed until a comparable, verified implementation is available.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import sys
import time


@dataclass(frozen=True)
class Window:
    id: str
    pid: int
    title: str
    application: str
    bounds: tuple[int, int, int, int] | None = None
    state: str = "normal"

    def public(self) -> dict:
        return {"id": self.id, "pid": self.pid, "title": self.title,
                "application": self.application, "bounds": self.bounds, "state": self.state}


class Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", Rect),
                ("rcWork", Rect), ("dwFlags", wintypes.DWORD)]


LAYOUT_ACTIONS = {"maximize", "snap_left", "snap_right"}


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(Rect))
    rectangle = Rect()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
        raise OSError("Could not inspect window bounds")
    return rectangle.left, rectangle.top, rectangle.right - rectangle.left, rectangle.bottom - rectangle.top


def split_work_area(work: tuple[int, int, int, int], action: str) -> tuple[int, int, int, int]:
    left, top, right, bottom = work
    if right <= left or bottom <= top:
        raise ValueError("Monitor work area is invalid")
    if action == "maximize":
        return left, top, right - left, bottom - top
    middle = left + (right - left) // 2
    if action == "snap_left":
        return left, top, middle - left, bottom - top
    if action == "snap_right":
        return middle, top, right - middle, bottom - top
    raise ValueError("Unsupported layout action")


def _layout_target(hwnd: int, action: str) -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
    monitor = user32.MonitorFromWindow(hwnd, 2)  # Nearest monitor, including minimized windows.
    if not monitor:
        raise OSError("Could not find a monitor for the window")
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(MonitorInfo)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise OSError("Could not inspect the monitor work area")
    work = info.rcWork
    return split_work_area((work.left, work.top, work.right, work.bottom), action)


def _windows_only() -> None:
    if sys.platform != "win32":
        raise NotImplementedError("Window control currently supports Windows only")


def _process_name(pid: int) -> str:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return "unknown"
    try:
        image = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image))
        if kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
            return Path(image.value).name
        return "unknown"
    finally:
        kernel32.CloseHandle(handle)


def list_windows() -> list[Window]:
    _windows_only()
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsZoomed.argtypes = (wintypes.HWND,)
    found: list[Window] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def inspect(hwnd, unused):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not 0 < length <= 512:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        if not title.value.strip():
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == os.getpid():
            return True
        state = "minimized" if user32.IsIconic(hwnd) else "maximized" if user32.IsZoomed(hwnd) else "normal"
        try:
            bounds = _window_rect(hwnd)
        except OSError:
            return True
        found.append(Window(hex(int(hwnd)), owner.value, title.value,
                            _process_name(owner.value), bounds, state))
        return len(found) < 100

    user32.EnumWindows(inspect, 0)
    return found


def resolve_window(args: dict) -> Window:
    window_id = args["id"]
    if not isinstance(window_id, str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", window_id):
        raise ValueError("Window id must be a hexadecimal id from window_list")
    if not isinstance(args["pid"], int) or isinstance(args["pid"], bool) or args["pid"] <= 0:
        raise ValueError("Window PID must be positive")
    if not isinstance(args["title"], str) or not args["title"]:
        raise ValueError("Window title is required")
    matching = next((row for row in list_windows() if row.id.lower() == window_id.lower()), None)
    if matching is None:
        raise ValueError("Window is no longer visible")
    if matching.pid != args["pid"] or matching.title != args["title"]:
        raise ValueError("Window changed since it was listed; list windows again")
    return matching


def describe_action(args: dict) -> str:
    window = resolve_window(args)
    action = args["action"]
    if action not in {"focus", "minimize", "restore", "close"} | LAYOUT_ACTIONS:
        raise ValueError("Unsupported window action")
    message = f"{action.capitalize()} window {window.title!r} ({window.application}, PID {window.pid}, id {window.id})"
    if action in LAYOUT_ACTIONS:
        target = _layout_target(int(window.id, 16), action)
        args["_expected_layout"] = list(target)
        message += f" to screen bounds x={target[0]}, y={target[1]}, width={target[2]}, height={target[3]}"
    if action == "close":
        message += "; unsaved work in that app may be lost"
    return message


def manage_window(config: dict, args: dict) -> dict:
    window = resolve_window(args)  # Recheck after the confirmation dialog.
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    action = args["action"]
    target = None
    if action in LAYOUT_ACTIONS:
        target = _layout_target(int(window.id, 16), action)
        if args.get("_expected_layout") != list(target):
            raise ValueError("Monitor layout changed after approval; request the action again")
    user32 = ctypes.windll.user32
    hwnd = int(window.id, 16)
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.GetForegroundWindow.restype = wintypes.HWND
    verified = False
    if action == "focus":
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow(hwnd)
        verified = int(user32.GetForegroundWindow() or 0) == hwnd
    elif action == "minimize":
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindow(hwnd, 6)
        verified = bool(user32.IsIconic(hwnd))
    elif action == "restore":
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindow(hwnd, 9)
        verified = bool(user32.IsWindowVisible(hwnd)) and not bool(user32.IsIconic(hwnd))
    elif action == "maximize":
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.IsZoomed.argtypes = (wintypes.HWND,)
        user32.ShowWindow(hwnd, 3)
        verified = bool(user32.IsZoomed(hwnd))
    elif action in {"snap_left", "snap_right"}:
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        user32.ShowWindow(hwnd, 9)
        if not user32.SetWindowPos(hwnd, None, *target, 0x0004 | 0x0010):
            raise OSError("Windows rejected the requested window layout")
        actual = _window_rect(hwnd)
        verified = all(abs(actual[index] - target[index]) <= 16 for index in range(4))
    elif action == "close":
        user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        if not user32.PostMessageW(hwnd, 0x0010, 0, 0):
            raise OSError("Windows rejected the close request")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (config["_root"] / "data" / "STOP").exists():
                raise RuntimeError("Global kill switch is active")
            if not user32.IsWindow(hwnd):
                verified = True
                break
            time.sleep(0.1)
    else:
        raise ValueError("Unsupported window action")
    return {"action": action, "window": window.public(), "target_bounds": target,
            "actual_bounds": _window_rect(hwnd) if action in LAYOUT_ACTIONS and user32.IsWindow(hwnd) else None,
            "verified": verified,
            "note": "Window state verified" if verified else "Action sent, but the resulting window state could not be verified"}


def _validate_input(args: dict) -> None:
    action = args.get("action")
    required = {"type": {"action", "id", "pid", "title", "text"},
                "hotkey": {"action", "id", "pid", "title", "keys"}}
    if action not in required or set(args) != required[action]:
        raise ValueError("Window input requires an exact target and either text or hotkey keys")
    if action == "type" and not args["text"]:
        raise ValueError("Text cannot be empty")
    if action == "hotkey":
        names = args["keys"]
        allowed = {"ctrl", "alt", "shift", "tab", "enter", "esc"}
        if not isinstance(names, list) or not 1 <= len(names) <= 3 or not all(isinstance(name, str) for name in names):
            raise ValueError("Hotkey must contain one to three distinct keys")
        if len(set(names)) != len(names):
            raise ValueError("Hotkey must contain one to three distinct keys")
        if any(not isinstance(name, str) or
               (name not in allowed and not (len(name) == 1 and name.isascii() and name.isalnum()))
               for name in names):
            raise ValueError("Unsupported hotkey key")


def describe_input(args: dict) -> str:
    _validate_input(args)
    window = resolve_window(args)
    target = f"window {window.title!r} ({window.application}, PID {window.pid}, id {window.id})"
    if args["action"] == "type":
        return (f"Type exactly {args['text']!r} into the currently focused control of {target}. "
                "The specific field and the app's resulting action cannot be verified")
    return (f"Press {'+'.join(args['keys'])} in {target}. "
            "The app's resulting action cannot be verified")


def _focus_window(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow(hwnd)
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        if _foreground_window_id() == hwnd:
            return True
        time.sleep(0.05)
    return False


def _foreground_window_id() -> int:
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    return int(user32.GetForegroundWindow() or 0)


def _send_keyboard(args: dict) -> None:
    from pynput.keyboard import Controller as Keyboard, Key
    keyboard = Keyboard()
    if args["action"] == "type":
        keyboard.type(args["text"])
        return
    special = {"ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift,
               "tab": Key.tab, "enter": Key.enter, "esc": Key.esc}
    pressed = []
    try:
        for name in args["keys"]:
            key = special.get(name, name)
            keyboard.press(key)
            pressed.append(key)
    finally:
        for key in reversed(pressed):
            keyboard.release(key)


def send_window_input(config: dict, args: dict) -> dict:
    _validate_input(args)
    window = resolve_window(args)  # Recheck after exact approval.
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    if not _focus_window(int(window.id, 16)):
        raise RuntimeError("Target window could not be focused; no input was sent")
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    if _foreground_window_id() != int(window.id, 16):
        raise RuntimeError("Target window lost focus; no input was sent")
    _send_keyboard(args)
    return {"action": args["action"], "window": window.public(), "sent": True,
            "verified": False,
            "note": "Input was sent to the target window; the app's resulting action was not verified"}
