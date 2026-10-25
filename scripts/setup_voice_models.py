"""Explicit, one-time download of local speech models into the PRIMUS root."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
stt = root / "models" / "stt" / "faster-whisper-tiny.en"
tts = root / "models" / "tts"
os.environ["HF_HOME"] = str(root / "temp" / "huggingface")
os.environ["XDG_CACHE_HOME"] = str(root / "temp" / "xdg-cache")
os.environ["PYTHONPYCACHEPREFIX"] = str(root / "temp" / "pycache")

from huggingface_hub import snapshot_download

stt.mkdir(parents=True, exist_ok=True)
tts.mkdir(parents=True, exist_ok=True)
snapshot_download(repo_id="Systran/faster-whisper-tiny.en", local_dir=str(stt),
                  allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"])
subprocess.run([sys.executable, "-m", "piper.download_voices", "--data-dir", str(tts),
                "en_US-lessac-low"], check=True, cwd=root, env=os.environ.copy())
print(f"STT model: {stt}")
print(f"TTS model: {tts / 'en_US-lessac-low.onnx'}")
