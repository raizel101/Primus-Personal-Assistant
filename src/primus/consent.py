"""Fail-closed, one-action desktop consent for authenticated local API tasks."""
from __future__ import annotations


def confirm_desktop(prompt: str) -> bool:
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title("PRIMUS action approval")
        root.geometry("650x360")
        root.attributes("-topmost", True)
        decision = {"approved": False}

        tk.Label(root, text="Approve this exact PRIMUS action?", font=("Segoe UI", 14, "bold")).pack(pady=8)
        details = tk.Text(root, wrap="word", height=12)
        details.insert("1.0", prompt.replace("Approve this exact action? [y/N] ", ""))
        details.configure(state="disabled")
        details.pack(fill="both", expand=True, padx=12)

        def finish(approved: bool):
            decision["approved"] = approved
            root.destroy()

        row = tk.Frame(root)
        row.pack(pady=12)
        tk.Button(row, text="Reject", command=lambda: finish(False), width=14).pack(side="left", padx=8)
        tk.Button(row, text="Approve once", command=lambda: finish(True), width=14).pack(side="left", padx=8)
        root.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        root.after(60000, lambda: finish(False))
        root.lift()
        root.focus_force()
        root.mainloop()
        return decision["approved"]
    except Exception:
        return False
