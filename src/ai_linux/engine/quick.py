"""Requests answered by rules before any model: sleep, greetings, small talk, thanks, help, "play X on YouTube".

These were the turns that woke the big model for nothing (history.db, 2026-10-29), or that it got wrong.
"""

from __future__ import annotations

import datetime
import random
import re
from dataclasses import dataclass

_FILL = r"(?:(?:okay|ok|alright|all right|right|thanks|thank you|please|now|you can|you may|can you)[, ]+)*"
_END = r"(?:[, ]+(?:now|please|for now|then|thanks|thank you|for a while|for a bit))*[.!?]*"
_SLEEP = re.compile(
    rf"(?i)^{_FILL}(?:go (?:back )?to sleep|sleep|stop listening|stop|stop yourself|that'?s all|that is all|"
    rf"that will be all|that'?ll be all|that'?s it|never ?mind|cancel|nothing|no thanks|bye|bye bye|goodbye|"
    rf"good night|go away|be quiet|quiet|shut up|dismiss|rest|take a rest|see you|see you later){_END}$")
_GREET = re.compile(r"(?i)^(?:hi|hello|hey|hiya|hey there|hi there|hello there|good (morning|afternoon|evening)|"
                    r"namaste|yo)[.!?]*$")
_AWAKE = re.compile(r"(?i)^(?:please |no,? )*(?:(?:don'?t|do not) (?:go to )?sleep(?: yet| now)?|stay (?:awake|here))[.!]*$")
_WRONG = re.compile(r"(?i)^(?:no,? )?(?:that'?s|that is|that was|this is) (?:wrong|not right|incorrect|"
                    r"not what i (?:asked|wanted|meant|said))[.!]*$|^wrong (?:answer|action)[.!]*$")
_HI = r"(?:(?:hi|hello|hey|so|and|okay|ok)[, ]+)?"
# Friendly chat with no request in it: short, varied replies instead of waking the big model (2026-10-30).
_CHAT = [(re.compile(rf"(?i)^{_HI}(?:{pat})[.!?]*$"), replies) for pat, replies in (
    (r"how are you(?: doing)?(?: today)?|how(?:'s| is) it going|how have you been|how do you feel|"
     r"how are you feeling(?: today)?|are you (?:okay|ok|alright|all right|good|fine)|you good|how(?:'s| is) your day",
     ("I'm doing well, thanks for asking. How about you?", "All good here, ready when you are.",
      "Feeling great. What's on your mind?")),
    (r"what are you doing|what are you up to|what'?s up|wh?assup|what'?s going on|what are you doing right now",
     ("Just here, listening for you. What do you need?", "Not much, waiting to help. What can I do?",
      "Keeping an eye on things. Anything I can do for you?")),
    (r"are you there|you there|are you listening|can you hear me|are you awake|do you hear me",
     ("I'm here.", "Yes, I'm listening.", "I'm here. Go ahead.")),
    (r"who are you|what are you|what'?s your name|what is your name|introduce yourself|tell me about yourself",
     ("I'm {name}, your assistant on this computer. Ask me to open apps, find things or answer questions.",)),
    (r"(?:i'?m|i am) (?:fine|good|great|okay|ok|well|doing well|doing good|doing great|alright|all right)(?: too)?",
     ("Glad to hear it.", "Nice. Let me know if you need anything.")),
    (r"nice to meet you|pleased to meet you", ("Nice to meet you too.",)),
    (r"good job|well done|nice work|great job|you(?:'re| are) (?:great|awesome|amazing|the best|smart)|i love you",
     ("Thank you, that's kind.", "Thanks! Happy to help.")),
    (r"what do you think|what are your thoughts|any thoughts", ("About what? Tell me and I'll think it over.",)),
)]
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
        "write a reply for you, set timers, type what you say when you say dictate (stop dictating ends it), "
        "and remember things you tell me. Say my name, then ask.")


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


def quick(text: str, name: str = "Phoenix") -> Quick | None:
    t = " ".join(text.split()).strip()
    if not t or len(t.split()) > 9:
        return None
    if _WRONG.match(t):
        return Quick("mark_wrong", {})  # marks the previous request for review (bench/router/export_history.py)
    if _AWAKE.match(t):
        return Quick(say="Okay, I'm still here.")
    if _SLEEP.match(t):
        return Quick("go_to_sleep", {})
    m = _GREET.match(t)
    if m:
        return Quick(say=_greeting(m.group(1)))
    for pattern, replies in _CHAT:
        if pattern.match(t):
            return Quick(say=random.choice(replies).format(name=name))
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
