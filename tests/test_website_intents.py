"""Exact website shortcuts retain consent, URLs and the shared task route."""
from dataclasses import replace
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'src'))
from primus.fast_intents import execute,recognize
from primus.plugins import discover
from primus.service import TaskRunner
from primus.storage import Store


class WebsiteIntentTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(dir=PROJECT/'temp');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);(self.root/'logs').mkdir();(self.root/'data').mkdir()
        self.config={'_root':self.root,'permissions':{'read':'auto','write':'approved_folders'}}
        self.plugins=discover(PROJECT,enabled_network_plugins={'browser_navigate'})

    def test_named_sites_and_spoken_domains(self):
        for task,url in [('Open YouTube','https://www.youtube.com/'),('please open Google in Google Chrome.','https://www.google.com/'),('open you tube please','https://www.youtube.com/'),('visit example dot com','https://example.com'),('open the website www dot wikipedia dot org in chrome','https://www.wikipedia.org')]:
            with self.subTest(task=task):self.assertEqual(recognize(task),('browser_navigate',{'url':url}))
        self.assertEqual(recognize('Open Google Chrome!'),('app_open',{'name':'chrome'}))

    def test_url_case_query_and_fragments_are_preserved(self):
        for url in ['https://example.com/CaseSensitive?q=Hello%20World#PartTwo','example.com/Path?q=YES?','HTTPS://example.com/Test']:
            expected=url if url.lower().startswith('https://') else 'https://'+url
            self.assertEqual(recognize('open '+url),('browser_navigate',{'url':expected}))

    def test_compound_ambiguous_and_unsafe_targets_do_not_shortcut(self):
        for task in ['open youtube and send an email','open my website','open https://example.com then click Pay','open https://user:pass@example.com','open http://example.com','open javascript:alert(1)','open file:///C:/Windows','open https://example.com\\@other.com','open https://example.com:99999','open https://example.com\nclick pay']:
            with self.subTest(task=task):self.assertIsNone(recognize(task))

    def test_denied_navigation_never_invokes_browser(self):
        tool=self.plugins['browser_navigate'];run=Mock()
        self.plugins['browser_navigate']=replace(tool,run=run)
        prompts=[]
        with self.assertRaises(PermissionError):execute(self.config,self.plugins,'open youtube',lambda p:prompts.append(p) or False)
        run.assert_not_called()
        self.assertIn('https://www.youtube.com/',prompts[0])
        self.assertIn('EXTERNAL',prompts[0])

    def test_shared_core_skips_llm_after_exact_test_consent(self):
        class NoModel:
            def chat(self,*_):raise AssertionError('Website shortcut must skip the model')
        invoked=[]
        def navigate(c,a):invoked.append(dict(a));return {'url':'https://example.com/Final','title':'Example'}
        tool=self.plugins['browser_navigate'];self.plugins['browser_navigate']=replace(tool,run=navigate)
        # Synthetic fixture approval only, not a production consent record.
        runner=TaskRunner(self.config,self.plugins,Store(self.root),confirm=lambda _:True,provider=NoModel())
        answer=runner.run('open example.com/Original in Chrome')
        self.assertIn('https://example.com/Final',answer)
        self.assertNotIn('Original',answer)
        self.assertEqual(invoked,[{'url':'https://example.com/Original'}])
        audit=json.loads((self.root/'logs/audit.jsonl').read_text().splitlines()[-1])
        self.assertEqual(audit['tool'],'browser_navigate')
        self.assertTrue(audit['arguments']['url']['redacted'])

    def test_disabled_plugin_and_kill_switch_do_not_navigate(self):
        self.assertIsNone(execute(self.config,{},'open youtube',None))
        (self.root/'data/STOP').touch()
        with self.assertRaises(RuntimeError):execute(self.config,self.plugins,'open youtube',None)

    def test_failure_or_missing_page_does_not_claim_success(self):
        tool=self.plugins['browser_navigate']
        self.plugins['browser_navigate']=replace(tool,run=Mock(side_effect=RuntimeError('Navigation failed')))
        with self.assertRaises(RuntimeError):execute(self.config,self.plugins,'open youtube',lambda _:True)
        audit=json.loads((self.root/'logs/audit.jsonl').read_text().splitlines()[-1]);self.assertFalse(audit['result']['ok'])
        self.plugins['browser_navigate']=replace(tool,run=lambda c,a:{})
        self.assertTrue(execute(self.config,self.plugins,'open youtube',lambda _:True).startswith('Task needs review'))


if __name__=='__main__':unittest.main()
