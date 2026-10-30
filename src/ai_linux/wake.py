"""Wake-word matching and the name test. gi-free and numpy-free: the engine and the app share this one matcher."""

from __future__ import annotations

import difflib
import re

MATCH = 0.75  # similarity the heard word needs to the name
JOINED_MATCH = 0.85  # stricter for two heard words glued together: "where is" must not pass for "hermes"
SEARCH_WORDS = 4  # the name must start within the first few words, as people say it
# The test sentences; the name sits where people put it when they call the assistant.
PHRASES = ("Hello {name}.", "{name}, can you do this for me?", "Wake up {name}.", "Hey {name}, what time is it?",
           "{name}, open my files.")
NEGATIVE = "Please open my files and play some music."  # no name: waking on this means false alarms
GOOD, FAIR = 0.8, 0.6  # share of sentences where the name must be heard


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


# said before the name when calling it: "Hey Phoenix" is a call, "Stop Phoenix" is a request
CALL_WORDS = {"hey", "hi", "hello", "okay", "ok", "yo", "so", "um", "uh", "oh", "and", "wake", "up", "dear", "hiya"}


def strip_wake(text: str, wake: str) -> str | None:
    """The command after the wake word (or a short one before it, as in "stop Phoenix"), "" if only the wake word
    was said, None if it wasn't said."""
    wake_w = words(wake)
    if not wake_w:
        return text
    tokens = list(re.finditer(r"[A-Za-z0-9']+", text))
    heard = [t.group().lower() for t in tokens]
    n = len(wake_w)
    for i in range(min(len(heard) - n + 1, SEARCH_WORDS)):
        end = None
        if difflib.SequenceMatcher(None, " ".join(heard[i:i + n]), " ".join(wake_w)).ratio() >= MATCH:
            end = i + n - 1
        else:
            # speech recognition sometimes splits an unusual name in two ("Dora Emon"); same length only
            joined, name = "".join(heard[i:i + n + 1]), "".join(wake_w)
            if (i + n < len(heard) and abs(len(joined) - len(name)) <= 2
                    and difflib.SequenceMatcher(None, joined, name).ratio() >= JOINED_MATCH):
                end = i + n
        if end is None:
            continue
        after = text[tokens[end].end():].lstrip(" ,.!?;:-")
        before = text[:tokens[i].start()].rstrip(" ,.!?;:-")
        if words(after) or not set(heard[:i]) - CALL_WORDS or not _closing(before):
            return after  # "I told Phoenix" is a call, not the request "I told"
        return before
    return None


def _closing(text: str) -> bool:
    """Words said before the name that make a request on their own: "Stop Phoenix", "Thanks Phoenix"."""
    from ai_linux.engine.quick import quick  # pure Python, no engine dependencies
    q = quick(text)
    return q is not None and (q.tool == "go_to_sleep" or bool(q.say))


def phrases(name: str) -> list[str]:
    return [p.format(name=name.strip().title()) for p in PHRASES]


def heard(text: str, name: str) -> bool:
    return bool(words(name)) and strip_wake(text, name) is not None


def warnings(name: str) -> list[str]:
    w = words(name)
    if not w:
        return ["Type a name first."]
    out = []
    if len(w) == 1 and len(w[0]) < 4:
        out.append("Very short names are easy to miss or mishear.")
    if len(w) > 2:
        out.append("One or two words work best.")
    return out


def rate(hits: int, total: int, false_alarm: bool = False) -> tuple[str, str]:
    """(good | fair | poor, advice) from how often the name was heard."""
    share = hits / total if total else 0.0
    if false_alarm:
        return "poor", f"Heard it {hits} of {total} times, but it also woke on a sentence without the name. " \
                       "Try a different name."
    if share >= GOOD:
        return "good", f"Heard it {hits} of {total} times. Good choice, go ahead."
    if share >= FAIR:
        return "fair", f"Heard it {hits} of {total} times. It may be missed sometimes; a clearer name is safer."
    return "poor", f"Heard it {hits} of {total} times. Try a different name."


class NameTest:
    """One run of the name test: the sentences in order, what was heard, and the verdict."""

    def __init__(self, name: str):
        self.name = name
        self.sentences = phrases(name) + [NEGATIVE]
        self.results: list[tuple[str, bool]] = []  # (heard text, went as it should)

    @property
    def done(self) -> bool:
        return len(self.results) >= len(self.sentences)

    @property
    def current(self) -> str | None:
        return None if self.done else self.sentences[len(self.results)]

    @property
    def on_negative(self) -> bool:
        return len(self.results) == len(PHRASES)

    def record(self, text: str) -> bool:
        ok = heard(text, self.name) != self.on_negative
        self.results.append((text, ok))
        return ok

    def verdict(self) -> tuple[str, str]:
        n = len(PHRASES)
        hits = sum(ok for _, ok in self.results[:n])
        return rate(hits, n, len(self.results) > n and not self.results[n][1])
