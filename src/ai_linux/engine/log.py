"""A loguru-style logger on stdlib logging, so the copied engine modules keep their "{}" calls."""

from __future__ import annotations

import logging


class _Logger:
    def __init__(self, name: str):
        self._log = logging.getLogger(name)

    def _emit(self, level: int, msg, args, kwargs) -> None:
        if self._log.isEnabledFor(level):
            text = str(msg)
            if args or kwargs:
                try:
                    text = text.format(*args, **kwargs)
                except (IndexError, KeyError, ValueError):
                    text = f"{text} {args} {kwargs}"
            self._log.log(level, text, stacklevel=3)

    def trace(self, msg, *a, **k): self._emit(5, msg, a, k)
    def debug(self, msg, *a, **k): self._emit(logging.DEBUG, msg, a, k)
    def info(self, msg, *a, **k): self._emit(logging.INFO, msg, a, k)
    def success(self, msg, *a, **k): self._emit(logging.INFO, msg, a, k)
    def warning(self, msg, *a, **k): self._emit(logging.WARNING, msg, a, k)
    def error(self, msg, *a, **k): self._emit(logging.ERROR, msg, a, k)
    def exception(self, msg, *a, **k): self._emit(logging.ERROR, msg, a, k)


logger = _Logger("ai_linux.engine.vendored")
logging.getLogger("ai_linux.engine.vendored").setLevel(logging.WARNING)  # the copied modules are chatty
