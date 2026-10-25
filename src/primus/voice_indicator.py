"""Small local Tk window with a rotating, projected 3D voice orb."""
from __future__ import annotations

import json
import math
import os
import time
import tkinter as tk

from .process_lock import ProcessLock


COLORS = {"starting": (93, 176, 235), "listening": (53, 214, 192),
          "checking": (100, 165, 255),
          "heard": (255, 190, 76), "processing": (172, 119, 255),
          "speaking": (96, 220, 250), "stopped": (110, 120, 143),
          "error": (245, 89, 104)}
LABELS = {"starting": "STARTING", "listening": "LISTENING",
          "checking": "CHECKING WAKE WORD",
          "heard": "WAKE WORD HEARD", "processing": "PROCESSING",
          "speaking": "SPEAKING", "stopped": "MIC OFF", "error": "VOICE ERROR"}


def indicator_visible(state: str, manual: bool = False) -> bool:
    """Show the orb after an accepted wake word, through the reply."""
    return manual or state in {"heard", "processing", "speaking", "error"}


def voice_state(config: dict) -> str:
    path = config["_root"] / "data" / "voice-status.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8")).get("state", "stopped")
        return state if state in COLORS else "stopped"
    except (OSError, ValueError, TypeError):
        return "stopped"


def _hex(red: int, green: int, blue: int) -> str:
    return f"#{max(0, min(255, red)):02x}{max(0, min(255, green)):02x}{max(0, min(255, blue)):02x}"


def _project(latitude: float, longitude: float, rotation: float) -> tuple[float, float, float]:
    x = math.cos(latitude) * math.sin(longitude)
    y = math.sin(latitude)
    z = math.cos(latitude) * math.cos(longitude)
    turned_x = x * math.cos(rotation) + z * math.sin(rotation)
    turned_z = z * math.cos(rotation) - x * math.sin(rotation)
    perspective = 2.7 / (2.7 - turned_z * 0.3)
    return turned_x * perspective, y * perspective, turned_z


def draw_orb(canvas: tk.Canvas, state: str, phase: float) -> None:
    canvas.delete("all")
    color = COLORS[state]
    center_x, center_y, radius = 88, 82, 58
    pulse = 1 + (0.06 if state in {"checking", "heard", "processing", "speaking"} else 0.025) * math.sin(phase * 3)
    radius *= pulse
    canvas.create_oval(center_x-radius-9, center_y-radius-2, center_x+radius+9,
                       center_y+radius+16, outline=_hex(*(int(c * 0.28) for c in color)), width=2)
    for step in range(16, 0, -1):
        portion = step / 16
        r = radius * portion
        light = 0.2 + 0.58 * (1 - portion)
        fill = _hex(*(int(c * light) for c in color))
        canvas.create_oval(center_x-r-5*(1-portion), center_y-r-7*(1-portion),
                           center_x+r-5*(1-portion), center_y+r-7*(1-portion),
                           fill=fill, outline=fill)
    wire = _hex(*(min(255, int(c * 0.72 + 50)) for c in color))
    rotation = phase * (1.7 if state == "processing" else 0.65)
    # Meridians and parallels are actual 3D sphere coordinates projected each frame.
    for longitude in [index * math.pi / 3 for index in range(6)]:
        points = []
        for index in range(37):
            x, y, z = _project(-math.pi/2 + index*math.pi/36, longitude, rotation)
            if z >= -0.06:
                points.extend((center_x + radius*x, center_y + radius*y))
            elif len(points) >= 4:
                canvas.create_line(*points, fill=wire, width=1, smooth=True)
                points = []
        if len(points) >= 4:
            canvas.create_line(*points, fill=wire, width=1, smooth=True)
    for latitude in (-math.pi/3, 0, math.pi/3):
        points = []
        for index in range(73):
            x, y, z = _project(latitude, index*math.pi/36, rotation)
            if z >= -0.06:
                points.extend((center_x + radius*x, center_y + radius*y))
            elif len(points) >= 4:
                canvas.create_line(*points, fill=wire, width=1, smooth=True)
                points = []
        if len(points) >= 4:
            canvas.create_line(*points, fill=wire, width=1, smooth=True)
    canvas.create_oval(center_x-31, center_y-39, center_x-10, center_y-18,
                       fill="#d9fff9", outline="")
    canvas.create_text(center_x, 163, text=LABELS[state], fill="#eef5ff",
                       font=("Segoe UI", 10, "bold"))


def run_indicator(config: dict) -> None:
    with ProcessLock(config["_root"] / "data" / "indicator.lock"):
        manual_open = config["_root"] / "data" / "orb-open"
        window = tk.Tk()
        window.withdraw()
        window.title("PRIMUS voice")
        window.configure(bg="#0b1020")
        window.resizable(False, False)
        window.attributes("-topmost", True)
        width, height = 176, 184
        window.geometry(f"{width}x{height}+{window.winfo_screenwidth()-width-24}+48")
        canvas = tk.Canvas(window, width=width, height=height, bg="#0b1020",
                           highlightthickness=0)
        canvas.pack()
        start = time.monotonic()
        shown = False
        dismissed = False
        def dismiss():
            nonlocal shown, dismissed
            manual_open.unlink(missing_ok=True)
            dismissed = True
            shown = False
            window.withdraw()
        window.protocol("WM_DELETE_WINDOW", dismiss)
        def tick():
            nonlocal shown, dismissed
            if (config["_root"] / "data" / "STOP").exists():
                window.destroy()
                return
            state = voice_state(config)
            manual = manual_open.exists()
            if not indicator_visible(state, manual):
                dismissed = False
            should_show = indicator_visible(state, manual) and not dismissed
            if should_show and not shown:
                window.deiconify()
                window.lift()
                shown = True
            elif not should_show and shown:
                window.withdraw()
                shown = False
            if should_show:
                draw_orb(canvas, state, time.monotonic() - start)
            window.after(50, tick)
        tick()
        window.mainloop()
