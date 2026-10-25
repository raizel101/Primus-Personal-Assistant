"""Optional local tray and hotkey client for the PRIMUS API."""
from __future__ import annotations
import json
import os
import queue
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from .auth import api_token, local_session_id
from .process_lock import ProcessLock

def _image():
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (64, 64), "#182433")
    draw = ImageDraw.Draw(image)
    draw.ellipse((9, 9, 55, 55), fill="#5bd6b0")
    draw.text((25, 17), "P", fill="#182433")
    return image

def prompt_once(config):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from .image_input import image_source
    root = tk.Tk()
    root.title("PRIMUS")
    root.geometry("620x335")
    entry = tk.Entry(root)
    entry.pack(fill="x", padx=12, pady=12)
    entry.focus_set()
    selected_image: str | None = None
    selected_region = None
    selected_label = tk.StringVar(value="No image attached")
    mode = tk.StringVar(value="scene")

    def attach_image() -> None:
        nonlocal selected_image, selected_region
        path = filedialog.askopenfilename(parent=root, title="Choose an approved image",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.webp")])
        if not path:
            return
        try:
            selected_image = str(image_source(config, path))
        except Exception as exc:
            messagebox.showerror("PRIMUS", str(exc), parent=root)
            return
        selected_label.set("Image: " + selected_image)
        selected_region = None
        focus_button.configure(state="normal")
        mode_choice.configure(state="readonly")

    def focus_image():
        nonlocal selected_region
        if not selected_image:
            return
        try:
            from .image_focus import choose_region
            selected_region = choose_region(root, config, selected_image, selected_region)
            selected_label.set(("Selected image area: " if selected_region else "Image: ") + selected_image)
        except Exception as exc:
            messagebox.showerror("PRIMUS", str(exc), parent=root)

    attach_button = tk.Button(root, text="Attach image", command=attach_image)
    attach_button.pack(anchor="w", padx=12)
    tk.Label(root, textvariable=selected_label, anchor="w", wraplength=590).pack(fill="x", padx=12)
    image_options = tk.Frame(root)
    image_options.pack(fill="x", padx=12, pady=6)
    tk.Label(image_options, text="Image mode:").pack(side="left")
    mode_choice = ttk.Combobox(image_options, textvariable=mode, values=("scene", "detail", "text", "gestures"), width=12, state="disabled")
    mode_choice.pack(side="left", padx=8)
    focus_button = tk.Button(image_options, text="Focus area…", command=focus_image, state="disabled")
    focus_button.pack(side="left")
    def mission_control():
        subprocess.Popen([sys.executable, str(config["_root"] / "primus.py"), "dashboard"],
                         cwd=config["_root"], env=_child_environment(config["_root"]))
    tk.Button(image_options, text="Mission Control", command=mission_control).pack(side="right")
    status = tk.StringVar(value="Enter a request, then press Run.")
    status_label = tk.Label(root, textvariable=status, anchor="w")
    status_label.pack(fill="x", padx=12)
    progress = ttk.Progressbar(root, mode="indeterminate")
    progress.pack(fill="x", padx=12, pady=(8, 4))
    finished: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=1)
    running = False
    started = 0.0

    def request_task(task: str, image_path: str | None, image_mode: str, region) -> None:
        try:
            route = "/images/analyze" if image_path else "/tasks"
            payload = ({"path": image_path, "mode": image_mode} if image_path else
                       {"task": task, "session": local_session_id()})
            if image_path:
                if task:
                    payload["question"] = task
                if region:
                    payload["region"] = region
            endpoint = f"http://127.0.0.1:{config['local_api']['port']}{route}"
            request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                              headers={"Authorization": "Bearer " + api_token(), "Content-Type": "application/json"},
                              method="POST")
            with urlopen(request, timeout=180) as response:
                answer = json.load(response)
            result = answer.get("result", answer)
            if image_path:
                detail = result.get("observation", str(result))
                notes = result.get("image_info", {}).get("notes", [])
                if notes:
                    detail += "\n\nImage quality: " + " ".join(notes)
            else:
                detail = str(result)
            finished.put((True, detail))
        except HTTPError as exc:
            try:
                detail = json.load(exc).get("error", str(exc))
            except Exception:
                detail = str(exc)
            finished.put((False, detail))
        except Exception as exc:
            finished.put((False, str(exc)))

    def poll() -> None:
        try:
            ok, result = finished.get_nowait()
        except queue.Empty:
            elapsed = int(time.monotonic() - started)
            status.set(f"PRIMUS is processing or awaiting approval... {elapsed}s")
            root.after(200, poll)
            return
        progress.stop()
        status.set("Finished." if ok else "Request needs attention.")
        root.update_idletasks()
        if ok:
            messagebox.showinfo("PRIMUS result", result, parent=root)
        else:
            messagebox.showerror("PRIMUS", result, parent=root)
        root.destroy()

    def submit(event=None):
        nonlocal running, started
        if running:
            return
        task = entry.get().strip()
        if not task and not selected_image:
            return
        running = True
        started = time.monotonic()
        entry.configure(state="disabled")
        attach_button.configure(state="disabled")
        focus_button.configure(state="disabled")
        mode_choice.configure(state="disabled")
        run_button.configure(state="disabled")
        status.set("PRIMUS is processing or awaiting approval... 0s")
        progress.start(10)
        threading.Thread(target=request_task, args=(task, selected_image, mode.get(), selected_region), daemon=True).start()
        root.after(200, poll)

    def close() -> None:
        if running:
            messagebox.showinfo("PRIMUS", "This request is still running. Wait for its result, or use the global PRIMUS stop command.", parent=root)
            return
        root.destroy()

    entry.bind("<Return>", submit)
    run_button = tk.Button(root, text="Run", command=submit)
    run_button.pack(pady=5)
    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()

def _start_local_core(config):
    from .api import make_server
    endpoint = f"http://127.0.0.1:{config['local_api']['port']}/health"
    try:
        server, scheduler = make_server(config)
    except OSError:
        request = Request(endpoint, headers={"Authorization": "Bearer " + api_token()})
        with urlopen(request, timeout=2) as response:
            if not json.load(response).get("ok"):
                raise RuntimeError("Existing local API did not verify")
        return None, None, None
    try:
        server.start_local_mcp(config)
    except Exception:
        server.server_close()
        raise
    stop = threading.Event()
    api_thread = threading.Thread(target=server.serve_forever, daemon=True)
    scheduler_thread = threading.Thread(target=scheduler.run_forever, args=(stop,), daemon=True)
    api_thread.start()
    scheduler_thread.start()
    return server, stop, scheduler_thread

def _child_environment(root):
    environment = os.environ.copy()
    environment.update({
        "TEMP": str(root / "temp"), "TMP": str(root / "temp"),
        "HF_HOME": str(root / "temp" / "huggingface"),
        "XDG_CACHE_HOME": str(root / "temp" / "xdg-cache"),
        "PYTHONPYCACHEPREFIX": str(root / "temp" / "pycache"),
    })
    return environment

def run_tray(config):
    with ProcessLock(config["_root"] / "data" / "tray.lock"):
        _run_tray(config)


def _run_tray(config):
    try:
        import pystray
        from pynput import keyboard
    except ImportError as exc:
        raise RuntimeError("Install the approved tray dependencies into PRIMUS/vendor first") from exc
    server, stop, scheduler_thread = _start_local_core(config)
    voice_process = None
    indicator_process = None
    def open_prompt():
        subprocess.Popen([sys.executable, str(config["_root"] / "primus.py"), "prompt"],
                         cwd=config["_root"])
    def open_gestures():
        subprocess.Popen([sys.executable, str(config["_root"] / "primus.py"), "gestures"],
                         cwd=config["_root"], env=_child_environment(config["_root"]))
    def open_mission_control():
        subprocess.Popen([sys.executable, str(config["_root"] / "primus.py"), "dashboard"],
                         cwd=config["_root"], env=_child_environment(config["_root"]))
    def start_voice():
        nonlocal voice_process
        if voice_process is None or voice_process.poll() is not None:
            root = config["_root"]
            voice_process = subprocess.Popen([sys.executable, str(config["_root"] / "primus.py"), "voice"],
                                             cwd=root, env=_child_environment(root),
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def start_indicator():
        nonlocal indicator_process
        if indicator_process is None or indicator_process.poll() is not None:
            root = config["_root"]
            with (root / "logs" / "indicator.err.log").open("ab") as errors:
                indicator_process = subprocess.Popen([sys.executable, str(root / "primus.py"), "indicator"],
                                                     cwd=root, env=_child_environment(root),
                                                     stdout=subprocess.DEVNULL, stderr=errors)
    def stop_indicator():
        nonlocal indicator_process
        if indicator_process is not None and indicator_process.poll() is None:
            indicator_process.terminate()
            try:
                indicator_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                indicator_process.kill()
                indicator_process.wait(timeout=5)
        indicator_process = None
    def stop_voice():
        nonlocal voice_process
        if voice_process is not None and voice_process.poll() is None:
            voice_process.terminate()
            try:
                voice_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                voice_process.kill()
                voice_process.wait(timeout=5)
            from .voice import write_voice_status
            write_voice_status(config, "stopped")
        voice_process = None
    listener = keyboard.GlobalHotKeys({config["tray"]["hotkey"]: open_prompt})
    listener.start()
    icon = pystray.Icon("PRIMUS", _image(), "PRIMUS",
                        menu=pystray.Menu(pystray.MenuItem("Ask PRIMUS", lambda *_: open_prompt()),
                                          pystray.MenuItem("Mission Control", lambda *_: open_mission_control()),
                                          pystray.MenuItem("Gesture camera", lambda *_: open_gestures()),
                                          pystray.MenuItem("Start Voice", lambda *_: start_voice()),
                                          pystray.MenuItem("Stop Voice", lambda *_: stop_voice()),
                                          pystray.MenuItem("Quit", lambda icon, _: icon.stop())))
    try:
        if config["tray"].get("show_voice_indicator", False):
            start_indicator()
        if config["tray"].get("auto_start_voice", False):
            start_voice()
        icon.run()
    finally:
        listener.stop()
        stop_voice()
        stop_indicator()
        if server:
            stop.set()
            server.shutdown()
            server.server_close()
            scheduler_thread.join(timeout=2)
