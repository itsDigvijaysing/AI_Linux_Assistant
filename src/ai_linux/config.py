"""App identity, XDG paths and persistent settings."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from pathlib import Path

from gi.repository import GObject

log = logging.getLogger(__name__)

from ai_linux.paths import (  # noqa: E402,F401  re-exported for existing imports
    APP_ID, APP_NAME, ICON_NAME, ASSETS_DIR, CACHE_DIR, CONFIG_DIR, DATA_DIR, HISTORY_DB, LOG_DIR, MODELS_DIR, PACKAGE_DIR, REPO_ROOT,
    SETTINGS_FILE, STATE_DIR,
)

from ai_linux.schema import (  # noqa: E402,F401  re-exported for existing imports
    CHOICES, DEFAULTS, PROFILE_KEYS, PROFILES, SHARED_NOW, default_for, on_ac_power,
)

_OLD_PROFILE = {"performance": "charger", "battery": "battery", "auto": "auto"}


class Settings(GObject.Object):
    """Stores only keys the user changed, so new defaults reach existing installs.

    Keys in PROFILE_KEYS hold one value per power profile: get()/set() use the profile in use now, get_for()/
    set_for() a named one. ``changed::<key>`` fires when the value in use changes (also when the profile
    switches); ``changed::<profile>.<key>`` when a profile's stored value changes. Main thread only.
    """

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST | GObject.SignalFlags.DETAILED, None, (str,)),
        "profile-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, path: Path = SETTINGS_FILE, *, on_ac: bool | None = None):
        super().__init__()
        self._path = Path(path)
        self._values = self._read()
        self._on_ac = on_ac_power() if on_ac is None else bool(on_ac)
        if self._migrate():
            self._save()

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text())
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable settings %s: %s", self._path, exc)
            return {}

    def _migrate(self) -> bool:
        """Before profiles a key had one value: it now applies to both (the small model to battery only)."""
        changed = False
        old = self._values.pop("power_mode", None)
        if old is not None:
            changed = True
            if old in _OLD_PROFILE and "profile" not in self._values:
                self._values["profile"] = _OLD_PROFILE[old]
        for key in PROFILE_KEYS:
            if key in self._values:
                value = self._values.pop(key)
                for profile in PROFILES:
                    self._values.setdefault(f"{profile}.{key}", value)
                changed = True
        # 2026-10-29: the models became one choice for both profiles; a profile keeps only speed and small_on
        for key in SHARED_NOW:
            per = {p: self._values.pop(f"{p}.{key}") for p in PROFILES if f"{p}.{key}" in self._values}
            changed = changed or bool(per)
            if key == "small_model":
                if self._values.get("small_model") in ("none", ""):
                    per = {p: "none" for p in PROFILES} | per
                    self._values.pop("small_model")
                    changed = True
                for p in [p for p, v in per.items() if v in ("none", "")]:
                    per.pop(p)
                    if default_for(p, "small_on"):  # "none" was the old way to turn it off
                        self._values.setdefault(f"{p}.small_on", False)
            if per:
                self._values.setdefault(key, per.get("battery", per.get("charger")))
        for p in PROFILES:
            if self._values.pop(f"{p}.big_model_policy", None) == "instant":
                self._values.setdefault(f"{p}.speed", "full")
                changed = True
            changed = self._values.pop(f"{p}.ollama_keep_alive", None) is not None or changed
        mode = self._values.pop("load_mode", None)
        if mode is not None:
            changed = True
            if mode == "full":
                for p in PROFILES:
                    self._values.setdefault(f"{p}.speed", "full")
        return changed

    # profiles

    def active_profile(self) -> str:
        mode = self.get("profile")
        if mode in PROFILES:
            return mode
        return "charger" if self._on_ac else "battery"

    def set_on_ac(self, on_ac: bool) -> None:
        """Called by the app's power watcher."""
        if bool(on_ac) != self._on_ac:
            self._switch(lambda: setattr(self, "_on_ac", bool(on_ac)))

    def _switch(self, change) -> None:
        old = self.active_profile()
        before = {k: self.get(k) for k in PROFILE_KEYS}
        change()
        new = self.active_profile()
        if new != old:
            self.emit("profile-changed", new)
            for k in PROFILE_KEYS:
                if self.get(k) != before[k]:
                    self.emit(f"changed::{k}", k)

    # values

    def get(self, key: str):
        if key in PROFILE_KEYS:
            return self.get_for(self.active_profile(), key)
        if key in self._values:
            return copy.deepcopy(self._values[key])
        return copy.deepcopy(DEFAULTS[key])

    def get_for(self, profile: str, key: str):
        stored = f"{profile}.{key}"
        if stored in self._values:
            return copy.deepcopy(self._values[stored])
        return copy.deepcopy(default_for(profile, key))

    def is_set(self, key: str) -> bool:
        if key in PROFILE_KEYS:
            return self.is_set_for(self.active_profile(), key)
        return key in self._values

    def is_set_for(self, profile: str, key: str) -> bool:
        return f"{profile}.{key}" in self._values

    def set(self, key: str, value) -> None:
        if key in PROFILE_KEYS:
            self.set_for(self.active_profile(), key, value)
            return
        self._check(key, value)
        if key in self._values and self._values[key] == value:
            return
        if key == "profile":
            self._switch(lambda: self._store(key, value))
        else:
            self._store(key, value)
        self.emit(f"changed::{key}", key)

    def set_for(self, profile: str, key: str, value) -> None:
        if profile not in PROFILES or key not in PROFILE_KEYS:
            raise KeyError(f"{profile}.{key}")
        self._check(key, value)
        stored = f"{profile}.{key}"
        if stored in self._values and self._values[stored] == value:
            return
        before = self.get(key)
        self._store(stored, value)
        self.emit(f"changed::{stored}", stored)
        if self.get(key) != before:
            self.emit(f"changed::{key}", key)

    def reset(self, key: str) -> None:
        if key in PROFILE_KEYS:
            self.reset_for(self.active_profile(), key)
            return
        if key in self._values:
            if key == "profile":
                self._switch(lambda: self._values.pop(key))
            else:
                self._values.pop(key)
            self._save()
            self.emit(f"changed::{key}", key)

    def reset_for(self, profile: str, key: str) -> None:
        stored = f"{profile}.{key}"
        if stored in self._values:
            before = self.get(key)
            self._values.pop(stored)
            self._save()
            self.emit(f"changed::{stored}", stored)
            if self.get(key) != before:
                self.emit(f"changed::{key}", key)

    @staticmethod
    def _check(key: str, value) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        if key in CHOICES and value not in CHOICES[key]:
            raise ValueError(f"{key}={value!r} not in {CHOICES[key]}")

    def _store(self, key: str, value) -> None:
        self._values[key] = copy.deepcopy(value)
        self._save()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".app.", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._values, f, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class ProfileView(GObject.Object):
    """Settings as a page that edits one chosen profile sees them; other keys pass straight through.

    Same get/set/is_set/reset and ``changed::<key>`` signals as Settings, so a page's rows need no profile code.
    ``select()`` switches the edited profile and re-announces every key whose shown value changed.
    """

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST | GObject.SignalFlags.DETAILED, None, (str,)),
        "selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, settings: Settings, profile: str | None = None):
        super().__init__()
        self.base = settings
        self.profile = profile or settings.active_profile()
        self._handler = settings.connect("changed", self._relay)

    def _relay(self, _settings, key: str) -> None:
        profile, dot, name = key.partition(".")
        if dot:
            if profile == self.profile:
                self.emit(f"changed::{name}", name)
        elif key not in PROFILE_KEYS:
            self.emit(f"changed::{key}", key)

    def select(self, profile: str) -> None:
        if profile == self.profile or profile not in PROFILES:
            return
        before = {k: self.get(k) for k in PROFILE_KEYS}
        self.profile = profile
        self.emit("selected", profile)
        for k in PROFILE_KEYS:
            if self.get(k) != before[k]:
                self.emit(f"changed::{k}", k)

    def get(self, key: str):
        return self.base.get_for(self.profile, key) if key in PROFILE_KEYS else self.base.get(key)

    def set(self, key: str, value) -> None:
        if key in PROFILE_KEYS:
            self.base.set_for(self.profile, key, value)
        else:
            self.base.set(key, value)

    def is_set(self, key: str) -> bool:
        return self.base.is_set_for(self.profile, key) if key in PROFILE_KEYS else self.base.is_set(key)

    def reset(self, key: str) -> None:
        if key in PROFILE_KEYS:
            self.base.reset_for(self.profile, key)
        else:
            self.base.reset(key)

    def active_profile(self) -> str:
        return self.base.active_profile()

    def close(self) -> None:
        if self._handler:
            self.base.disconnect(self._handler)
            self._handler = 0


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def stopped_note(settings) -> str:
    """Off is set per power source, so plugging in or out can change it; muting is not the same and has no note."""
    if settings.get("listen_mode") != "off" or settings.get("muted"):
        return ""
    return f"Listening is off {'on the charger' if settings.active_profile() == 'charger' else 'on battery'}"
