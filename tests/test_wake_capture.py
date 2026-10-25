"""Regression tests for capture gaps during wake decoding and playback isolation."""
from pathlib import Path
import json
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'src'))
from primus.voice import SpeechInput, WakeListener, _run_voice


class FakeMicrophone:
    def __init__(self, frames):
        self.frames=list(frames)
        self.active=False
        self.all_captured=threading.Event()
        self.closed=threading.Event()
    def __enter__(self):
        self.active=True
        return self
    def __exit__(self,*_):
        self.active=False
        self.closed.set()
    def utterance(self,timeout,*,cancel_event):
        if self.frames:
            value=self.frames.pop(0)
            if not self.frames:self.all_captured.set()
            return value
        cancel_event.wait(.05)
        return None


class WakeCaptureTests(unittest.TestCase):
    def test_captures_second_utterance_while_consumer_is_busy(self):
        mic=FakeMicrophone([b'first',b'second'])
        with WakeListener(mic) as listener:
            self.assertEqual(listener.get(),b'first')
            self.assertTrue(mic.all_captured.wait(1),'Capture must not wait for a recognition result')
            self.assertTrue(mic.active)
            self.assertEqual(listener.get(),b'second')
        self.assertTrue(mic.closed.is_set())
        self.assertFalse(listener.worker.is_alive())

    def test_queue_is_bounded_recent_and_discarded_on_exit(self):
        mic=FakeMicrophone([b'one',b'two',b'three',b'four'])
        with WakeListener(mic) as listener:
            self.assertTrue(mic.all_captured.wait(1))
            # The event is set inside capture immediately before enqueuing four.
            # Acquire the thread's next wait to avoid a producer/consumer race.
            for _ in range(100):
                with listener.pending.mutex:
                    if list(listener.pending.queue)==[b'three',b'four']:break
                threading.Event().wait(.001)
            self.assertEqual(listener.pending.qsize(),2)
            self.assertEqual(listener.get(),b'three')
            self.assertEqual(listener.get(),b'four')
        self.assertTrue(listener.pending.empty())
        self.assertFalse(mic.active)

    def test_capture_device_failure_propagates_and_closes(self):
        class Broken(FakeMicrophone):
            def utterance(self,*args,**kwargs):raise OSError('device failed')
        mic=Broken([])
        with self.assertRaises(OSError):
            with WakeListener(mic) as listener:listener.get()
        self.assertTrue(mic.closed.is_set())

    def test_capture_start_failure_does_not_hang(self):
        class NoDevice(FakeMicrophone):
            def __enter__(self):raise OSError('no microphone')
        with self.assertRaises(OSError):
            with WakeListener(NoDevice([])):pass

    def test_input_cancel_returns_without_reading_device(self):
        mic=SpeechInput(None,{'_root':PROJECT,'voice':{'energy_threshold':500,'silence_seconds':.5}})
        stop=threading.Event();stop.set()
        self.assertIsNone(mic.utterance(1,cancel_event=stop))

    def test_single_click_rejected_and_spoken_duration_kept(self):
        def capture(levels):
            mic=SpeechInput(None,{'_root':PROJECT,'voice':{'energy_threshold':500,'silence_seconds':.5}})
            clock=[0.0]
            class Stream:
                def read(self,_):
                    clock[0]+=.1
                    level=levels.pop(0) if levels else 0
                    return level.to_bytes(2,'little',signed=True)*1600,False
            mic.stream=Stream()
            with patch('primus.voice.time.monotonic',side_effect=lambda:clock[0]):
                return mic.utterance(2)
        self.assertIsNone(capture([0,1000,0,0,0,0,0,0]))
        self.assertIsNotNone(capture([0,1000,1000,1000,0,0,0,0,0,0]))

    def test_voice_loop_keeps_capture_during_checks_and_closes_before_tts(self):
        with tempfile.TemporaryDirectory(dir=PROJECT/'temp') as temporary:
            root=Path(temporary);(root/'data').mkdir()
            mic=FakeMicrophone([b'noise',b'wake'])
            checks=[];played=[]
            def checked(_speech,pcm):
                self.assertTrue(mic.active,'Old loop closed the mic during decode')
                self.assertTrue(mic.all_captured.wait(1))
                checks.append(pcm)
                return (True,'exit') if pcm==b'wake' else (False,'')
            def speak(text,_config):
                self.assertFalse(mic.active,'TTS must not feed the capture thread')
                played.append(text)
            speech=SimpleNamespace(input=mic,stt_device='test',speak=speak)
            client=SimpleNamespace(request=lambda _: {'root':str(root)})
            with patch('primus.voice.voice_status',return_value={'ready':True}),patch('primus.voice.LocalSpeech',return_value=speech),patch('primus.tray._start_local_core',return_value=(None,None,None)),patch('primus.voice.VoiceClient',return_value=client),patch('primus.voice.confirmed_wake',side_effect=checked),patch('primus.voice._console'):
                _run_voice({'_root':root,'voice':{}})
            self.assertEqual(checks,[b'noise',b'wake'])
            self.assertEqual(played,['Voice mode stopped.'])
            state=json.loads((root/'data/voice-status.json').read_text())
            self.assertEqual(state['wake_checks'],2)
            self.assertEqual(state['wake_accepts'],1)
            self.assertFalse(state['capture_active'])
            self.assertNotIn('transcript',state)


if __name__=='__main__':unittest.main()
