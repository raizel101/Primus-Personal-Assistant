"""Explicitly approved local STT model download; no config or startup mutation."""
from pathlib import Path
import hashlib
import json
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vendor'))
for key,value in {
    'TEMP':ROOT/'temp', 'TMP':ROOT/'temp', 'HF_HOME':ROOT/'temp/huggingface',
    'HF_HUB_CACHE':ROOT/'temp/huggingface/hub', 'HF_XET_CACHE':ROOT/'temp/huggingface/xet',
    'XDG_CACHE_HOME':ROOT/'temp/xdg-cache', 'PYTHONPYCACHEPREFIX':ROOT/'temp/pycache',
}.items():os.environ[key]=str(value)
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['HF_HUB_DISABLE_XET']='1'


def main():
    from huggingface_hub import HfApi,snapshot_download
    import numpy as np
    from faster_whisper import WhisperModel
    repo='Systran/faster-whisper-base.en'
    info=HfApi(token=False).model_info(repo,files_metadata=True)
    target=ROOT/'models/stt/faster-whisper-base.en'
    wanted=['config.json','model.bin','tokenizer.json','vocabulary.txt','README.md']
    snapshot_download(repo_id=repo,revision=info.sha,token=False,local_dir=str(target),
                      cache_dir=str(ROOT/'temp/huggingface/hub'),allow_patterns=wanted,max_workers=2)
    model_row=next(x for x in info.siblings if x.rfilename=='model.bin')
    actual=hashlib.sha256((target/'model.bin').read_bytes()).hexdigest()
    expected=model_row.lfs.sha256
    if actual!=expected:raise RuntimeError('Downloaded speech model SHA-256 mismatch')
    model=WhisperModel(str(target),device='cuda',compute_type='float16')
    list(model.transcribe(np.zeros(16000,dtype=np.float32),language='en',beam_size=1,vad_filter=False)[0])
    report={'repo':repo,'revision':info.sha,'directory':str(target),'model_sha256':actual,
            'publisher_lfs_hash_matched':True,'local_cuda_probe_passed':True,
            'files':{name:{'bytes':(target/name).stat().st_size,'sha256':hashlib.sha256((target/name).read_bytes()).hexdigest()} for name in wanted},
            'config_changed':False,'packages_installed':False,'audio_saved':False}
    out=ROOT/'outputs/base-speech-model-setup.json'
    out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'model_ready':str(target),'revision':info.sha,'model_hash_matched':True,'cuda_probe_passed':True,'report':str(out)},indent=2))


if __name__=='__main__':main()
