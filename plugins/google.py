"""Read-only Gmail and Google Calendar tools, disabled by default."""
from __future__ import annotations
from datetime import datetime, timedelta
from urllib.parse import urlencode
from primus.google import enabled, google_get, token
from primus.plugins import Plugin

def gmail_today(config, args):
    enabled(config)
    local = datetime.now().astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    query = f"after:{int(start.timestamp())} before:{int(end.timestamp())}"
    access = token(config)
    base = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    listing = google_get(config, base + "?" + urlencode({"q": query, "maxResults": 10}), access_token=access)
    rows = []
    for item in listing.get("messages", [])[:10]:
        detail = google_get(config, base + "/" + item["id"] + "?format=metadata&metadataHeaders=Subject&metadataHeaders=From", access_token=access)
        headers = {h["name"].lower(): h["value"] for h in detail.get("payload", {}).get("headers", [])}
        rows.append({"id": item["id"], "subject": headers.get("subject", ""), "from": headers.get("from", ""),
                     "snippet": detail.get("snippet", "")})
    return {"date": start.date().isoformat(), "messages": rows}

def calendar_today(config, args):
    enabled(config)
    local = datetime.now().astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    url = "https://www.googleapis.com/calendar/v3/calendars/primary/events?" + urlencode({
        "timeMin": start.isoformat(), "timeMax": end.isoformat(),
        "singleEvents": "true", "orderBy": "startTime", "maxResults": 30})
    data = google_get(config, url, access_token=token(config))
    return {"events": [{"summary": e.get("summary", ""), "start": e.get("start"),
                        "end": e.get("end")} for e in data.get("items", [])]}

EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
PLUGINS = [
    Plugin("gmail_today", "Read today's Gmail subjects, senders, and snippets after network confirmation",
           EMPTY, "EXTERNAL", True, gmail_today),
    Plugin("calendar_today", "Read today's Google Calendar events after network confirmation",
           EMPTY, "EXTERNAL", True, calendar_today),
]
