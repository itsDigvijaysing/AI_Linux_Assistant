"""What Phoenix knows about you: facts you keep for good, and facts it learned that fade unless you pin them.

One JSON file shared by the app (Knowledge dialog) and the engine (the big model's remember/forget tools and its
instructions). No GTK and no numpy, so both Pythons import it. Limits keep the instructions short for a 4B model.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ai_linux.paths import DATA_DIR

KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
KEPT_MAX = 20  # facts you keep
LEARNED_MAX = 30  # facts it learned; the oldest goes when a new one comes
LEARNED_DAYS = 14
FACT_CHARS = 200
PROMPT_CHARS = 2400  # about 600 tokens in the big model's instructions
_DAY = 86400.0


@dataclass
class Fact:
    id: str
    text: str
    kept: bool  # True: stays until you delete it; False: learned, fades after LEARNED_DAYS
    source: str  # you | learned
    created: float
    updated: float

    def days_left(self, now: float | None = None) -> int | None:
        if self.kept:
            return None
        now = time.time() if now is None else now
        return max(0, int((self.updated + LEARNED_DAYS * _DAY - now) // _DAY))

    def expired(self, now: float) -> bool:
        return not self.kept and now >= self.updated + LEARNED_DAYS * _DAY


def clean(text: str) -> str:
    """One line, no surrounding quotes or list marks, at most FACT_CHARS."""
    text = " ".join(str(text or "").split()).strip(" \"'-*•")
    return text[:FACT_CHARS].rstrip()


def _key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


# Words every fact shares (facts are written "They ... their ..."): matching on them deleted the wrong fact.
_COMMON = {"they", "them", "their", "theirs", "themselves", "they're", "they've", "they'd", "they'll", "i", "me", "my",
           "mine", "is", "are", "was", "were", "be", "been", "am", "has", "have", "had", "do", "does", "did", "the",
           "a", "an", "to", "of", "and", "or", "in", "on", "at", "for", "with", "that", "this", "it", "its", "about",
           "forget", "remember", "please", "you", "can"}


def _content(text: str) -> set[str]:
    return set(_key(text).split()) - _COMMON


def _overlap(a: str, b: str) -> float:
    wa, wb = _content(a), _content(b)
    return len(wa & wb) / max(1, min(len(wa), len(wb))) if wa and wb else 0.0


class Knowledge:
    """Reads the file again whenever it changed on disk, so both processes see each other's edits."""

    def __init__(self, path: Path | str = KNOWLEDGE_FILE, on_change=None):
        self.path = Path(path)
        self.on_change = on_change
        self._facts: list[Fact] = []
        self._stamp: tuple | None = None

    # reading

    def _read(self) -> None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            self._facts, self._stamp = [], None
            return
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == self._stamp:
            return
        try:
            raw = json.loads(self.path.read_text())
            facts = [Fact(**{k: f[k] for k in Fact.__dataclass_fields__}) for f in raw.get("facts", [])]
        except (OSError, ValueError, KeyError, TypeError):
            facts = []  # a broken file reads as empty; the next save replaces it
        self._facts, self._stamp = facts, stamp

    def facts(self, now: float | None = None) -> list[Fact]:
        """Kept facts first, then learned ones newest first; faded ones are left out."""
        now = time.time() if now is None else now
        self._read()
        live = [f for f in self._facts if not f.expired(now)]
        return sorted(live, key=lambda f: (not f.kept, -f.updated if not f.kept else f.created))

    def kept(self, now: float | None = None) -> list[Fact]:
        return [f for f in self.facts(now) if f.kept]

    def learned(self, now: float | None = None) -> list[Fact]:
        return [f for f in self.facts(now) if not f.kept]

    def prompt(self, now: float | None = None) -> str:
        """The facts for the big model's instructions, kept ones first, cut at PROMPT_CHARS."""
        facts = self.facts(now)
        out, used = [], 0
        for title, group in (("Facts they keep", [f for f in facts if f.kept]),
                             ("Learned in recent conversations, may be out of date", [f for f in facts if not f.kept])):
            lines = []
            for f in group:
                if used + len(f.text) + 2 > PROMPT_CHARS:
                    break
                lines.append(f.text.rstrip(".") + ".")
                used += len(f.text) + 2
            if lines:
                out.append(f"{title}: " + " ".join(lines))
        return " ".join(out)

    # changes (read, change, write under a lock: the app and the engine both write)

    @contextlib.contextmanager
    def _edit(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._stamp = None
            self._read()
            now = time.time()
            self._facts = [f for f in self._facts if not f.expired(now)]
            yield now
            self._write()
        if self.on_change is not None:
            self.on_change()

    def _write(self) -> None:
        data = json.dumps({"version": 1, "facts": [asdict(f) for f in self._facts]}, indent=1)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".knowledge-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        st = self.path.stat()
        self._stamp = (st.st_mtime_ns, st.st_size)

    def add(self, text: str, kept: bool = False, source: str = "you") -> tuple[Fact | None, str]:
        """Returns (fact, "") or (None, why). A fact already known is refreshed instead of repeated."""
        text = clean(text)
        if len(_key(text)) < 3:
            return None, "That is too short to remember."
        with self._edit() as now:
            same = next((f for f in self._facts if _key(f.text) == _key(text) or _overlap(f.text, text) >= 0.8), None)
            if same is not None:  # the same fact, or one saying more about it: keep the fuller wording
                if len(_content(text)) > len(_content(same.text)):
                    same.text = text
                same.updated = now
                same.kept = same.kept or kept
                return same, ""
            if kept and sum(f.kept for f in self._facts) >= KEPT_MAX:
                return None, f"You already keep {KEPT_MAX} facts. Delete one first."
            fact = Fact(uuid.uuid4().hex[:12], text, kept, source, now, now)
            self._facts.append(fact)
            learned = sorted((f for f in self._facts if not f.kept), key=lambda f: f.updated)
            for old in learned[:max(0, len(learned) - LEARNED_MAX)]:
                self._facts.remove(old)
            return fact, ""

    def update(self, fact_id: str, text: str | None = None, kept: bool | None = None) -> str:
        """"" when done, else why not."""
        with self._edit() as now:
            fact = next((f for f in self._facts if f.id == fact_id), None)
            if fact is None:
                return "That fact is gone."
            if kept and not fact.kept and sum(f.kept for f in self._facts) >= KEPT_MAX:
                return f"You already keep {KEPT_MAX} facts. Delete one first."
            if text is not None and len(_key(clean(text))) >= 3:
                fact.text = clean(text)
            if kept is not None:
                fact.kept = kept
            fact.updated = now
            return ""

    def delete(self, fact_id: str) -> None:
        with self._edit():
            self._facts = [f for f in self._facts if f.id != fact_id]

    def clear_learned(self) -> None:
        with self._edit():
            self._facts = [f for f in self._facts if f.kept]

    def forget(self, text: str) -> list[str]:
        """Delete the one fact that best matches ``text`` by its own words; returns what was forgotten.
        Nothing is deleted when two facts match equally well: the caller asks which."""
        with self._edit():
            scored = sorted(((_overlap(text, f.text), f) for f in self._facts), key=lambda x: -x[0])
            if not scored or scored[0][0] < 0.6 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
                return []
            gone = scored[0][1]
            self._facts = [f for f in self._facts if f is not gone]
            return [gone.text]


def migrate_about_me(settings, store: Knowledge) -> None:
    """The old "About you" text becomes facts you keep; your name stays a setting. What doesn't fit (the kept
    facts are full) stays in the old text, so nothing is lost."""
    about = str(settings.get("about_me") or "").strip()
    if not about:
        return
    left = []
    for part in re.split(r"(?<=[.!?])\s+|\n+", about):
        if part.strip():
            fact, why = store.add(part, kept=True)  # a fact already known is refreshed, not repeated
            if fact is None and "too short" not in why:
                left.append(part.strip())
    if left:
        settings.set("about_me", " ".join(left))
    else:
        settings.reset("about_me")
