"""Requests answered by rules before any model: going to sleep, greetings, thanks, help and "play X on YouTube".

These were the turns that woke the big model for nothing (history.db, 2026-10-29), or that it got wrong.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

_FILL = r"(?:(?:okay|ok|alright|all right|right|thanks|thank you|please|now|you can|you may|can you)[, ]+)*"
_END = r"(?:[, ]+(?:now|please|for now|then|thanks|thank you|for a while|for a bit))*[.!?]*"
_SLEEP = re.compile(
    rf"(?i)^{_FILL}(?:go (?:back )?to sleep|sleep|stop listening|stop|stop yourself|that'?s all|that is all|"
    rf"that will be all|that'?ll be all|that'?s it|never ?mind|cancel|nothing|no thanks|bye|bye bye|goodbye|"
    rf"good night|go away|be quiet|quiet|shut up|dismiss|rest|take a rest|see you|see you later){_END}$")
_GREET = re.compile(r"(?i)^(?:hi|hello|hey|hiya|hey there|hi there|hello there|good (morning|afternoon|evening)|"
                    r"namaste|yo)[.!]*$")
_HOW = re.compile(r"(?i)^(?:hi |hello |hey )?(?:how are you|how are you doing|how's it going|what's up)[.!?]*$")
_THANKS = re.compile(r"(?i)^(?:thanks|thank you|thanks a lot|thank you so much|cheers|great,? thanks|"
                     r"perfect,? thanks)[.!]*$")
_HELP = re.compile(r"(?i)^(?:help|help me|what can you do|what else can you do|what do you do|what are you able to do|"
                   r"what can i ask you|what can i say|what are your (?:features|skills|abilities)|"
                   r"what things can you do|how can you help(?: me)?)[.!?]*$")
_YOUTUBE = re.compile(r"(?i)^(?:please )?(?:(?:open|go to|launch) youtube (?:and |then )?)?"
                      r"(?:play|search(?: for)?|find|put on|show me)\s+(.+?)"
                      r"(?:\s+(?:on|in|from|using)\s+youtube)?[.!]*$")

HELP = ("I can open apps, files and websites, search the web or YouTube, change the volume and brightness, "
        "control music, lock the screen, take screenshots, tell the time and battery level, read your screen, "
        "write a reply for you and remember things you tell me. Say my name, then ask.")


@dataclass(frozen=True)
class Quick:
    """A tool call ("go_to_sleep", {}) or a reply to say."""

    tool: str = ""
    args: dict | None = None
    say: str = ""


def _greeting(part: str | None) -> str:
    if part:
        return f"Good {part.lower()}! What can I do for you?"
    hour = datetime.datetime.now().hour
    return "Hi! What can I do for you?" if 5 <= hour < 22 else "Hi, still up? What can I do for you?"


def quick(text: str) -> Quick | None:
    t = " ".join(text.split()).strip()
    if not t or len(t.split()) > 9:
        return None
    if _SLEEP.match(t):
        return Quick("go_to_sleep", {})
    m = _GREET.match(t)
    if m:
        return Quick(say=_greeting(m.group(1)))
    if _HOW.match(t):
        return Quick(say="I'm doing well, thanks. What can I do for you?")
    if _THANKS.match(t):
        return Quick(say="You're welcome.")
    if _HELP.match(t):
        return Quick(say=HELP)
    m = _YOUTUBE.match(t)
    if m and ("youtube" in t.lower()):
        q = re.sub(r"(?i)\s+(?:video|videos)$", "", m.group(1)).strip()
        if q and q.lower() not in ("it", "something", "music", "a video"):
            return Quick("search_web", {"query": q, "site": "youtube"})
    return None
