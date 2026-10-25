"""In-memory focus selection for an approved image."""
from io import BytesIO

from .image_input import _prepare, image_source


def choose_region(parent, config, path, previous=None):
    import tkinter as tk
    from PIL import Image, ImageTk
    image, _ = _prepare(image_source(config, path), "detail")
    with Image.open(BytesIO(image)) as original:
        preview = original.copy()
    preview.thumbnail((560, 360))
    width, height = preview.size
    dialog = tk.Toplevel(parent)
    dialog.title("PRIMUS — Focus on image detail")
    dialog.transient(parent)
    tk.Label(dialog, text="Drag a box around the object or text you want PRIMUS to examine.").pack(padx=12, pady=10)
    canvas = tk.Canvas(dialog, width=width, height=height, highlightthickness=0)
    canvas.pack(padx=12)
    photo = ImageTk.PhotoImage(preview)
    canvas.create_image(0, 0, image=photo, anchor="nw")
    result, selection, start, rectangle = previous, previous, None, None
    if previous:
        rectangle = canvas.create_rectangle(previous[0] * width, previous[1] * height,
                                             previous[2] * width, previous[3] * height, outline="#00e6c5", width=3)

    def press(event):
        nonlocal start, rectangle
        start = (max(0, min(width, event.x)), max(0, min(height, event.y)))
        if rectangle:
            canvas.delete(rectangle)
        rectangle = canvas.create_rectangle(*start, *start, outline="#00e6c5", width=3)

    def drag(event):
        nonlocal selection
        if start is None:
            return
        x, y = max(0, min(width, event.x)), max(0, min(height, event.y))
        left, right = sorted((start[0], x))
        top, bottom = sorted((start[1], y))
        canvas.coords(rectangle, left, top, right, bottom)
        selection = [left / width, top / height, right / width, bottom / height] if right - left >= 10 and bottom - top >= 10 else None

    def apply(full=False):
        nonlocal result
        if not full and selection is None:
            return
        result = None if full else selection
        dialog.destroy()

    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", drag)
    controls = tk.Frame(dialog)
    controls.pack(pady=12)
    tk.Button(controls, text="Use selected area", command=apply).pack(side="left", padx=6)
    tk.Button(controls, text="Use full image", command=lambda: apply(True)).pack(side="left", padx=6)
    tk.Button(controls, text="Cancel", command=dialog.destroy).pack(side="left", padx=6)
    dialog.grab_set()
    dialog.wait_window()
    preview.close()
    return result
