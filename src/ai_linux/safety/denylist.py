"""Catastrophic-command denylist (AI_Linux v3).

A conservative BACKSTOP, not a sandbox. ``destructive_reason(command)`` returns a short reason
string when a command would clearly wipe the whole machine, the user's home, or a raw disk, and
``None`` otherwise. It is deliberately CATASTROPHIC-ONLY: ordinary destructive commands stay
allowed (``shutdown``/``reboot``, ``rm file.txt``, ``rm -rf ./build``, ``cd ~/proj && rm -rf *``,
deleting a single sub-folder such as ``~/Downloads`` or ``~/.config``), because a small model
over-refuses and these are legitimate voice commands. The real boundary is the action gate.

Ported from src/glados/mcp/shell_exec.py::_destructive_reason and hardened against the bypasses a
2026-10-27 audit found (path traversal with ``..``, ``${HOME:?}`` and other parameter forms, simple
variable indirection, ``sh -c`` nesting, decode-and-execute pipes, ``find`` with ``-exec rm``,
recursive ``chmod``/``chown`` of a root, ``mv`` of a root, and a ``python -c`` rmtree of home).

What it CANNOT catch (by design, a regex is never complete, so lean on the gate, not this):
  * two-step download-then-run (``curl -o f url; sh f`` -- the second command runs an opaque file),
  * arbitrary interpreter logic whose destructive target is computed at runtime,
  * command substitution that assembles a dangerous string indirectly ($(printf ...) etc.),
  * anything a child process does after launch.
One deliberate non-block: ``rm -rf ~/.config`` (a single sub-folder) stays ALLOWED to honour the
catastrophic-only rule; protecting named dotfolders would need a separate, configurable list.
"""

from __future__ import annotations

import os
import re

_HOME = os.path.expanduser("~")
_HOME_PARENT = os.path.dirname(_HOME) or "/"  # usually /home; wiping it loses every user's data

# A path that resolves to one of these is a top-level root (catastrophic to recursively delete).
_ROOTS = frozenset({"/", _HOME_PARENT, _HOME, "__HOME_USER__"})

# Interpreters that execute code read from stdin when a download/decoder is piped into them.
_INTERPRETERS = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "ash", "python", "python2", "python3",
     "perl", "ruby", "node", "nodejs", "php"}
)
_DOWNLOADERS = frozenset({"curl", "wget", "fetch"})
_DECODER_RE = re.compile(r"\b(?:base64\b[^|]*(?:-d|-D|--decode)|xxd\b[^|]*-r|openssl\b[^|]*\s-d\b|uudecode)\b", re.I)

_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:?\s*&\s*\}\s*;\s*:")

# Static catastrophic patterns (literal device/disk targets and self-contained forms).
_DENY: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"--no-preserve-root"), "rm --no-preserve-root"),
    (re.compile(r"\bdd\b[^|;&\n]*\bof\s*=\s*/dev/(sd|nvme|mmcblk|vd|hd|disk)", re.I), "dd to a raw disk device"),
    (re.compile(r"\bmkfs(\.\w+)?\b[^|;&\n]*\s/dev/", re.I), "mkfs on a device"),
    (re.compile(r"\bwipefs\b", re.I), "wipefs"),
    (re.compile(r"\bshred\b[^|;&\n]*\s/dev/", re.I), "shred a device"),
    (re.compile(r"\btruncate\b[^|;&\n]*\s/dev/(sd|nvme|mmcblk|vd|hd|disk)", re.I), "truncate a device"),
    (re.compile(r">\s*/dev/(sd|nvme|mmcblk|vd|hd)", re.I), "redirect to a raw disk device"),
    (re.compile(r"\btee\b[^|;&\n]*\s/dev/(sd|nvme|mmcblk|vd|hd)", re.I), "tee to a raw disk device"),
    (_FORK_BOMB, "fork bomb"),
]


def _strip_quotes_escapes(s: str) -> str:
    """Remove shell escapes then quote characters, so quoted/escaped targets still match.

    Turns ``sh -c "rm -rf \\$HOME"`` into ``sh -c  rm -rf $HOME`` for scanning. This changes
    word boundaries but never hides a catastrophic token, which is all the scan needs.
    """
    s = re.sub(r"\\(.)", r"\1", s)  # drop backslash escapes: \$ -> $, \" -> "
    return s.replace('"', " ").replace("'", " ")


def _apply_assignments(s: str) -> str:
    """Inline simple leading ``VAR=value`` assignments, so ``X=~; rm -rf $X`` is seen as ``rm -rf ~``.

    Only whitespace-free values are substituted (enough for the audited indirection cases); complex
    values with spaces or command substitution are left alone and caught by other checks.
    """
    for name, val in re.findall(r"(?:^|[;&]\s*)(\w+)=(\S+)", s):
        if "$(" in val or "`" in val:  # a computed value, do not textually inline
            continue
        s = re.sub(rf"\$\{{{name}\}}|\${name}\b", lambda _m, v=val: v, s)
    return s


def _normalise(token: str, base: str = _HOME) -> str:
    """Resolve a path token to a canonical absolute path for root comparison.

    Expands ~, $HOME/${HOME}/${HOME:?}/${HOME:-x}, joins relatives against ``base`` (the shell cwd,
    which is the user's home at runtime), and collapses ``.``/``..`` with normpath. A ``~user`` form
    is treated as a root sentinel (conservative). Globs are handled by the caller, not here.
    """
    t = token.strip().strip('"').strip("'")
    if not t:
        return base
    t = re.sub(r"\$\{HOME(?:[:%#/][^}]*)?\}", _HOME, t)  # ${HOME}, ${HOME:?}, ${HOME:-x}, ${HOME%/x}
    t = t.replace("$HOME", _HOME)
    if t == "~" or t.startswith("~/"):
        t = _HOME + t[1:]
    elif t.startswith("~"):  # ~otheruser -> another home, treat as a root-level target
        return "__HOME_USER__"
    if not t.startswith("/"):
        t = os.path.join(base, t)
    return os.path.normpath(t)


def _is_root(path: str) -> bool:
    return path in _ROOTS


def _glob_base(token: str) -> str | None:
    """If the token is a top-level wipe glob, return the directory it wipes, else None."""
    t = token.strip().strip('"').strip("'")
    if t in ("*", ".*"):
        return ""  # wipes the cwd (resolved by the caller against the governing cd)
    for suffix in ("/*", "/.*"):
        if t.endswith(suffix):
            return t[: -len(suffix)] or "/"
    return None


def _governing_cwd(pre: str) -> str:
    """Effective cwd for a command: the last ``cd`` target before it, else the home directory."""
    cds = re.findall(r"\bcd\b\s+([^\s;&|]+)", pre)
    return _normalise(cds[-1]) if cds else _HOME


def _rm_targets(c: str) -> str | None:
    """Block an ``rm`` with recursive+force flags whose target resolves to a top-level root."""
    for m in re.finditer(r"\brm\b", c):
        args = re.split(r"[;&|]", c[m.end():])[0]
        norm = re.sub(r"--recursive(?:=\S*)?", " -r ", args)  # long flags -> short so findall sees them
        norm = re.sub(r"--force(?:=\S*)?", " -f ", norm)
        flags = "".join(re.findall(r"(?:^|\s)-([A-Za-z]+)", norm)).lower()
        if "r" not in flags or "f" not in flags:
            continue
        cwd = _governing_cwd(c[: m.start()])
        for t in args.split():
            if t.startswith("-"):
                continue
            gb = _glob_base(t)
            if gb is not None:
                base = cwd if gb == "" else _normalise(gb, cwd)
                if _is_root(base):
                    return "recursive force-delete of a top-level path"
                continue
            if _is_root(_normalise(t, cwd)):
                return "recursive force-delete of a top-level path"
    return None


def _mv_root(c: str) -> str | None:
    """Block moving a top-level root away (``mv ~ /tmp/x`` orphans the whole home)."""
    for m in re.finditer(r"\bmv\b", c):
        args = re.split(r"[;&|]", c[m.end():])[0]
        for t in args.split():
            if t.startswith("-"):
                continue
            return "move of a top-level path" if _is_root(_normalise(t)) else None  # first path is the source
    return None


def _chmod_chown_root(c: str) -> str | None:
    """Block a recursive chmod/chown of a root (chmod only for a locking mode, to stay catastrophic-only)."""
    for m in re.finditer(r"\b(chmod|chown)\b", c):
        verb = c[m.start():m.end()]
        args = re.split(r"[;&|]", c[m.end():])[0]
        if not re.search(r"(?:^|\s)-\S*[Rr]|--recursive", args):
            continue
        if verb == "chmod" and not re.search(r"\b(000|777)\b", args):
            continue  # a recursive chmod with a normal mode is recoverable, leave it
        for t in args.split():
            if t.startswith("-"):
                continue
            if _is_root(_normalise(t)):
                return f"recursive {verb} of a top-level path"
    return None


def _find_root(c: str) -> str | None:
    """Block ``find <root> ... -delete`` or ``-exec rm`` on a bare top-level root."""
    root_alt = rf"(?:/|~/?|\$HOME/?|\$\{{HOME\}}/?|/home/?|{re.escape(_HOME)}/?)"
    pat = re.compile(
        rf"\bfind\b\s+[\"']?{root_alt}[\"']?\s[^|;&\n]*(?:-delete\b|-exec\s+(?:rm|unlink)\b)", re.I
    )
    return "find delete/exec-rm of a top-level path" if pat.search(c) else None


def _pipe_to_interpreter(c: str) -> str | None:
    """Block a pipeline that feeds a download or decoded payload into an interpreter reading stdin."""
    if "|" not in c:
        return None
    segs = [s.strip() for s in c.split("|")]
    last = segs[-1].split()
    if not last:
        return None
    final = os.path.basename(last[0])
    if final == "sudo" and len(last) > 1:  # curl x | sudo bash
        final = os.path.basename(last[1])
    if final not in _INTERPRETERS:
        return None
    for s in segs[:-1]:
        toks = s.split()
        if not toks:
            continue
        head = os.path.basename(toks[0])
        if head == "sudo" and len(toks) > 1:
            head = os.path.basename(toks[1])
        if head in _DOWNLOADERS or _DECODER_RE.search(s):
            return "pipe a download/decoded payload into an interpreter"
    return None


def _eval_or_python(c: str) -> str | None:
    """Block ``eval`` of a remote/decoded payload and a ``python -c`` rmtree of home/root."""
    if re.search(r"\beval\b", c) and (_DECODER_RE.search(c) or re.search(r"\b(curl|wget)\b", c)):
        return "eval of a downloaded/decoded payload"
    if re.search(r"\b(python|python2|python3)\b", c) and re.search(r"(?:^|\s)-c\b", c):
        if re.search(r"\b(?:rmtree|removedirs)\b", c) and re.search(r"expanduser|Path\.home|\bHOME\b|~", c):
            return "python one-liner deleting a home/root tree"
    return None


_SCANNERS = (_pipe_to_interpreter, _eval_or_python, _find_root, _chmod_chown_root, _mv_root, _rm_targets)


def _scan(c: str) -> str | None:
    for pattern, reason in _DENY:
        if pattern.search(c):
            return reason
    for scan in _SCANNERS:
        reason = scan(c)
        if reason is not None:
            return reason
    return None


def destructive_reason(command: str) -> str | None:
    """Return a reason string if ``command`` is catastrophic, else None. The single entry point."""
    if not command or not command.strip():
        return None
    raw = " ".join(command.split())  # collapse whitespace
    assigned = _apply_assignments(raw)
    unquoted = _strip_quotes_escapes(assigned)
    for form in dict.fromkeys((raw, assigned, unquoted)):  # dedupe, keep order
        reason = _scan(form)
        if reason is not None:
            return reason
    return None
