"""Local wake diagnostic: no saved audio/transcripts, model download or task dispatch."""
from pathlib import Path
import json
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "src"))
from primus.config import load_config
from primus.voice import SpeechInput, voice_paths, wake_command


def category(text):
    import re
    if wake_command(text)[0]:
        return "exact_wake_at_start"
    if not text.strip():
        return "empty_decode"
    if re.search(r"\bprimus\b", text, re.I):
        return "exact_name_later_in_utterance"
    if re.search(r"\b(?:prime\s+us|primis|premus|primas|premise|primers|promise)\b", text, re.I):
        return "possible_name_misrecognition"
    return "other_speech"


def main():
    import numpy as np
    import sounddevice as sd
    from faster_whisper import WhisperModel
    config = load_config(ROOT / "config.json")
    stt, _ = voice_paths(config)
    model = WhisperModel(str(stt), device="cuda", compute_type="float16")
    # One silent encode warms the local model before the diagnostic window.
    list(model.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1, vad_filter=False)[0])
    print("Diagnostic ready. Listening for up to 25 seconds. Audio remains in memory.", flush=True)
    captures=[]
    started=time.monotonic()
    with SpeechInput(sd, config) as microphone:
        while time.monotonic()-started < 25 and len(captures)<30:
            remaining=25-(time.monotonic()-started)
            pcm=microphone.utterance(timeout=max(.1,remaining))
            if pcm is None:
                continue
            samples=np.frombuffer(pcm,dtype=np.int16).astype(np.float32)/32768.0
            decoded=[]
            for beam in (1,5):
                segments,_=model.transcribe(samples,language="en",beam_size=beam,condition_on_previous_text=False,vad_filter=False)
                text=" ".join(s.text.strip() for s in segments)
                decoded.append({"beam_size":beam,"wake_accepted":wake_command(text)[0],"category":category(text)})
            captures.append({"seconds":round(len(pcm)/32000,2),"rms":round(SpeechInput.energy(pcm),1),"noise_floor":round(microphone.noise_floor or 0,1),"trigger_threshold":round(max(config['voice']['energy_threshold'],(microphone.noise_floor or 0)*2.5),1),"decodes":decoded})
    result={"speech_model_directory":str(stt),"selected_input_unchanged":config['voice'].get('input_device'),"captures":captures,"audio_saved":False,"transcripts_saved":False,"tasks_dispatched":0}
    (ROOT/'outputs/wake-diagnostic.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
