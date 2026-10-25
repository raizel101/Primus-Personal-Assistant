"""Guarded local image-file analysis; original pixels never enter logs or storage."""
from __future__ import annotations

import hashlib
import math
from io import BytesIO
from pathlib import Path

from .security import guard_path
from .vision import _ask_local, _model_ready

MAX_SOURCE_BYTES = 10_000_000
MAX_PIXELS = 25_000_000
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
DEFAULT_QUESTION = "Describe the scene and notable objects in this image."
MODES = ("scene", "detail", "text", "gestures")
MODE_PROMPTS = {
    "scene": "In at most 70 words, give a clear scene summary, main objects, and any directly visible activity.",
    "detail": "Use four short lines labelled Scene, Objects/positions, Activity, and Uncertainty. Use at most 90 words total. Distinguish direct evidence from possible interpretations. Do not invent hidden details or exact counts when unclear.",
    "text": "Read only clearly legible non-credential text, preserving line breaks. Mark unreadable portions [unclear]. Do not guess missing characters. Finish with one sentence about the document or scene. Never transcribe passwords, recovery codes, tokens, or other credentials.",
}


def image_source(config: dict, raw: str) -> Path:
    path = guard_path(raw, config)
    if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Choose an existing JPEG, PNG, or WebP image in an approved folder")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Image exceeds the 10 MB input limit")
    return path


def checked_region(region):
    if region is None:
        return None
    if (not isinstance(region, (list, tuple)) or len(region) != 4 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in region)):
        raise ValueError("Region must contain four finite numbers: left, top, right, bottom")
    left, top, right, bottom = region
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError("Region must be ordered coordinates between 0 and 1")
    return list(region)


def _prepare(path: Path, mode: str = "scene", region=None) -> tuple[bytes, str]:
    from PIL import Image, ImageOps, UnidentifiedImageError

    if mode not in MODES:
        raise ValueError("Unknown image analysis mode")
    region = checked_region(region)
    with path.open("rb") as source_file:
        source = source_file.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("Image exceeds the 10 MB input limit")
    digest = hashlib.sha256(source).hexdigest()
    try:
        with Image.open(BytesIO(source)) as original:
            if original.format not in ALLOWED_FORMATS or original.width * original.height > MAX_PIXELS:
                raise ValueError("Unsupported image format or image dimensions")
            oriented = ImageOps.exif_transpose(original)
            # Composite transparency on white; invisible RGB pixels are not visual evidence.
            rgba = oriented.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            background.alpha_composite(rgba)
            rgb = background.convert("RGB")
            rgba.close()
            background.close()
            try:
                if region:
                    w, h = rgb.size
                    box = (int(region[0] * w), int(region[1] * h), int(region[2] * w), int(region[3] * h))
                    if box[2] - box[0] < 32 or box[3] - box[1] < 32:
                        raise ValueError("Focus region must be at least 32 by 32 original pixels")
                    cropped = rgb.crop(box)
                    rgb.close()
                    rgb = cropped
                rgb.thumbnail((2048, 1536) if mode in {"detail", "text"} else (1600, 1200), Image.Resampling.LANCZOS)
                output = BytesIO()
                rgb.save(output, format="JPEG", quality=92 if mode in {"detail", "text"} else 88, optimize=True)
                image = output.getvalue()
            finally:
                rgb.close()
                if oriented is not original:
                    oriented.close()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ValueError("Image could not be decoded") from exc
    if not image or len(image) > 3_000_000:
        raise ValueError("Prepared image exceeds the in-memory limit")
    return image, digest


def image_quality(image: bytes) -> dict:
    from PIL import Image, ImageStat
    with Image.open(BytesIO(image)) as picture:
        grayscale = picture.convert("L")
        stats = ImageStat.Stat(grayscale)
        grayscale.close()
        notes = []
        if max(picture.size) < 480:
            notes.append("Low resolution; small objects and text may be unclear.")
        if stats.mean[0] < 35 and stats.stddev[0] < 20:
            notes.append("Very dark image; use better lighting.")
        elif stats.mean[0] > 245 and stats.stddev[0] < 12:
            notes.append("Very bright image; some details may be washed out.")
        if stats.stddev[0] < 12:
            notes.append("Low contrast; some details may be hard to distinguish.")
        return {"width": picture.width, "height": picture.height, "notes": notes}


def analyze_image(config: dict, args: dict) -> dict:
    mode = args.get("mode", "scene")
    if mode not in MODES:
        raise ValueError("Unknown image analysis mode")
    if mode != "gestures" and not config.get("vision", {}).get("enabled", False):
        raise RuntimeError("Image vision is disabled in config")
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    path = image_source(config, args["path"])
    question = args.get("question") or (DEFAULT_QUESTION if mode == "scene" else "Analyze the selected image in this mode.")
    if not isinstance(question, str) or len(question) > 300:
        raise ValueError("Image question must be at most 300 characters")
    region = checked_region(args.get("region"))
    image, source_sha256 = _prepare(path, mode, region)
    quality = image_quality(image)
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    extra = {}
    if mode == "gestures":
        from .gestures import recognize_image
        extra = recognize_image(config, image)
        observation = extra.pop("observation")
        source = "local hand landmarks and geometric rules; may be inaccurate"
    else:
        _model_ready(config)
        prompt = MODE_PROMPTS[mode] + "\nUser question: " + question
        if region:
            prompt += "\nThis is a selected region, not the whole image; describe only this region."
        if quality["notes"]:
            prompt += "\nImage quality observations: " + " ".join(quality["notes"])
        observation = _ask_local(config, image, prompt)
        source = "local vision model; visual interpretation may be inaccurate"
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    return {"path": str(path), "observation": observation,
            "source_sha256": source_sha256, "mode": mode, "region": region,
            "image_info": quality, "source": source, **extra}
