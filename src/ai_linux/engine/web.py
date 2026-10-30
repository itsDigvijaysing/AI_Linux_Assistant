"""Web Access tools' network side: weather from Open-Meteo (free, no key) and short answers from DuckDuckGo's
Instant Answer API with a Wikipedia summary as the fallback. Standard library only; every call has a timeout."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

TIMEOUT = 6
UA = "Phoenix-assistant/3 (local desktop assistant)"

# WMO weather interpretation codes (open-meteo.com/en/docs)
WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310  fixed https hosts only
        return json.loads(r.read(1_000_000).decode("utf-8", "replace"))


def place(city: str, fetch=get_json) -> dict | None:
    q = urllib.parse.urlencode({"name": city, "count": 1, "language": "en", "format": "json"})
    hits = fetch(f"https://geocoding-api.open-meteo.com/v1/search?{q}").get("results") or []
    return hits[0] if hits else None


def weather(city: str, when: str = "now", fetch=get_json) -> str:
    """One or two spoken sentences; raises on network errors, ValueError for an unknown place."""
    p = place(city, fetch)
    if p is None:
        raise ValueError(city)
    q = urllib.parse.urlencode({
        "latitude": p["latitude"], "longitude": p["longitude"], "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"})
    d = fetch(f"https://api.open-meteo.com/v1/forecast?{q}")
    name = p.get("name") or city
    if when in ("today", "tomorrow"):
        i = 0 if when == "today" else 1
        day = d["daily"]
        rain = day.get("precipitation_probability_max", [None, None])[i]
        text = (f"{when.capitalize()} in {name}: {WMO.get(day['weather_code'][i], 'mixed weather')}, "
                f"{round(day['temperature_2m_min'][i])} to {round(day['temperature_2m_max'][i])} degrees")
        return text + (f", {rain} percent chance of rain." if rain is not None else ".")
    cur = d["current"]
    feels = round(cur["apparent_temperature"])
    text = f"In {name} it's {round(cur['temperature_2m'])} degrees and {WMO.get(cur['weather_code'], 'mixed')}"
    if abs(feels - round(cur["temperature_2m"])) >= 3:
        text += f", feels like {feels}"
    return text + "."


def _sentences(text: str, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(parts[:n]).strip()


def lookup(query: str, fetch=get_json) -> str | None:
    """A short answer, or None when neither source has one."""
    q = urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1, "skip_disambig": 1})
    d = fetch(f"https://api.duckduckgo.com/?{q}")
    for key in ("Answer", "AbstractText", "Definition"):
        v = d.get(key) if isinstance(d, dict) else None
        if isinstance(v, str) and v.strip():
            return _sentences(v)
    q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": query, "srlimit": 1,
                                "format": "json"})
    hits = (fetch(f"https://en.wikipedia.org/w/api.php?{q}").get("query") or {}).get("search") or []
    if not hits:
        return None
    title = urllib.parse.quote(hits[0]["title"].replace(" ", "_"), safe="")
    extract = fetch(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}").get("extract") or ""
    return _sentences(extract) or None
