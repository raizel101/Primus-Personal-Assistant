"""Explicit-start camera preview; frames and landmarks remain in memory."""
from __future__ import annotations

import queue
import threading

from .audit import append_audit
from .gestures import CONNECTIONS, LABELS, StableGesture, camera_frames
from .gesture_actions import COMMANDS, GestureActionGate, dispatch


def run_gesture_preview(config):
    import tkinter as tk
    from tkinter import messagebox, ttk
    from PIL import Image, ImageTk
    if not config.get("gestures", {}).get("enabled", False):
        raise RuntimeError("Gesture recognition is disabled in config")
    root = tk.Tk()
    root.title("PRIMUS — Local gesture vision")
    root.geometry("700x760")
    root.configure(bg="#101923")
    tk.Label(root, text="PRIMUS Gesture Vision", font=("Segoe UI", 19, "bold"),
             fg="#64dec4", bg="#101923").pack(pady=(16, 5))
    tk.Label(root, text="Camera starts only when you choose Start Camera. Frames stay on this laptop.",
             fg="#b8c6d5", bg="#101923").pack()
    frame = tk.Frame(root, width=640, height=420, bg="#080d14")
    frame.pack(padx=20, pady=12)
    frame.pack_propagate(False)
    picture = tk.Label(frame, text="Camera is off", bg="#080d14", fg="#a6b9cb")
    picture.pack(expand=True, fill="both")
    status = tk.StringVar(value="Show one well-lit hand; keep it in view for at least 0.6 seconds.")
    tk.Label(root, textvariable=status, wraplength=650, fg="white", bg="#101923").pack(padx=15)
    tk.Label(root, text="Thumb up → YouTube   ·   Victory V → Chrome   ·   Pointing → Google\nHold 1 second. Remove hand for 1 second before another action.\nEach app or website request still needs Approve once.",
             fg="#a6b9cb", bg="#101923").pack(pady=8)
    controls = ttk.Frame(root)
    controls.pack()
    action_enabled = threading.Event()
    action_busy = threading.Event()
    enabled_var = tk.BooleanVar(value=False)
    action_status = tk.StringVar(value="Actions OFF. Enable below, then remove your hand before posing.")
    def enable_actions():
        if enabled_var.get():
            action_enabled.set()
            action_status.set("Actions ON. Remove hand, then hold a mapped gesture for 1 second.")
        else:
            action_enabled.clear()
            action_status.set("Actions OFF. An already requested action keeps its separate approval dialog.")
    ttk.Checkbutton(root,text="Enable gesture commands for this camera session",variable=enabled_var,command=enable_actions).pack(pady=8)
    tk.Label(root,textvariable=action_status,wraplength=650,fg="#64dec4",bg="#101923").pack(padx=15,pady=4)
    action_results = queue.Queue()

    def request_action(gesture):
        action_status.set(COMMANDS[gesture][0]+" requested. Review the separate PRIMUS approval window.")
        def work():
            try:
                action_results.put(dispatch(config,gesture))
            except Exception as exc:
                action_results.put("Not completed: "+str(exc)+". No automatic retry.")
        threading.Thread(target=work,daemon=True).start()
    frames = queue.Queue(maxsize=1)
    events = queue.Queue()
    stop = threading.Event()
    worker = None
    closing = False

    def capture():
        try:
            import cv2
            stable = StableGesture(config["gestures"].get("hold_ms", 600))
            gate = GestureActionGate()
            # Require hands out of frame after Start/Enable before first action.
            gate.armed = False
            stream = camera_frames(config, stop)
            try:
                for bgr, hands, timestamp in stream:
                    primary = max(hands, key=lambda h: max(0, h["bounds"][2] - h["bounds"][0]) * max(0, h["bounds"][3] - h["bounds"][1])) if hands else None
                    label = primary["gesture"] if primary else "no_hand"
                    confirmed = stable.update(label, timestamp)
                    command = gate.update(label,timestamp,enabled=action_enabled.is_set(),busy=action_busy.is_set())
                    if command and not stop.is_set():
                        action_busy.set()
                        events.put(("action",command))
                    for hand in hands:
                        points = hand["landmarks"]
                        color = (180, 225, 65) if confirmed and hand is primary else (70, 180, 240)
                        for a, b in CONNECTIONS:
                            cv2.line(bgr, tuple(int(v) for v in points[a][:2]), tuple(int(v) for v in points[b][:2]), color, 2)
                        for point in points:
                            cv2.circle(bgr, tuple(int(v) for v in point[:2]), 3, color, -1)
                    info = LABELS[label]
                    if primary:
                        info += f" — {'held steady' if confirmed else 'hold steady to confirm'}; hand tracking {primary['hand_score']:.2f}"
                    image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                    image.thumbnail((640, 420))
                    if frames.full():
                        try:
                            frames.get_nowait()
                        except queue.Empty:
                            pass
                    frames.put_nowait((image, info))
            finally:
                stream.close()
        except Exception as exc:
            events.put(("error",str(exc)))
            append_audit(config["_root"], "gesture_camera_error", {}, {"ok": False, "error": str(exc)})

    def start():
        nonlocal worker
        if worker is not None and worker.is_alive():
            return
        if (config["_root"] / "data" / "STOP").exists():
            messagebox.showerror("PRIMUS", "Global kill switch is active", parent=root)
            return
        stop.clear()
        status.set("Loading local hand models and opening camera…")
        start_button.configure(state="disabled")
        stop_button.configure(state="normal")
        worker = threading.Thread(target=capture, daemon=True)
        worker.start()

    def halt():
        stop.set()
        action_enabled.clear()
        enabled_var.set(False)
        action_status.set("Actions OFF. Stop does not undo an already approved action.")
        stop_button.configure(state="disabled")
        status.set("Stopping camera…")

    def close():
        nonlocal closing
        closing = True
        halt()

    def poll():
        try:
            image, info = frames.get_nowait()
            if not stop.is_set():
                picture.photo = ImageTk.PhotoImage(image)
                picture.configure(image=picture.photo, text="")
                status.set(info)
        except queue.Empty:
            pass
        try:
            kind, value = events.get_nowait()
            if kind == "action":
                if action_enabled.is_set() and not stop.is_set() and not closing:
                    request_action(value)
                else:
                    action_busy.clear()
            else:
                status.set(value)
                action_enabled.clear()
                enabled_var.set(False)
                if not closing:
                    messagebox.showerror("PRIMUS camera", value, parent=root)
        except queue.Empty:
            pass
        try:
            result = action_results.get_nowait()
            action_busy.clear()
            action_status.set(result+" Remove hand for 1 second to rearm.")
        except queue.Empty:
            pass
        if worker is None or not worker.is_alive():
            if closing:
                root.destroy()
                return
            start_button.configure(state="normal")
            stop_button.configure(state="disabled")
            if worker is not None:
                picture.configure(image="", text="Camera is off")
                picture.photo = None
                if stop.is_set():
                    status.set("Camera stopped. No frames were saved.")
        root.after(50, poll)

    start_button = ttk.Button(controls, text="Start Camera", command=start)
    start_button.pack(side="left", padx=8)
    stop_button = ttk.Button(controls, text="Stop Camera", command=halt, state="disabled")
    stop_button.pack(side="left", padx=8)
    root.bind("<Escape>", lambda _: close())
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(50, poll)
    root.mainloop()
