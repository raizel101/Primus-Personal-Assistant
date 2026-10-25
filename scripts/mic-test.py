"""Console-only local mic/transcription diagnostic. No tasks or audio/transcript files."""
from pathlib import Path
import argparse
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vendor'))
sys.path.insert(0,str(ROOT/'src'))
os.environ['HF_HUB_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['HF_HOME']=str(ROOT/'temp/huggingface')
os.environ['XDG_CACHE_HOME']=str(ROOT/'temp/xdg-cache')
from primus.config import load_config
from primus.voice import SpeechInput,voice_paths,wake_command


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-devices',action='store_true')
    parser.add_argument('--device',type=int,help='Override mic index for this test only; config stays unchanged')
    parser.add_argument('--seconds',type=int,choices=range(2,11),default=4,metavar='2-10')
    parser.add_argument('--cpu',action='store_true',help='Use CPU instead of attempting GPU')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    import sounddevice as sd
    if args.list_devices:
        for i,d in enumerate(sd.query_devices()):
            if d['max_input_channels']>0:
                print(f"{i:2d}  {d['name']}  [host API {d['hostapi']}]")
        return
    import numpy as np
    from faster_whisper import WhisperModel
    config=load_config(ROOT/'config.json')
    device=args.device if args.device is not None else config['voice'].get('input_device')
    selected=sd.query_devices(device,'input')
    model_path,_=voice_paths(config)
    print('PRIMUS MICROPHONE TEST',flush=True)
    print('Microphone:',selected['name'],flush=True)
    print('Model:',model_path.name,flush=True)
    print('Only displays text in this terminal. No audio/transcripts saved. No task dispatch.',flush=True)
    print('The normal background assistant is separate; Stop Voice from its tray menu for an isolated test.',flush=True)
    print('Loading local speech model...',flush=True)
    model=None
    if not args.cpu:
        try:
            model=WhisperModel(str(model_path),device='cuda',compute_type='float16',local_files_only=True)
            list(model.transcribe(np.zeros(16000,dtype=np.float32),language='en',vad_filter=False)[0])
            engine='GPU'
        except (RuntimeError,OSError,ImportError):
            model=None
    if model is None:
        model=WhisperModel(str(model_path),device='cpu',compute_type='int8',local_files_only=True)
        engine='CPU'
    print('Recognition:',engine,flush=True)
    print('Speak during LISTENING. Decoding pauses this test capture. Ctrl+C stops.\n',flush=True)
    try:
        while True:
            print(f'LISTENING for {args.seconds} seconds — speak now.',flush=True)
            chunks=[];levels=[]
            with sd.RawInputStream(samplerate=16000,channels=1,dtype='int16',blocksize=1600,device=device) as stream:
                for i in range(args.seconds*10):
                    if (ROOT/'data/STOP').exists():raise RuntimeError('PRIMUS kill switch is active')
                    data,overflow=stream.read(1600)
                    if overflow:print('\nAudio overflow; this chunk may be incomplete.',flush=True)
                    pcm=bytes(data);chunks.append(pcm)
                    rms=SpeechInput.energy(pcm);levels.append(rms)
                    bar='#'*min(35,int(rms/120))
                    print(f'\rMIC [{bar:<35}] RMS {rms:7.1f}   {(i+1)/10:.1f}s',end='',flush=True)
            print('\nTRANSCRIBING locally...',flush=True)
            samples=np.frombuffer(b''.join(chunks),dtype=np.int16).astype(np.float32)/32768.0
            heard=[]
            for beam in (1,5):
                segments,_=model.transcribe(samples,language='en',beam_size=beam,condition_on_previous_text=False,vad_filter=False)
                text=' '.join(s.text.strip() for s in segments).strip()
                awake,_=wake_command(text);heard.append(awake)
                print(f'Pass {beam} heard: {text or "[no words recognized]"}',flush=True)
                print('  Wake phrase at start:', 'YES' if awake else 'NO',flush=True)
            print('Both wake checks accepted:', 'YES' if all(heard) else 'NO',flush=True)
            print(f'Peak RMS: {max(levels):.1f}; configured trigger floor: {config["voice"]["energy_threshold"]}',flush=True)
            print('This fixed-window test bypasses the normal speech-energy gate. Transcription can be wrong.\n',flush=True)
            if args.once:break
    except KeyboardInterrupt:
        print('\nMic test stopped; microphone released.',flush=True)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('\nMic test stopped.',flush=True)
    except Exception as exc:
        print(f'\nMic test error: {type(exc).__name__}: {exc}',flush=True)
        raise SystemExit(1)
