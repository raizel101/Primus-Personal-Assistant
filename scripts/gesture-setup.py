"""Preview or apply the optional root-local, offline hand-recognition setup."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen

COMMIT = "25f423d0e04c31a17254620e58febd7386da523b"
RAW = f"https://raw.githubusercontent.com/opencv/opencv_zoo/{COMMIT}"
MEDIA = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{COMMIT}"
ASSETS = [
    ("models/gestures/palm.onnx", f"{MEDIA}/models/palm_detection_mediapipe/palm_detection_mediapipe_2023feb.onnx",
     "78ff51c38496b7fc8b8ebdb6cc8c1abb02fa6c38427c6848254cdaba57fcce7c", 3_905_734),
    ("models/gestures/hand.onnx", f"{MEDIA}/models/handpose_estimation_mediapipe/handpose_estimation_mediapipe_2023feb.onnx",
     "db0898ae717b76b075d9bf563af315b29562e11f8df5027a1ef07b02bef6d81c", 4_099_621),
    ("src/primus/_hand_models/mp_palmdet.py", f"{RAW}/models/palm_detection_mediapipe/mp_palmdet.py", None, None),
    ("src/primus/_hand_models/mp_handpose.py", f"{RAW}/models/handpose_estimation_mediapipe/mp_handpose.py", None, None),
    ("src/primus/_hand_models/LICENSE", f"{RAW}/LICENSE", None, None),
    ("models/gestures/PALM_LICENSE", f"{RAW}/models/palm_detection_mediapipe/LICENSE", None, None),
    ("models/gestures/HAND_LICENSE", f"{RAW}/models/handpose_estimation_mediapipe/LICENSE", None, None),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Install and download after explicit approval")
    parser.add_argument("--skip-package", action="store_true", help="Use an already installed OpenCV")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    configured = Path(json.loads((root / "config.json").read_text(encoding="utf-8-sig"))["root"]).resolve()
    if root != configured:
        raise ValueError("Root setting must match the project folder")
    print(f"Install opencv-python==4.13.0.92 without dependency replacements into {root / 'vendor'}")
    print(f"Download two hand models and inference source/licenses from OpenCV Zoo commit {COMMIT} into {root}")
    if not args.apply:
        print("Preview only. Use --apply after approval. Camera will not be opened.")
        return
    env = os.environ.copy()
    temp = root / "temp"
    temp.mkdir(exist_ok=True)
    env.update({"TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
                "PIP_CACHE_DIR": str(temp / "pip-cache"), "PYTHONPYCACHEPREFIX": str(temp / "pycache")})
    if not args.skip_package:
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "--only-binary=:all:", "--no-deps", "--target", str(root / "vendor"),
                        "opencv-python==4.13.0.92"], env=env, cwd=root, check=True)
    for relative, url, expected, size in ASSETS:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(url, timeout=60) as response:
            content = response.read(10_000_001)
        if len(content) > 10_000_000 or (size and len(content) != size):
            raise ValueError(f"Unexpected asset size: {relative}")
        digest = hashlib.sha256(content).hexdigest()
        if expected and digest != expected:
            raise ValueError(f"Checksum mismatch: {relative}")
        if relative.endswith("mp_palmdet.py"):
            content = content.replace(
                b"        keep_idx = cv.dnn.NMSBoxes(boxes, score, self.score_threshold, self.nms_threshold, top_k=self.topK)",
                b"        # PRIMUS: OpenCV NMS takes x/y/width/height, while model boxes are x1/y1/x2/y2.\n"
                b"        nms_boxes = np.c_[boxes[:, :2], boxes[:, 2:] - boxes[:, :2]]\n"
                b"        keep_idx = cv.dnn.NMSBoxes(nms_boxes, score, self.score_threshold, self.nms_threshold, top_k=self.topK)")
            digest = hashlib.sha256(content).hexdigest()
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Refusing to replace a different existing file: {destination}")
        else:
            with destination.open("xb") as handle:
                handle.write(content)
        print(f"Verified {relative}: {digest}")
    print("Gesture setup ready. Start Camera explicitly in PRIMUS to use it.")


if __name__ == "__main__":
    main()
