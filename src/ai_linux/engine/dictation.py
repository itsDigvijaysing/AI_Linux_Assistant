"""Dictation: what you say is typed where your cursor is.

"Dictate" lets the big model fix grammar and wording (like OpenWhispr's clean-up); "type what I say" types the
words as said. Both get a light clean-up first: fillers out, spoken "comma", "new line" and the like into text.
A 4B model can answer or add instead of rewriting, so ``pick`` keeps what was said when its text looks wrong.
"""

from __future__ import annotations

import re

_START_WITH_TEXT = re.compile(r"(?i)^(?:please\s+)?dictate\b[\s,:;.!-]*(.*?)\s*$")
_START_EXACT = re.compile(r"(?i)^(?:please\s+)?(?:just\s+)?type\s+"
                          r"(?:exactly\s+what\s+i\s+say|what\s+i\s+say|exactly)\b[\s,:;.!-]*(.*?)\s*$")
_START_ALONE = re.compile(r"(?i)^(?:please\s+)?(?:(?:start|begin)\s+)?(?:dictation|dictating|take\s+dictation)"
                          r"(?:\s+mode)?[.!]*$")
_STOP = re.compile(r"(?i)^(?:please\s+)?(?:stop|end|finish|done|that's all|that is all|i'm done|im done)"
                   r"(?:\s+(?:dictating|dictation|typing))?[.!]*$")
_FILLER = re.compile(r"(?i)(?<![\w'])(?:um+|uh+|erm+|hmm+|mm+)(?![\w'])[,.]?[ \t]*")
_SPOKEN = ((r"new paragraph", "\n\n"), (r"new line|next line", "\n"), (r"comma", ", "), (r"full stop", ". "),
           (r"question mark", "? "), (r"exclamation (?:mark|point)", "! "), (r"colon", ": "))


def start(text: str) -> tuple[str, str] | None:
    """(mode, words to type) when ``text`` turns dictation on, else None. Mode: polish | exact."""
    t = " ".join(text.split())
    m = _START_EXACT.match(t)
    if m:
        return "exact", m.group(1)
    m = _START_WITH_TEXT.match(t)
    if m:
        return "polish", m.group(1)
    return ("polish", "") if _START_ALONE.match(t) else None


def pick(said: str, rewrite: str) -> str:
    """The model's rewrite, unless it is empty, much longer or shorter, or turned a question into an answer."""
    r = re.sub(r"<think>.*?</think>", "", rewrite or "", flags=re.S).strip().strip('"\u201c\u201d').strip()
    if not r:
        return said
    a, b = len(said.split()), len(r.split())
    if b > 1.5 * a + 4 or b < 0.5 * a:
        return said
    if said.rstrip().endswith("?") != r.rstrip().endswith("?"):
        return said
    return r


def is_stop(text: str) -> bool:
    return bool(_STOP.match(" ".join(text.split())))


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
