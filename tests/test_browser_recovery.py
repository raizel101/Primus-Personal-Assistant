"""Closed Chrome recovery only on a new explicit open/navigation request."""
from pathlib import Path
import io
import json
import queue
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from primus.browser import BrowserController
from primus.gesture_actions import dispatch


class BrowserRecoveryTests(unittest.TestCase):
    def test_new_navigation_reopens_closed_context_without_replaying_first_action(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'temp') as tmp:
            controller=BrowserController.__new__(BrowserController);controller.root=Path(tmp)
            contexts=[];calls=[]
            def launch(**kwargs):
                context=Mock();page=Mock();page.url='https://example.com/';page.title.return_value='Example'
                context.pages=[page];context.browser.is_connected.return_value=True
                def navigate(*args,**kwargs):
                    calls.append(args[0])
                    context.browser.is_connected.return_value=False
                page.goto.side_effect=navigate
                contexts.append(context)
                return context
            pw=Mock();pw.chromium.launch_persistent_context.side_effect=launch
            responses=[queue.Queue(),queue.Queue()]
            requests=[('navigate',{'url':'https://example.com/'},responses[0]),('navigate',{'url':'https://www.youtube.com/'},responses[1])]
            controller.requests=Mock();controller.requests.get.side_effect=[*requests,SystemExit()]
            module=types.SimpleNamespace(sync_playwright=lambda:types.SimpleNamespace(start=lambda:pw))
            with patch.dict(sys.modules,{'playwright.sync_api':module}):
                with self.assertRaises(SystemExit):controller._worker()
            self.assertEqual(len(contexts),2)
            self.assertEqual(calls,['https://example.com/','https://www.youtube.com/'])
            self.assertTrue(all(response.get()[0] for response in responses))

    def test_read_does_not_launch_browser_without_explicit_open(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'temp') as tmp:
            controller=BrowserController.__new__(BrowserController);controller.root=Path(tmp)
            response=queue.Queue();controller.requests=Mock();controller.requests.get.side_effect=[('read',{},response),SystemExit()]
            pw=Mock();module=types.SimpleNamespace(sync_playwright=lambda:types.SimpleNamespace(start=lambda:pw))
            with patch.dict(sys.modules,{'playwright.sync_api':module}):
                with self.assertRaises(SystemExit):controller._worker()
            self.assertFalse(response.get()[0]);pw.chromium.launch_persistent_context.assert_not_called()

    def test_gesture_displays_api_error_body_and_does_not_retry(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'temp') as tmp:
            root=Path(tmp);(root/'logs').mkdir()
            error=HTTPError('http://127.0.0.1:8765/tasks',400,'Bad Request',{},io.BytesIO(json.dumps({'error':'Chrome is closed; reopen it.'}).encode()))
            with patch('primus.dashboard.api_request',side_effect=error) as api:
                with self.assertRaisesRegex(RuntimeError,'Chrome is closed'):dispatch({'_root':root},'thumb_up')
                self.assertEqual(api.call_count,1)


if __name__=='__main__':unittest.main()
