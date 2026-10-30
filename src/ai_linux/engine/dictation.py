"""Dictation: what you say is typed where your cursor is, with a light clean-up and no model.

Parakeet already writes capitals and punctuation; this only drops fillers and turns spoken "comma", "new line"
and the like into text. No model, so it is instant, works on battery and never changes your words.
"""

from __future__ import annotations

import re

_START_WITH_TEXT = re.compile(r"(?i)^(?:please\s+)?(?:dictate|type\s+what\s+i\s+say)\b[\s,:;.!-]*(.*?)\s*$")
_START_ALONE = re.compile(r"(?i)^(?:please\s+)?(?:(?:start|begin)\s+)?(?:dictation|dictating|take\s+dictation)"
                          r"(?:\s+mode)?[.!]*$")
_STOP = re.compile(r"(?i)^(?:please\s+)?(?:stop|end|finish|done|that's all|that is all|i'm done|im done)"
                   r"(?:\s+(?:dictating|dictation|typing))?[.!]*$")
_FILLER = re.compile(r"(?i)(?<![\w'])(?:um+|uh+|erm+|hmm+|mm+)(?![\w'])[,.]?[ \t]*")
_SPOKEN = ((r"new paragraph", "\n\n"), (r"new line|next line", "\n"), (r"comma", ", "), (r"full stop", ". "),
           (r"question mark", "? "), (r"exclamation (?:mark|point)", "! "), (r"colon", ": "))


def start(text: str) -> str | None:
    """The words to type when ``text`` turns dictation on ("" for none), else None."""
    t = " ".join(text.split())
    m = _START_WITH_TEXT.match(t)
    if m:
        return m.group(1)
    return "" if _START_ALONE.match(t) else None


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
