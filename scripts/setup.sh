#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" "$ROOT/primus.py" init
if [ "${INSTALL_PHASE2:-0}" = "1" ]; then
    printf 'Install pinned keyring, pystray, pynput, Pillow into %s/vendor from PyPI? Type YES: ' "$ROOT"
    read -r answer
    [ "$answer" = "YES" ] || { echo "Phase 2 dependency install not approved" >&2; exit 1; }
    PIP_CACHE_DIR="$ROOT/temp/pip-cache" TMPDIR="$ROOT/temp" PYTHONPYCACHEPREFIX="$ROOT/temp/pycache" \
        "$PYTHON_BIN" -m pip --disable-pip-version-check install --target "$ROOT/vendor" -r "$ROOT/requirements-phase2.txt"
fi
if [ "${INSTALL_VOICE:-0}" = "1" ]; then
    printf 'Install pinned faster-whisper, sounddevice, Piper and local speech models into %s? Type YES: ' "$ROOT"
    read -r answer
    [ "$answer" = "YES" ] || { echo "Phase 3 voice install not approved" >&2; exit 1; }
    export PIP_CACHE_DIR="$ROOT/temp/pip-cache" TMPDIR="$ROOT/temp" PYTHONPYCACHEPREFIX="$ROOT/temp/pycache"
    export HF_HOME="$ROOT/temp/huggingface" XDG_CACHE_HOME="$ROOT/temp/xdg-cache" PYTHONPATH="$ROOT/vendor"
    "$PYTHON_BIN" -m pip --disable-pip-version-check install --target "$ROOT/vendor" -r "$ROOT/requirements-phase3.txt"
    "$PYTHON_BIN" "$ROOT/scripts/setup_voice_models.py"
    "$PYTHON_BIN" "$ROOT/primus.py" voice-doctor
fi
if [ "${INSTALL_BROWSER:-0}" = "1" ]; then
    printf 'Install pinned Playwright into %s/vendor from PyPI using existing Chrome? Type YES: ' "$ROOT"
    read -r answer
    [ "$answer" = "YES" ] || { echo "Browser dependency install not approved" >&2; exit 1; }
    PIP_CACHE_DIR="$ROOT/temp/pip-cache" TMPDIR="$ROOT/temp" PYTHONPYCACHEPREFIX="$ROOT/temp/pycache" \
        "$PYTHON_BIN" -m pip --disable-pip-version-check install --target "$ROOT/vendor" -r "$ROOT/requirements-browser.txt"
fi
"$PYTHON_BIN" "$ROOT/primus.py" doctor
