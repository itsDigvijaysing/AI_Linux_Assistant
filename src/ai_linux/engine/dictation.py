"""Dictation: what you say is typed where your cursor is.

"Dictate" lets the big model clean it up: grammar, fillers, false starts and rambling (its prompt is adapted from
OpenWhispr's); "type what I say" types the words as said. Both get a light clean-up first: fillers out, spoken "comma",
"new line" and the like into text. What is said is collected until "stop dictating", then typed once (pauses to think
cut nothing; a very long silence ends a forgotten one).
A 4B model can answer or add instead of rewriting, so ``pick`` keeps what was said when its text looks wrong.
"""

from __future__ import annotations

import re

from ai_linux.wake import CALL_WORDS, is_name, words

_LEAD = re.compile(r"(?i)^(?:(?:um+|uh+|okay|ok|so|alright|hey|please)\b[\s,:;.!-]*)+")
# "dictate is not working" talks about it; "dictate the report by Friday" is text
_START_WITH_TEXT = re.compile(r"(?i)^dictate\b(?!\s+(?:is|isn't|does|doesn't|did|didn't|was|wasn't|not|mode)\b)"
                              r"[\s,:;.!-]*(.*?)\s*$")
_START_EXACT = re.compile(r"(?i)^(?:just\s+)?type\s+"
                          r"(?:exactly\s+what\s+i\s+say|what\s+i\s+say|exactly)\b[\s,:;.!-]*(.*?)\s*$")
_ASK = (r"(?:let'?s|let\s+us|(?:can|could|would|will)\s+you|i\s+want\s+to|i\s+wanna|i'?d\s+like\s+to|"
        r"i\s+would\s+like\s+to|i\s+need\s+to)\s+")
_START_ALONE = re.compile(rf"(?i)^(?:{_ASK})?(?:(?:start|begin|turn\s+on|switch\s+on)\s+)?"
                          r"(?:dictation|dictating|dictate|take\s+dictation)(?:\s+mode)?(?:\s+something)?"
                          r"(?:\s+(?:right\s+)?now)?(?:\s+please)?[.!?]*$")
# about dictation, not "done" or "that's all": those can be part of the text
_STOP = re.compile(r"(?i)\b(?:please\s+)?(?:(?:i'?m\s+|i\s+am\s+)?done\s+(?:with\s+)?(?:the\s+)?|"
                   r"(?:stop|end|finish)\s+(?:the\s+)?)(?:dictating|dictation|dictated|dictate)\b")
# about dictation itself; "remind me to dictate the letter" is a request like any other
_ABOUT = re.compile(rf"(?i)^(?:{_ASK}dictate\b|(?:how|why)\b.*\bdictat|(?:is|does)\s+dictation\b|"
                    r"are\s+you\s+dictating\b|"
                    r"dictat(?:e|ion|ing)\s+(?:is|isn't|does|doesn't|did|didn't|was|wasn't|not)\b)")
_AFTER_STOP = {"now", "please", "thanks", "thank", "you"}
_FILLER = re.compile(r"(?i)(?<![\w'])(?:um+|uh+|erm+|hmm+|mm+)(?![\w'])[,.]?[ \t]*")
_SPOKEN = ((r"new paragraph", "\n\n"), (r"new line|next line", "\n"), (r"comma", ", "), (r"full stop", ". "),
           (r"question mark", "? "), (r"exclamation (?:mark|point)", "! "), (r"colon", ": "))


def start(text: str) -> tuple[str, str] | None:
    """(mode, words to type) when ``text`` turns dictation on, else None. Mode: polish | exact."""
    t = _LEAD.sub("", " ".join(text.split()))
    m = _START_EXACT.match(t)
    if m:
        return "exact", m.group(1)
    if _START_ALONE.match(t):
        return "polish", ""
    m = _START_WITH_TEXT.match(t)
    return ("polish", m.group(1)) if m else None


def about(text: str) -> bool:
    return bool(_ABOUT.match(_LEAD.sub("", " ".join(text.split()))))


def pick(said: str, rewrite: str) -> str:
    """The model's rewrite, unless it is empty, much longer or shorter, lost a question or changed the line breaks."""
    r = re.sub(r"<think>.*?</think>", "", rewrite or "", flags=re.S).strip().strip('"\u201c\u201d').strip()
    r = re.sub(r"\s*\u2014\s*", ", ", r)  # small models love them, speech never has them
    if not r:
        return said
    a, b = len(said.split()), len(r.split())
    if b > 1.5 * a + 4 or b < 0.5 * a:
        return said
    body = said.strip()
    gained = r.count("?") - body.count("?")
    asking_back = gained > a // 12 and set(words(r)) - set(words(body))  # the same words only got their mark fixed
    if gained < 0 or asking_back or body.count("\n") != r.count("\n"):
        return said
    return r + said[len(said.rstrip()):]  # a line break said at the very end


def split_stop(text: str, wake: str = "") -> tuple[str, bool]:
    """(words before it, True) when ``text`` ends with "stop dictating", the name or a "please" around it;
    else (text, False)."""
    t = " ".join(text.split())
    last = None
    for last in _STOP.finditer(t):
        pass
    if last is None:
        return text, False
    after = [w for w in words(t[last.end():]) if w not in _AFTER_STOP]
    if after and not is_name(" ".join(after), wake):
        return text, False
    before = t[:last.start()].rstrip(" ,;:-")
    tokens = list(re.finditer(r"[A-Za-z0-9']+", before))
    n = len(words(wake))
    if n and len(tokens) >= n and is_name(" ".join(m.group() for m in tokens[-n:]), wake):
        head = before[:tokens[-n].start()].rstrip(" ,;:-")
        if not set(words(head)) - CALL_WORDS:  # "Okay Phoenix, stop dictating": all of it was for us
            return "", True
        if before[-1] not in ".!?" or head[-1] in ".!?":  # else "We flew to Phoenix.": the name ends a sentence
            before = head
    return before, True


def clean(text: str) -> str:
    """Fillers out, spoken punctuation and line breaks in, spacing and capitals fixed."""
    t = _FILLER.sub("", " ".join(text.split()))
    for words, mark in _SPOKEN:
        t = re.sub(rf"(?i)[ \t]*[,.]?[ \t]*\b(?:{words})\b[,.]?[ \t]*", mark, t)
    t = re.sub(r"[ \t]+([,.:;?!])", r"\1", t)
    t = re.sub(r"[ \t]*\n[ \t]*", "\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t).strip(" \t")
    t = re.sub(r"(^|\n|[.!?] )([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    return "" if not re.search(r"\w", t) else t
