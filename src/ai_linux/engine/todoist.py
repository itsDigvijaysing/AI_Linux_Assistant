"""Todoist through its API v1 (https://developer.todoist.com/api/v1/): add with Quick Add, list by filter, close.
Standard library only; every call has a timeout. The token comes from the app (the user's keyring), never a file.

Quick Add reads dates, projects and labels from the sentence itself ("buy milk tomorrow at 5"), so the local
model only has to pick the tool, not fill a date.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.todoist.com/api/v1"
TIMEOUT = 8
UA = "Phoenix-assistant/3 (local desktop assistant)"
WHEN = {"today": "today | overdue", "overdue": "overdue", "tomorrow": "tomorrow", "week": "7 days"}


class TodoistError(Exception):
    pass


class TokenRejected(TodoistError):
    pass


def _call(token: str, method: str, path: str, *, params: dict | None = None, body: dict | None = None,
          base: str | None = None) -> dict | None:
    url = f"{base or API}/{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310  fixed https host
            raw = r.read(2_000_000)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise TokenRejected(f"HTTP {exc.code}") from exc
        raise TodoistError(f"HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:  # no network, timeout, bad URL
        raise TodoistError(str(exc) or type(exc).__name__) from exc
    try:
        return json.loads(raw) if raw.strip() else None
    except ValueError as exc:
        raise TodoistError("Todoist sent a reply that is not JSON") from exc


def quick_add(token: str, text: str, base: str | None = None) -> dict:
    """The new task (content, due) from a natural sentence."""
    return _call(token, "POST", "tasks/quick", body={"text": text}, base=base) or {}


def tasks(token: str, query: str, limit: int = 50, base: str | None = None) -> list[dict]:
    """Active tasks matching a Todoist filter ("today | overdue", "search: milk"); one page is plenty to speak."""
    page = _call(token, "GET", "tasks/filter", params={"query": query, "limit": limit}, base=base) or {}
    return [t for t in page.get("results") or [] if isinstance(t, dict) and t.get("id")]


def close(token: str, task_id: str, base: str | None = None) -> None:
    _call(token, "POST", f"tasks/{urllib.parse.quote(str(task_id), safe='')}/close", base=base)


def check(token: str, base: str | None = None) -> int:
    """Whether the token works (TokenRejected if not); returns how many tasks are due today or overdue."""
    return len(tasks(token, WHEN["today"], base=base))
