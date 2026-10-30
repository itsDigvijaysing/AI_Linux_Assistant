"""Shared handles passed to every component instead of globals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppContext:
    app: Any  # ai_linux.app.App
    settings: Any  # ai_linux.config.Settings
    bus: Any  # ai_linux.state.Bus
    controller: Any  # ai_linux.controller.Controller
    history: Any = None  # ai_linux.history_store.HistoryStore, when available
    ollama: Any = None  # ai_linux.ollama.OllamaManager, when available
    tray: Any = None
    pill: Any = None
    hotkey: Any = None
    todoist: Any = None  # ai_linux.todoist_link.TodoistLink
    screenshot_mode: bool = False  # headless screenshots: no tray, pill, hotkey or autostart writes
    extras: dict = field(default_factory=dict)
