"""Local, turn-based hands-free speech interface for PRIMUS."""
from __future__ import annotations

import array
import json
import math
import os
import queue
import re
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .auth import api_token, local_session_id
from .process_lock import ProcessLock


WAKE_PATTERN = re.compile(
    r"^\s*(?:(?:(?:hey|okay|ok)[, ]+)?primus|(?:hey|okay|ok)[, ]+premus)\b",
    re.IGNORECASE,
)


def wake_command(transcript: str) -> tuple[bool, str]:
    """Return whether a transcript addresses PRIMUS and any following command."""
    match = WAKE_PATTERN.search(transcript)
    if not match:
        return False, ""
    return True, transcript[match.end():].strip(" ,.!?:;-\t\n")


def confirmed_wake(speech, pcm: bytes) -> tuple[bool, str]:
    """Require both unprompted decodes to accept the narrowly matched wake phrase."""
    first_awake, first_command = wake_command(speech.transcribe(pcm, beam_size=1))
    if not first_awake:
        return False, ""
    second_awake, second_command = wake_command(speech.transcribe(pcm, beam_size=5))
    if not second_awake:
        return False, ""
    # A one-turn command is safe to dispatch only if both decodes heard one.
    return True, second_command if first_command and second_command else ""


def inside_root(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Voice model paths must stay inside the PRIMUS root")
    return path


def voice_paths(config: dict) -> tuple[Path, Path]:
    settings = config["voice"]
    root = config["_root"]
    return (inside_root(root, settings["stt_model_dir"]),
            inside_root(root, settings["tts_model"]))


def voice_status(config: dict) -> dict:
    stt, tts = voice_paths(config)
    try:
        import sounddevice  # noqa: F401
        import faster_whisper  # noqa: F401
        import piper  # noqa: F401
        packages = True
    except ImportError:
        packages = False
    return {"packages": packages, "stt_model": all((stt / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")),
            "tts_model": tts.is_file() and tts.with_suffix(tts.suffix + ".json").is_file()}


class SpeechInput:
    """Capture complete utterances as 16 kHz mono PCM in memory."""
    def __init__(self, sounddevice, config: dict):
        self.sd = sounddevice
        self.config = config
        self.rate = 16000
        self.block = 1600
        self.stream = None
        self.noise_floor: float | None = None
        self.prefetched: list[bytes] = []

    @staticmethod
    def energy(pcm: bytes) -> float:
        samples = array.array("h")
        samples.frombytes(pcm)
        return math.sqrt(sum(sample * sample for sample in samples) / max(len(samples), 1))

    def __enter__(self):
        self.stream = self.sd.RawInputStream(samplerate=self.rate, channels=1,
                                             dtype="int16", blocksize=self.block,
                                             device=self.config["voice"].get("input_device"))
        self.stream.start()
        if self.noise_floor is None:
            # Calibrate once, but replay these frames so an immediate wake phrase is not lost.
            self.prefetched = [bytes(self.stream.read(self.block)[0]) for _ in range(4)]
            base = float(self.config["voice"]["energy_threshold"])
            self.noise_floor = min(statistics.median(self.energy(pcm) for pcm in self.prefetched), base * 2)
        return self

    def __exit__(self, *_):
        self.stream.stop()
        self.stream.close()
        self.stream = None

    def utterance(self, timeout: float, *, cancel_event=None) -> bytes | None:
        settings = self.config["voice"]
        minimum_threshold = int(settings["energy_threshold"])
        silence_limit = float(settings["silence_seconds"])
        start = time.monotonic()
        last_voice = None
        frames: list[bytes] = []
        preroll: list[bytes] = []
        voiced_blocks = 0
        while time.monotonic() - start < timeout:
            if cancel_event is not None and cancel_event.is_set():
                return None
            if (self.config["_root"] / "data" / "STOP").exists():
                raise RuntimeError("Global kill switch is active")
            if self.prefetched:
                data, overflowed = self.prefetched.pop(0), False
            else:
                data, overflowed = self.stream.read(self.block)
            if overflowed:
                frames.clear()
                preroll.clear()
                last_voice = None
                voiced_blocks = 0
                continue
            pcm = bytes(data)
            energy = self.energy(pcm)
            threshold = max(minimum_threshold, (self.noise_floor or 0) * 2.5)
            now = time.monotonic()
            if last_voice is None:
                preroll.append(pcm)
                preroll = preroll[-3:]
                if energy >= threshold:
                    frames.extend(preroll)
                    last_voice = now
                    voiced_blocks += 1
                else:
                    self.noise_floor = (self.noise_floor or energy) * 0.98 + energy * 0.02
            else:
                frames.append(pcm)
                if energy >= threshold:
                    last_voice = now
                    voiced_blocks += 1
                elif now - last_voice >= silence_limit:
                    break
                if len(frames) >= 150:  # 15 seconds at 100 ms per block
                    break
        # A lone 100 ms click used to trigger an expensive wake decode and a
        # microphone-off interval. Require at least 200 ms above the threshold.
        if not frames or len(frames) < 3 or voiced_blocks < 2:
            return None
        return b"".join(frames)


class WakeListener:
    """Capture while the consumer decodes; bounded RAM only, close before TTS."""
    def __init__(self, microphone):
        self.microphone = microphone
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.pending = queue.Queue(maxsize=2)
        self.error = None
        self.worker = threading.Thread(target=self._capture, name="primus-wake-capture", daemon=True)

    def _capture(self):
        try:
            with self.microphone as source:
                self.ready.set()
                while not self.stop.is_set():
                    pcm = source.utterance(timeout=3, cancel_event=self.stop)
                    if pcm is None or self.stop.is_set():
                        continue
                    try:
                        self.pending.put_nowait(pcm)
                    except queue.Full:
                        # Keep recent utterances; never grow an audio backlog.
                        try:
                            self.pending.get_nowait()
                        except queue.Empty:
                            pass
                        self.pending.put_nowait(pcm)
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()

    def __enter__(self):
        self.worker.start()
        if not self.ready.wait(5) or self.error is not None:
            self.__exit__(None, None, None)
            if self.error is not None:
                raise self.error
            raise RuntimeError("Wake microphone did not become ready")
        return self

    def get(self):
        while not self.stop.is_set():
            if self.error is not None:
                raise self.error
            try:
                return self.pending.get(timeout=.1)
            except queue.Empty:
                if not self.worker.is_alive():
                    raise RuntimeError("Wake microphone stopped unexpectedly")
        raise RuntimeError("Wake capture stopped")

    def __exit__(self, *_):
        self.stop.set()
        self.worker.join(timeout=2)
        while True:
            try:
                self.pending.get_nowait()
            except queue.Empty:
                break
        if self.worker.is_alive():
            raise RuntimeError("Microphone did not stop; refusing speech playback")


class LocalSpeech:
    def __init__(self, config: dict):
        try:
            import sounddevice as sd
            from faster_whisper import WhisperModel
            from piper import PiperVoice
        except ImportError as exc:
            raise RuntimeError("Install Phase 3 voice packages into PRIMUS/vendor first") from exc
        stt, tts = voice_paths(config)
        if not (stt / "model.bin").is_file() or not tts.is_file() or not tts.with_suffix(tts.suffix + ".json").is_file():
            raise RuntimeError("Local voice models are missing; see README Phase 3 setup")
        self.sd = sd
        self.input = SpeechInput(sd, config)
        self.stt = None
        preference = config["voice"].get("stt_device", "auto")
        if preference != "cpu":
            try:
                import numpy as np
                candidate = WhisperModel(str(stt), device="cuda", compute_type="float16")
                # Some CUDA combinations load successfully but fail at the first encode.
                list(candidate.transcribe(np.zeros(16000, dtype=np.float32), language="en",
                                          beam_size=1, vad_filter=False)[0])
                self.stt = candidate
                self.stt_device = "cuda"
            except (RuntimeError, OSError, ImportError):
                if preference == "cuda":
                    raise
        if self.stt is None:
            self.stt = WhisperModel(str(stt), device="cpu", compute_type="int8")
            self.stt_device = "cpu"
        self.tts = PiperVoice.load(str(tts))

    def transcribe(self, pcm: bytes, *, beam_size: int = 1) -> str:
        import numpy as np
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self.stt.transcribe(samples, language="en", beam_size=beam_size,
                                           condition_on_previous_text=False,
                                           vad_filter=False)
        return " ".join(segment.text.strip() for segment in segments).strip()

    def speak(self, text: str, config: dict) -> None:
        # Close the microphone while speaking to prevent self-triggering.
        stream = None
        try:
            for chunk in self.tts.synthesize(text[:1200]):
                if (config["_root"] / "data" / "STOP").exists():
                    raise RuntimeError("Global kill switch is active")
                if stream is None:
                    stream = self.sd.RawOutputStream(dtype="int16", channels=chunk.sample_channels,
                                                     samplerate=chunk.sample_rate)
                    stream.start()
                stream.write(chunk.audio_int16_bytes)
        finally:
            if stream is not None:
                stream.stop()
                stream.close()


class VoiceClient:
    def __init__(self, config: dict):
        self.config = config
        self.base = f"http://127.0.0.1:{config['local_api']['port']}"
        self.session = local_session_id()

    def request(self, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(self.base + path, data=data,
                          headers={"Authorization": "Bearer " + api_token(),
                                   "Content-Type": "application/json"},
                          method="POST" if data is not None else "GET")
        with urlopen(request, timeout=180) as response:
            return json.load(response)

    def task(self, text: str) -> str:
        return self.request("/tasks", {"task": text, "session": self.session})["result"]


def write_voice_status(config: dict, state: str, *, error_type: str | None = None,
                       stt_device: str | None = None, capture_active: bool | None = None,
                       wake_checks: int | None = None, wake_accepts: int | None = None) -> None:
    """Write process state only; never persist microphone audio or transcripts."""
    root = config["_root"]
    target = root / "data" / "voice-status.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = root / "temp" / f"voice-status-{os.getpid()}.json"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    value = {"state": state, "pid": os.getpid(),
             "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if error_type:
        value["error_type"] = error_type
    if stt_device:
        value["stt_device"] = stt_device
    if capture_active is not None:
        value["capture_active"] = capture_active
    if wake_checks is not None:
        value["wake_checks"] = wake_checks
    if wake_accepts is not None:
        value["wake_accepts"] = wake_accepts
    temporary.write_text(json.dumps(value), encoding="utf-8")
    # Tk polls this file; a short-lived Windows reader can block atomic replace.
    for attempt in range(20):
        try:
            os.replace(temporary, target)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.02)


def _console(message: str) -> None:
    if sys.stdout is not None:
        try:
            print(message, flush=True)
        except UnicodeEncodeError:
            # Windows console/devnull encodings can reject model punctuation or
            # transcribed speech. Logging must not stop the microphone loop.
            encoding = getattr(sys.stdout, "encoding", None) or "ascii"
            print(message.encode(encoding, errors="replace").decode(encoding), flush=True)


def run_voice(config: dict) -> None:
    """Run at most one offline voice loop; record only minimal process state."""
    with ProcessLock(config["_root"] / "data" / "voice.lock"):
        write_voice_status(config, "starting")
        try:
            _run_voice(config)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)) or (config["_root"] / "data" / "STOP").exists():
                write_voice_status(config, "stopped")
            else:
                write_voice_status(config, "error", error_type=type(exc).__name__)
            raise
        else:
            write_voice_status(config, "stopped")


def _run_voice(config: dict) -> None:
    """Capture wake word and commands; use the authenticated local API."""
    from .tray import _start_local_core
    if (config["_root"] / "data" / "STOP").exists():
        raise RuntimeError("Global kill switch is active")
    status = voice_status(config)
    if not all(status.values()):
        raise RuntimeError(f"Voice setup incomplete: {status}. See README Phase 3 setup")
    speech = LocalSpeech(config)
    wake_checks, wake_accepts = 0, 0
    def update(state: str, *, capture_active=False) -> None:
        write_voice_status(config, state, stt_device=speech.stt_device,
                           capture_active=capture_active, wake_checks=wake_checks, wake_accepts=wake_accepts)
    server, stop, scheduler_thread = _start_local_core(config)
    client = VoiceClient(config)
    try:
        health = client.request("/health")
        if health.get("root") != str(config["_root"]):
            raise RuntimeError("Local API belongs to another PRIMUS root")
        _console("PRIMUS is listening for 'Primus'. Ctrl+C exits. Audio stays in memory.")
        while True:
            # Keep the microphone active during both unprompted decodes. The
            # context closes it and discards queued audio before any playback.
            with WakeListener(speech.input) as listener:
                update("listening", capture_active=True)
                while True:
                    pcm = listener.get()
                    wake_checks += 1
                    update("checking", capture_active=True)
                    awake, command = confirmed_wake(speech, pcm)
                    if awake:
                        wake_accepts += 1
                        break
                    update("listening", capture_active=True)
            update("heard")
            if not command:
                update("speaking")
                speech.speak("Yes?", config)
                update("heard")
                with speech.input as microphone:
                    update("heard", capture_active=True)
                    pcm = microphone.utterance(timeout=float(config["voice"]["command_timeout_seconds"]))
                update("heard")
                if pcm is None:
                    continue
                command = speech.transcribe(pcm)
            if not command:
                continue
            _console(f"You: {command}")
            if command.lower().strip(" .!?") in {"stop listening", "quit", "exit"}:
                update("speaking")
                speech.speak("Voice mode stopped.", config)
                return
            update("processing")
            try:
                response = client.task(command)
            except (URLError, TimeoutError, OSError, KeyError, ValueError) as exc:
                response = f"I couldn't complete that request: {exc}"
            _console(f"PRIMUS: {response}")
            update("speaking")
            speech.speak(response, config)
    except KeyboardInterrupt:
        _console("Voice mode stopped.")
    finally:
        if server:
            stop.set()
            server.shutdown()
            server.server_close()
            scheduler_thread.join(timeout=2)
