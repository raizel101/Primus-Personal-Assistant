from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from primus.gesture_actions import COMMANDS,GestureActionGate,dispatch


class GestureActionsTests(unittest.TestCase):
    def feed(self,gate,label,start,end,**kwargs):
        return [gate.update(label,t,enabled=True,**kwargs) for t in range(start,end+1,100)]

    def test_sustained_pose_fires_once_then_requires_release(self):
        gate=GestureActionGate()
        results=self.feed(gate,'thumb_up',0,6000)
        self.assertEqual([r for r in results if r],['thumb_up'])
        self.assertFalse(any(self.feed(gate,'victory',6100,8000)))
        self.feed(gate,'no_hand',8100,9200)
        self.assertIn('victory',self.feed(gate,'victory',9300,10400))

    def test_disabled_busy_and_unmapped_poses_do_not_trigger(self):
        gate=GestureActionGate()
        for stamp in range(0,2000,100):
            self.assertIsNone(gate.update('thumb_up',stamp,enabled=False))
        self.assertFalse(any(self.feed(gate,'thumb_up',2000,4000)))
        self.feed(gate,'no_hand',4100,5200)
        self.assertFalse(any(self.feed(gate,'open_palm',5300,7000)))
        self.assertFalse(any(self.feed(gate,'victory',7100,9000,busy=True)))

    def test_cooldown_and_frame_gaps_do_not_retrigger(self):
        gate=GestureActionGate()
        self.assertIn('pointing',self.feed(gate,'pointing',0,1000))
        self.feed(gate,'no_hand',1100,2200)
        self.assertFalse(any(self.feed(gate,'pointing',2300,4000)))
        gate=GestureActionGate();gate.armed=False
        gate.update('no_hand',0,enabled=True)
        gate.update('no_hand',2000,enabled=True)
        self.assertFalse(gate.armed,'Missing frames must not count as hand release time')

    def test_dispatch_uses_real_api_consent_path_without_approval_callback(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'temp') as tmp:
            root=Path(tmp);(root/'logs').mkdir();(root/'data').mkdir()
            with patch('primus.dashboard.api_request',return_value={'result':'Opened https://www.youtube.com/ in Google Chrome (PRIMUS profile).'}) as api:
                result=dispatch({'_root':root},'thumb_up')
                self.assertIn('youtube.com',result)
                api.assert_called_once_with({'_root':root},'/tasks',{'task':'open YouTube'},timeout=100)
            (root/'data/STOP').touch()
            with patch('primus.dashboard.api_request') as api:
                with self.assertRaises(RuntimeError):dispatch({'_root':root},'victory')
                api.assert_not_called()

    def test_unknown_mapping_and_failed_request_no_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'temp') as tmp:
            root=Path(tmp);(root/'logs').mkdir()
            with self.assertRaises(ValueError):dispatch({'_root':root},'closed_fist')
            with patch('primus.dashboard.api_request',side_effect=TimeoutError) as api:
                with self.assertRaises(TimeoutError):dispatch({'_root':root},'pointing')
                self.assertEqual(api.call_count,1)


if __name__=='__main__':unittest.main()
