"""Fixed gesture requests with release-to-rearm gating; gestures never approve actions."""
from .gestures import StableGesture

COMMANDS = {
    "thumb_up": ("Open YouTube", "open YouTube"),
    "victory": ("Open Google Chrome", "open Google Chrome"),
    "pointing": ("Open Google website", "open Google in Chrome"),
}


class GestureActionGate:
    def __init__(self):
        self.stable = StableGesture(hold_ms=1000, min_frames=4)
        self.armed = True
        self.absent_since = None
        self.last_trigger = None
        self.previous = None

    def update(self, label, timestamp, *, enabled, busy=False):
        if self.previous is not None and (timestamp < self.previous or timestamp-self.previous > 500):
            self.absent_since = None
        self.previous = timestamp
        if not enabled or busy:
            self.stable = StableGesture(hold_ms=1000, min_frames=4)
            self.armed = False
            self.absent_since = None
            return None
        if label == "no_hand":
            if self.absent_since is None:
                self.absent_since = timestamp
            if timestamp-self.absent_since >= 1000:
                self.armed = True
        else:
            self.absent_since = None
        held = self.stable.update(label,timestamp)
        if (self.armed and held in COMMANDS and
                (self.last_trigger is None or timestamp-self.last_trigger >= 5000)):
            self.armed = False
            self.last_trigger = timestamp
            return held
        return None


def dispatch(config, gesture):
    from .audit import append_audit
    from .dashboard import api_request
    if gesture not in COMMANDS:
        raise ValueError("Gesture has no configured action")
    if (config['_root']/'data/STOP').exists():
        raise RuntimeError("Global kill switch is active")
    task=COMMANDS[gesture][1]
    # The API owns the EXTERNAL approval dialog. No synthetic confirmation,
    # credentials in UI, arbitrary gesture commands or automatic retry.
    append_audit(config['_root'],'gesture_action_request',{'gesture':gesture,'command':task},{'requested':True,'approved_by_gesture':False})
    try:
        result=api_request(config,'/tasks',{'task':task},timeout=100)['result']
        append_audit(config['_root'],'gesture_action_result',{'gesture':gesture},{'ok':True,'summary':result})
        return result
    except Exception as exc:
        append_audit(config['_root'],'gesture_action_result',{'gesture':gesture},{'ok':False,'error':str(exc)})
        from urllib.error import HTTPError
        import json
        if isinstance(exc, HTTPError):
            try:
                detail=json.loads(exc.read(8192)).get('error','Request failed')
            except (ValueError,OSError):
                detail='PRIMUS rejected the request; check its action approval dialog.'
            raise RuntimeError(str(detail)[:1200]) from None
        raise
