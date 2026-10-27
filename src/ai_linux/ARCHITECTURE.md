# ai_linux (v3 desktop app): contracts

One GTK4/libadwaita process on system Python 3.14 + python3-gi. No webview, no Shell extension.
The speech loop runs in `engine/` as a child process (`python -m ai_linux.engine`, venv `~/.local/share/ai-linux/venv`).
`src/glados` (v2) stays only as the source the safety code was ported from; v3 imports nothing from it.

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
| `wake.py` | `strip_wake`, `heard`, `phrases`, `NameTest`, `rate`, `warnings` (gi- and numpy-free) |
| `models_catalog.py` | `ModelSpec`, `CATALOG` (small: jiwo-0.8b, laya (optional) · big: qwen3.5:4b, gemma4:e4b), `get`, `for_slot` |
| `ui/bar_model.py` | Pure bar animation: `BarAnimator.set_state/step/fps/is_settled`, `Frame`, `GEOMETRY`, `COLORS`, `fire_colors(dark)` (thinking and speaking, one colour per bar, sampled from the app icon; the rest stays blue), `level_from_rms`, `hex_to_rgb` |
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
| `desktop_entry.py` | `install()`, `uninstall()`; `python3 -m ai_linux.desktop_entry install` | app-grid launcher and icons under `~/.local/share`, no sudo |

## Rules
- Each builder owns its files and its own `ui/css/<area>.css`; shared files change only via the lead.
- Engine and network work off the main thread; results back via `GLib.idle_add` or `Bus`.
- Store only user-changed settings. No sudo at runtime. Licence: MIT; never copy GPL code (Alpaca, Newelle).
- Comments: one short line, the why. No em dash characters anywhere.

## Engine behaviour (2026-10-28)
- **Profiles:** `schema.py` PROFILE_KEYS have a battery and a charger value; `profile` = auto (follows `on_ac_power()`,
  polled every 5 s) | battery | charger. Charger default: no small model, the big model routes. Pages edit one profile
  through `ProfileView`; `ui/profile_group.py` switches it.
- **Mute** is the global `muted` setting, not a profile value, so a charger switch never unmutes. The client sends the
  effective `listen_mode` ("off" when muted).
- **Models:** jiwo (OpenVINO int8, Intel iGPU) picks the tool; arguments come from the tool's code parser, then jiwo for
  a fixed choice, then the big model (qwen3.5:4b on NVIDIA). A device, model or context change unloads the old model.
  Small models load through `engine/small_models.py` (`jiwo.py`, `laya.py`: both torch-free, `ask(state, question,
  options)`); downloadable OpenVINO models are listed in `downloads.OPENVINO_MODELS`.
- **Cues** (`engine/sounds.py`, pw-play): a finished action chimes instead of speaking (`action_feedback`); failures and
  answers are spoken; a chime when the big model takes over (`big_cue`).
- **Confirmations** show a dialog and, with audio, can be answered by voice (`spoken_confirm`); `confirm_done` closes it.
- **About you** (`user_name`, `about_me`) goes only into the big model's instructions. Default name/wake word: Phoenix.
- **Wake word** (`wake.py`, shared by app and engine): the name must start within the first 4 words; similarity
  0.75; a name split in two by speech recognition ("Dora Emon") also matches. **Name test** (Voice page, Test beside
  the wake word; `ui/name_test.py`): the user reads 5 sentences with the name and 1 without; the engine's `name_test`
  command transcribes one utterance and sends `{"ev": "heard"}` back without acting on it (expires after 20 s).
- **MCP** (`engine/mcp_client.py`, stdlib stdio JSON-RPC): each server's tools join as `<server>_<tool>`, gated as
  `mcp.<server>.<tool>` and confirmed unless the server sets `confirm: false`; reloaded live on a settings change.
- Measurements and model choices: `bench/README.md`.

## Open work (2026-10-28)
- Laya: uploaded to `itsDigvijaysing/laya-typed-decisions-openvino` (commit `c1014fc`, pinned); one full download
  check through the Models page is still to do (it was cut off by a weak connection).
- `SECURITY.md` still describes v2; `pyproject.toml` is still the v2 `glados` package.
- User-only: a live voice run on the real desktop (including the name test).
- Not yet compared with GLaDOS upstream since our 2026-06-15 copy (upstream `ea018f5`, 2026-10-10): ASR, VAD,
  routing. Upstream's routing tree (area, server, tool) isn't ported; v3 routes over one flat menu.
- `FUTURE_DIRECTION.md` predates v3 (it still says "keep the vendored GLaDOS").
