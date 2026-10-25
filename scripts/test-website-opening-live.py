"""Submit one website request; normal PRIMUS desktop consent is mandatory."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vendor'))
sys.path.insert(0,str(ROOT/'src'))
from primus.config import load_config
from primus.dashboard import api_request


def main():
    config=load_config(ROOT/'config.json')
    print('Submitting one open-example.com request. Review PRIMUS action approval and choose Approve once to allow the real network navigation.',flush=True)
    # No automatic confirmation or retry: the signed-in user must decide in the
    # existing API consent dialog. Connection loss can leave an unknown outcome.
    reply=api_request(config,'/tasks',{'task':'open https://example.com/ in Google Chrome'},timeout=100)
    result=reply['result']
    expected='Opened https://example.com/ in Google Chrome (PRIMUS profile).'
    visible=[]
    if sys.platform=='win32':
        user=ctypes.WinDLL('user32',use_last_error=True)
        user.IsWindowVisible.argtypes=[wintypes.HWND]
        user.GetWindowTextLengthW.argtypes=[wintypes.HWND]
        user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        @callback
        def visit(hwnd,_):
            n=user.GetWindowTextLengthW(hwnd)
            if n and user.IsWindowVisible(hwnd):
                title=ctypes.create_unicode_buffer(n+1);user.GetWindowTextW(hwnd,title,n+1)
                if title.value.startswith('Example Domain') and 'Google Chrome' in title.value:
                    visible.append(title.value)
            return True
        user.EnumWindows(visit,0)
    report={'request':'open https://example.com/ in Google Chrome','normal_desktop_approval_required':True,
            'actual_api_result':result,'navigation_result_matches':result==expected,
            'matching_visible_chrome_titles':visible,'no_automatic_approval_or_retry':True}
    out=ROOT/'outputs/website-opening-live-verification.json'
    out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if result!=expected:raise RuntimeError('Live navigation was not verified; inspect the actual result')


if __name__=='__main__':main()
