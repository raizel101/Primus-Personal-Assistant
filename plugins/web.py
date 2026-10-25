"""Disabled-by-default public web fetch and Brave search."""
from __future__ import annotations
from html.parser import HTMLParser
from urllib.parse import urlencode
from primus.auth import get_secret
from primus.net import fetch, json_get
from primus.plugins import Plugin

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

def web_fetch(config, args):
    _, body, mime = fetch(args["url"])
    if "html" in mime:
        parser = TextExtractor()
        parser.feed(body.decode("utf-8", "replace"))
        content = " ".join(" ".join(parser.parts).split())
    else:
        content = body.decode("utf-8", "replace")
    return {"url": args["url"], "content": content[:12000], "truncated": len(content) > 12000}

def web_search(config, args):
    if config.get("search", {}).get("provider") != "brave":
        raise ValueError("Only Brave search is configured")
    key = get_secret("brave-api-key")
    if not key:
        raise RuntimeError("Brave API key missing from OS keychain")
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": args["query"], "count": 5})
    data = json_get(url, headers={"X-Subscription-Token": key, "Accept": "application/json"})
    return {"results": [{"title": row.get("title"), "url": row.get("url"), "description": row.get("description")}
                        for row in data.get("web", {}).get("results", [])[:5]]}

PLUGINS = [
    Plugin("web_fetch", "Fetch a public HTTPS page; sends the URL over the network after confirmation",
           {"type": "object", "properties": {"url": {"type": "string", "minLength": 9, "maxLength": 2048}},
            "required": ["url"], "additionalProperties": False}, "EXTERNAL", True, web_fetch),
    Plugin("web_search", "Search Brave; sends the query over the network after confirmation",
           {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300}},
            "required": ["query"], "additionalProperties": False}, "EXTERNAL", True, web_search),
]
