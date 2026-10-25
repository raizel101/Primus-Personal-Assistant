"""OS-keychain storage for the local API token and optional connector credentials."""
from __future__ import annotations
import secrets
import hashlib
import hmac

SERVICE = "PRIMUS-local"

def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Install the approved keyring dependency into PRIMUS/vendor first") from exc
    return keyring

def get_secret(name: str) -> str | None:
    return _keyring().get_password(SERVICE, name)

def put_secret(name: str, value: str) -> None:
    _keyring().set_password(SERVICE, name, value)

def api_token(*, create: bool = False) -> str:
    token = get_secret("api-token")
    if not token and create:
        token = secrets.token_urlsafe(48)
        put_secret("api-token", token)
    if not token:
        raise RuntimeError("Local API token is missing; run 'primus.py api-token-init' interactively")
    return token

def local_session_id() -> str:
    """Stable private session across local voice and tray turns; no transcript on disk."""
    return hmac.new(api_token().encode("utf-8"), b"PRIMUS-local-session",
                    hashlib.sha256).hexdigest()[:32]
