"""Chrome control through a private Playwright pipe and PRIMUS-only profile."""
from __future__ import annotations

import queue
import threading
from pathlib import Path
from urllib.parse import urlsplit


class BrowserError(RuntimeError):
    pass


def _safe_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise BrowserError("A URL of at most 2048 characters is required")
    if url == "about:blank":
        return url
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise BrowserError("Only HTTPS URLs without embedded credentials are allowed")
    return url


def _selector(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise BrowserError("A CSS selector of at most 200 characters is required")
    if value.strip().lower() in {"*", "body", "html", ":root"}:
        raise BrowserError("Choose a specific visible element")
    return "css=" + value


class BrowserController:
    def __init__(self, root: Path):
        self.root = root
        self.requests: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._worker, daemon=True, name="primus-browser")
        self.thread.start()

    def call(self, action: str, **kwargs) -> dict:
        response: queue.Queue = queue.Queue(maxsize=1)
        self.requests.put((action, kwargs, response))
        try:
            ok, value = response.get(timeout=45)
        except queue.Empty as exc:
            raise BrowserError("Browser did not respond within 45 seconds") from exc
        if not ok:
            raise BrowserError(str(value))
        return value

    def _worker(self):
        context = None
        playwright = None
        closed = threading.Event()
        try:
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            profile = self.root / "data" / "browser-automation"
            profile.mkdir(parents=True, exist_ok=True)
            while True:
                action, kwargs, response = self.requests.get()
                try:
                    if (self.root / "data" / "STOP").exists():
                        raise BrowserError("Global kill switch is active")
                    # A user may close the controlled Chrome window between
                    # requests. Reopen only for the next explicit open/navigate
                    # request; never retry a click, fill or failed navigation.
                    disconnected = context is not None and context.browser is not None and not context.browser.is_connected()
                    if context is None or closed.is_set() or disconnected:
                        if action not in {"open", "navigate"}:
                            raise BrowserError("PRIMUS Chrome is closed. Request opening a website again and approve that exact action.")
                        context = playwright.chromium.launch_persistent_context(
                            user_data_dir=str(profile), channel="chrome", headless=False,
                            accept_downloads=False, service_workers="block", timeout=20000,
                            args=["--no-first-run", "--disable-background-networking"],
                        )
                        closed.clear()
                        context.on("close", lambda *_: closed.set())
                        context.set_default_timeout(10000)
                    page = context.pages[-1] if context.pages else context.new_page()
                    if action == "open":
                        result = {"url": page.url, "title": page.title(), "profile": str(profile)}
                    elif action == "navigate":
                        url = _safe_url(kwargs["url"])
                        page.goto(url, wait_until="domcontentloaded", timeout=20000)
                        result = {"url": page.url, "title": page.title()}
                    elif action == "read":
                        result = {"url": page.url, "title": page.title(),
                                  "text": page.locator("body").inner_text(timeout=10000)[:6000]}
                    elif action in {"preview", "click", "type"}:
                        locator = page.locator(_selector(kwargs["selector"]))
                        count = locator.count()
                        if count != 1 or not locator.is_visible():
                            raise BrowserError(f"Selector must match exactly one visible element; matched {count}")
                        kind = (locator.get_attribute("type") or "").lower()
                        if kind in {"password", "file", "hidden"}:
                            raise BrowserError("Password, file, and hidden inputs are blocked")
                        tag = locator.evaluate("el => el.tagName.toLowerCase()")
                        editable = locator.get_attribute("contenteditable") == "true"
                        preview = {"url": page.url, "selector": kwargs["selector"],
                                   "text": locator.inner_text(timeout=2000)[:120] if kind not in {"text", "search", "email", "number"} else "",
                                   "href": locator.get_attribute("href"), "input_type": kind,
                                   "tag": tag, "contenteditable": editable,
                                   "name": locator.get_attribute("name"), "id": locator.get_attribute("id"),
                                   "placeholder": locator.get_attribute("placeholder")}
                        if action == "preview":
                            result = preview
                        elif action == "click":
                            if preview != kwargs.get("expected"):
                                raise BrowserError("The browser element changed after approval; ask again")
                            locator.click(timeout=10000)
                            result = {"clicked": preview, "url_after": page.url}
                        else:
                            if preview != kwargs.get("expected"):
                                raise BrowserError("The browser field changed after approval; ask again")
                            if not (tag == "textarea" or editable or (tag == "input" and kind in {"text", "search", "email", "number", ""})):
                                raise BrowserError("Target is not a supported text field")
                            locator.fill(kwargs["text"], timeout=10000)
                            result = {"filled": preview, "characters": len(kwargs["text"]), "url_after": page.url}
                    else:
                        raise BrowserError("Unknown browser action")
                    response.put((True, result))
                except Exception as exc:
                    response.put((False, exc))
        except Exception as exc:
            while True:
                action, kwargs, response = self.requests.get()
                response.put((False, exc))
        finally:
            if context is not None:
                context.close()
            if playwright is not None:
                playwright.stop()


_controllers: dict[Path, BrowserController] = {}
_lock = threading.Lock()


def operate(root: Path, action: str, **kwargs) -> dict:
    with _lock:
        controller = _controllers.get(root)
        if controller is None:
            controller = BrowserController(root)
            _controllers[root] = controller
    return controller.call(action, **kwargs)
