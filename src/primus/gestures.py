"""Offline hand landmarks and conservative static gesture recognition."""
from __future__ import annotations

import hashlib
import math
import sys
import time

from .audit import append_audit
from .process_lock import ProcessLock

MODEL_HASHES = {
    "palm.onnx": "78ff51c38496b7fc8b8ebdb6cc8c1abb02fa6c38427c6848254cdaba57fcce7c",
    "hand.onnx": "db0898ae717b76b075d9bf563af315b29562e11f8df5027a1ef07b02bef6d81c",
}
LABELS = {"open_palm": "Open palm", "closed_fist": "Closed fist", "pointing": "Pointing",
          "victory": "Victory / two fingers", "thumb_up": "Thumb up", "thumb_down": "Thumb down",
          "pinch": "Pinch", "unknown": "Uncertain gesture", "no_hand": "No hand detected"}
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15),
               (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]


def model_paths(config):
    root = config["_root"].resolve()
    paths = {name: (root / "models" / "gestures" / name).resolve() for name in MODEL_HASHES}
    for name, path in paths.items():
        if not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError("Gesture models missing; run scripts/gesture-setup.py after approval")
        if path.stat().st_size > 5_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_HASHES[name]:
            raise RuntimeError("Gesture model integrity check failed")
    return paths


def classify_landmarks(points, screen) -> str:
    """Geometric labels, not calibrated gesture confidence or sign-language translation."""
    import numpy as np
    p, s = np.asarray(points, dtype=float), np.asarray(screen, dtype=float)
    if p.shape != (21, 3) or s.shape != (21, 3) or not np.isfinite(p).all() or not np.isfinite(s).all():
        return "unknown"
    distance = lambda a, b: float(np.linalg.norm(p[a] - p[b]))
    palm_width = distance(5, 17)
    if palm_width < 1e-6:
        return "unknown"

    def angle(a, b, c):
        v, w = p[a] - p[b], p[c] - p[b]
        denominator = np.linalg.norm(v) * np.linalg.norm(w)
        return math.degrees(math.acos(float(np.clip(np.dot(v, w) / denominator, -1, 1)))) if denominator > 1e-12 else 0

    states = []
    for base in (5, 9, 13, 17):
        joint_angle = angle(base, base + 1, base + 3)
        reach = distance(base + 3, 0) / max(distance(base + 1, 0), 1e-9)
        states.append(1 if joint_angle > 135 and reach > 1.1 else 0 if joint_angle < 110 or reach < 0.95 else -1)
    fingers = [state == 1 for state in states]
    thumb = angle(2, 3, 4) > 145 and distance(4, 5) > palm_width * 0.5
    if distance(4, 8) < palm_width * 0.25 and any(fingers[1:]):
        return "pinch"
    if -1 in states:
        return "unknown"
    if all(fingers) and thumb:
        return "open_palm"
    if fingers == [True, True, False, False]:
        return "victory"
    if fingers == [True, False, False, False]:
        return "pointing"
    if not any(fingers):
        if thumb:
            direction = s[4, :2] - s[2, :2]
            length = float(np.linalg.norm(direction))
            if length > 1e-6 and abs(direction[1]) > length * 0.65:
                return "thumb_up" if direction[1] < 0 else "thumb_down"
            return "unknown"
        return "closed_fist"
    return "unknown"


class StableGesture:
    """Require a sustained label and reset immediately when a hand is lost."""
    def __init__(self, hold_ms=600, min_frames=4):
        self.hold_ms, self.min_frames = hold_ms, min_frames
        self.candidate, self.since, self.frames = "unknown", 0, 0
        self.previous_timestamp = None

    def update(self, label: str, timestamp_ms: int) -> str | None:
        gap = self.previous_timestamp is not None and (timestamp_ms < self.previous_timestamp or timestamp_ms - self.previous_timestamp > 500)
        self.previous_timestamp = timestamp_ms
        if gap:
            self.candidate, self.frames = "unknown", 0
        if label in {"unknown", "no_hand"}:
            self.candidate, self.frames = "unknown", 0
            return None
        if label != self.candidate or timestamp_ms < self.since:
            self.candidate, self.since, self.frames = label, timestamp_ms, 1
        else:
            self.frames += 1
        if self.frames >= self.min_frames and timestamp_ms - self.since >= self.hold_ms:
            return label
        return None


class HandRecognizer:
    def __init__(self, config):
        if not config.get("gestures", {}).get("enabled", False):
            raise RuntimeError("Gesture recognition is disabled in config")
        if (config["_root"] / "data" / "STOP").exists():
            raise RuntimeError("Global kill switch is active")
        try:
            import cv2
            from ._hand_models.mp_palmdet import MPPalmDet
            from ._hand_models.mp_handpose import MPHandPose
        except ImportError as exc:
            raise RuntimeError("Gesture setup is missing; run scripts/gesture-setup.py after approval") from exc
        paths = model_paths(config)
        cv2.setNumThreads(2)
        self.config = config
        self.palms = MPPalmDet(str(paths["palm.onnx"]), scoreThreshold=0.65, topK=4)
        self.hands = MPHandPose(str(paths["hand.onnx"]), confThreshold=config["gestures"].get("min_hand_score", 0.8))

    def recognize(self, frame) -> list[dict]:
        import cv2
        import numpy as np
        if (self.config["_root"] / "data" / "STOP").exists():
            raise RuntimeError("Global kill switch is active")
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("Hand recognizer expects a BGR image")
        ratio = min(1.0, 960 / max(frame.shape[:2]))
        image = cv2.resize(frame, (round(frame.shape[1] * ratio), round(frame.shape[0] * ratio))) if ratio < 1 else frame
        palms = self.palms.infer(image)
        result = []
        for palm in sorted(palms, key=lambda row: float(row[-1]), reverse=True)[:2]:
            if not np.isfinite(palm).all() or palm[2] <= palm[0] or palm[3] <= palm[1]:
                continue
            try:
                hand = self.hands.infer(image, palm)
            except cv2.error:
                continue  # A partially clipped palm can be unsuitable for a landmark crop.
            if hand is None or not np.isfinite(hand).all():
                continue
            screen = hand[4:67].reshape(21, 3)
            points = hand[67:130].reshape(21, 3)
            label = classify_landmarks(points, screen)
            result.append({"gesture": label, "hand_score": round(float(hand[-1]), 3),
                           "landmarks": (screen / ratio).tolist(),
                           "bounds": (hand[:4] / ratio).tolist()})
        return result


def recognize_image(config, image: bytes) -> dict:
    import cv2
    import numpy as np
    frame = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Image could not be decoded for hand recognition")
    hands = HandRecognizer(config).recognize(frame)
    public = [{"gesture": hand["gesture"], "hand_score": hand["hand_score"]} for hand in hands]
    observation = ("Detected: " + "; ".join(LABELS[h["gesture"]] for h in hands) +
                   ". Hand scores measure tracking quality, not gesture certainty.") if hands else "No hand was detected confidently. Show a well-lit, unobstructed hand closer to the camera."
    return {"observation": observation, "hands": public}


def camera_frames(config, stop):
    """Acquire/release a local camera only during an explicitly started session."""
    import cv2
    with ProcessLock(config["_root"] / "data" / "camera.lock"):
        engine = HandRecognizer(config)
        if stop.is_set() or (config["_root"] / "data" / "STOP").exists():
            return
        camera = None
        opened = False
        try:
            settings = config["gestures"]
            index = settings.get("camera_index", 0)
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 9:
                raise ValueError("Camera index must be a local device number from 0 to 9")
            camera = cv2.VideoCapture(index, cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY)
            if not camera.isOpened():
                raise RuntimeError("Camera is unavailable. Check camera permission and close other camera apps.")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            opened = True
            append_audit(config["_root"], "gesture_camera_start", {"camera_index": index}, {"ok": True})
            while not stop.is_set():
                if (config["_root"] / "data" / "STOP").exists():
                    raise RuntimeError("Global kill switch is active")
                began = time.monotonic()
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("Camera frame unavailable; camera session stopped")
                if stop.is_set() or (config["_root"] / "data" / "STOP").exists():
                    break
                frame = cv2.flip(frame, 1)
                hands = engine.recognize(frame)
                if stop.is_set() or (config["_root"] / "data" / "STOP").exists():
                    break
                yield frame, hands, int(time.monotonic() * 1000)
                stop.wait(max(0, 1 / settings.get("max_fps", 12) - (time.monotonic() - began)))
        finally:
            if camera is not None:
                camera.release()
            if opened:
                append_audit(config["_root"], "gesture_camera_stop", {}, {"ok": True})
