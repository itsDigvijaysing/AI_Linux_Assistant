# ai_linux (v3 desktop app): contracts

One GTK4/libadwaita process on system Python 3.14 + python3-gi. No webview, no Shell extension.
The old `src/glados` engine stays in the repo until the speech loop is ported in (phase 2).

## Run
- `bin/ai-linux-app` (window) · `--background` (tray only) · `--demo` (simulated turns) · `--toggle` / `--mute` / `--unmute` / `--quit` (forwarded to the running instance).
- Headless check, nothing on the real screen:
  `scripts/headless-run.sh bin/ai-linux-app --screenshot /path/out.png --page home --state listening`
  (throwaway XDG dirs and app id; tray, pill and hotkey are skipped in screenshot mode).

## Foundation (shared, owned by the lead; builders read, do not edit)
| Module | What |
|---|---|
| `config.py` | `APP_ID`, `ICON_NAME`, XDG paths, `Settings` (GObject; `get/set/is_set/reset`, signal `changed::<key>`), `DEFAULTS`, `CHOICES`, `get_settings()` |
| `state.py` | `AssistantState` enum, `ACTIVE_STATES`, `STATE_LABELS`, `Turn`, `ToolCall`, `Bus` (signals `state-changed(state, detail)`, `turn-added(turn)`, `turn-updated(turn)`, `notice(level, text)`; attributes `state`, `level` 0..1, `level_source` mic/tts, `followup_until`) |
| `context.py` | `AppContext(app, settings, bus, controller, history, ollama, tray, pill, hotkey, screenshot_mode)` |
| `controller.py` | `Controller`: `start/stop/enable_demo/set_listen_mode/mute/toggle_listening/push_to_talk/cancel/submit_text`, `engine_available`; `DemoDriver` |
| `models_catalog.py` | `ModelSpec`, `CATALOG` (small: qwen3.5-0.8b-jev, laya · big: qwen3.5:4b, gemma4:e4b), `get`, `for_slot` |
| `ui/bar_model.py` | Pure bar animation: `BarAnimator.set_state/step/fps/is_settled`, `Frame`, `GEOMETRY`, `COLORS`, `level_from_rms`, `hex_to_rgb` |
| `ui/theme.py` | `install(display)` loads every `ui/css/*.css` + icon path; `apply_color_scheme(settings, display)` |
| `app.py` | `App(Adw.Application)`; builds optional parts by exact name (below); a missing part is logged, never fatal |
| `devtools.py` | `render_to_png(widget, path)`, `screenshot(...)` |

## Component contracts (exact names `app.py` imports)
| Module | Class / function | Notes |
|---|---|---|
| `history_store.py` | `HistoryStore(path)` | sqlite3: `add(turn)`, `update(turn)`, `recent(limit)`, `search(text, limit)`, `days()`, `delete(id)`, `clear()`, `prune(days)` |
| `ollama.py` | `OllamaManager(ctx)` | GObject, signal `status-changed`; never blocks the main thread |
| `tray.py` | `Tray(ctx)` | `.available`, `.close()` |
| `ui/pill.py` | `Pill(ctx)` | `.available`, `.backend` (`x11` / `layer-shell` / None), `.close()`; follows bus + settings itself |
| `hotkey.py` | `Hotkey(ctx)` | `.available`, `.close()` |
| `ui/window.py` | `MainWindow(ctx)` | `show_page(name)`, `current_page`, `add_toast(toast)`; pages `home history models voice tools safety health`; each page gets a banner, so keep its child an `Adw.ToolbarView` |
| `ui/pages/<name>.py` | `Page(ctx)` | an `Adw.NavigationPage`; loaded lazily, failure shows an error page |
| `ui/preferences.py` | `show_preferences(parent, ctx)` | |
| `ui/onboarding.py` | `maybe_show(parent, ctx)`, `show(parent, ctx)` | shown once (`onboarding_done`); the dialog has `go_to(index, animate=True)` and `.closed` |
| `autostart.py` | `is_enabled()`, `set_enabled(bool)` | XDG autostart entry with `--background` |

## Rules
- Each builder owns its files and its own `ui/css/<area>.css`; shared files change only via the lead.
- Engine and network work off the main thread; results back via `GLib.idle_add` or `Bus`.
- Store only user-changed settings. No sudo at runtime. Licence: MIT; never copy GPL code (Alpaca, Newelle).
- Comments: one short line, the why. No em dash characters anywhere.
