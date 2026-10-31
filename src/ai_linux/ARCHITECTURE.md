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
| `state.py` | `AssistantState` enum, `ACTIVE_STATES`, `STATE_LABELS`, `Turn`, `ToolCall`, `Bus` (signals `state-changed(state, detail)`, `turn-added(turn)`, `turn-updated(turn)`, `notice(level, text)`, `live-text(text, stable, final)`, `saying(text)`, `session-cleared()`, `wake-missed()`; attributes `state`, `level` 0..1, `level_source` mic/tts, `followup_until`, `live` (last live-text until the turn arrives), `session_start`) |
| `context.py` | `AppContext(app, settings, bus, controller, history, ollama, tray, pill, hotkey, screenshot_mode)` |
| `controller.py` | `Controller`: `start/stop/enable_demo/set_listen_mode/mute/toggle_listening/push_to_talk/cancel/submit_text`, `new_session/delete_session/restore_session`, `engine_available`; `DemoDriver` |
| `wake.py` | `strip_wake`, `heard`, `phrases`, `NameTest`, `rate`, `warnings` (gi- and numpy-free) |
| `models_catalog.py` | `ModelSpec`, `CATALOG` (small: jiwo-0.8b, laya (optional) · big: qwen3.5:4b, gemma4:e4b), `get`, `for_slot` |
| `ui/bar_model.py` | Pure bar animation: `BarAnimator.set_state/step/fps/is_settled`, `Frame`, `GEOMETRY`, `COLORS`, `fire_colors(dark)` (thinking and speaking, one colour per bar, sampled from the app icon; the rest stays blue), `level_from_rms`, `hex_to_rgb` |
| `ui/theme.py` | `install(display)` loads every `ui/css/*.css` + icon path; `apply_color_scheme(settings, display)`; `color_scheme(theme)`: light / dark force, system follows the desktop |
| `ui/caption_model.py` | The pill caption without GTK: `CaptionModel` (hidden / live / reply), `live_markup`, `scroll_target`, `edge_fades`, `reply_width`, `balanced_width`, `approach`, sizes and the `GLASS` colours |
| `app.py` | `App(Adw.Application)`; builds optional parts by exact name (below); a missing part is logged, never fatal |
| `devtools.py` | `render_to_png(widget, path)`, `screenshot(...)` |

## Component contracts (exact names `app.py` imports)
| Module | Class / function | Notes |
|---|---|---|
| `history_store.py` | `HistoryStore(path)` | sqlite3: `add(turn)`, `update(turn)`, `recent(limit)`, `search(text, limit)`, `days()`, `delete(id)`, `clear()`, `prune(days)` |
| `ollama.py` | `OllamaManager(ctx)` | GObject, signal `status-changed`; never blocks the main thread |
| `tray.py` | `Tray(ctx)` | `.available`, `.close()` |
| `ui/pill.py` | `Pill(ctx)` | `.available`, `.backend` (`x11` / `layer-shell` / None), `.close()`; follows bus + settings itself; frosted capsule plus a caption (`_Caption`) on the side away from the screen edge; light/dark from its display's `Adw.StyleManager` (class `ail-dark`, never a media query) |
| `hotkey.py` | `Hotkey(ctx)` | `.available`, `.close()` |
| `ui/window.py` | `MainWindow(ctx)` | `show_page(name)`, `current_page`, `add_toast(toast)`; pages `home history models voice tools safety health`; each page gets a banner, so keep its child an `Adw.ToolbarView` |
| `ui/pages/<name>.py` | `Page(ctx)` | an `Adw.NavigationPage`; loaded lazily, failure shows an error page |
| `ui/preferences.py` | `show_preferences(parent, ctx)` | General, Appearance, Privacy; no About tab (About is the sidebar's `Adw.AboutDialog`, `app._about`, with every credit) |
| `ui/onboarding.py` | `maybe_show(parent, ctx)`, `show(parent, ctx)` | shown once (`onboarding_done`); the dialog has `go_to(index, animate=True)` and `.closed` |
| `autostart.py` | `is_enabled()`, `set_enabled(bool)` | XDG autostart entry with `--background` |
| `desktop_entry.py` | `install()`, `uninstall()`; `python3 -m ai_linux.desktop_entry install` | app-grid launcher and icons under `~/.local/share`, no sudo |
| `credits.py` | `CREDITS`, `KINDS`, `by_kind()`, `ref_dir()`, `gtk_licence()` | every open-source credit (code, models, libraries, references); read by the About window (one section per kind, featured first) and `scripts/sync_refs.py` |
| `secret_store.py` | `lookup(name)`, `store(name, value, label)`, `clear(name)` | the login keyring over the freedesktop Secret Service (Gio only; libsecret's typelib isn't on a default Ubuntu); blocking, so worker threads only |
| `todoist_link.py` | `TodoistLink(settings, send_secret)`: `load`, `save(token, done)`, `disconnect(done)`, `.busy` | the Todoist token: checked with Todoist, kept in the keyring, handed to the engine; `ctx.todoist` |
| `mcp_presets.py` | `PRESETS`, `Preset.server()`, `get(key)` | Files, Git: fill the Add Server form; only read-only tools start on |
| `paste_portal.py` | `paste`, `authorize`, `detect`, `probe` (CLI); `PortalPaste`, `is_terminal()` | paste keys through the RemoteDesktop portal; system Python only |

## Rules
- Each builder owns its files and its own `ui/css/<area>.css`; shared files change only via the lead.
- Engine and network work off the main thread; results back via `GLib.idle_add` or `Bus`.
- Store only user-changed settings. No sudo at runtime. Licence: MIT; never copy GPL code (Alpaca, Newelle).
- Comments: one short line, the why. No em dash characters anywhere.

## Engine behaviour (2026-10-29)
- **Profiles:** `schema.py` PROFILE_KEYS have a battery and a charger value; `profile` = auto (follows `on_ac_power()`,
  polled every 5 s) | battery | charger. Since 2026-10-29 the models are one choice for both (`SHARED_NOW`: speech,
  small and big model and their devices, reasoning, escalation); a profile keeps only `speed`, `small_on` and the voice
  and pill keys. Charger default: Automatic, small model off (the big model routes). `Settings._migrate` moves old
  per-profile model values to one (battery's wins; "none" becomes `small_on` off; `big_model_policy` instant becomes
  speed full). The Voice page edits one profile through `ProfileView`; `ui/profile_group.py` switches it.
- **Mute** is the global `muted` setting, not a profile value, so a charger switch never unmutes. The client sends the
  effective `listen_mode` ("off" when muted).
- **Ollama:** Phoenix runs its own server on 127.0.0.1:11435 (`ollama_url` default) with the user's `~/.ollama`
  models, and `OllamaManager` starts it whenever it is down (at most once a minute; never after a manual Stop, in
  tests or screenshots). 11434 is often the official installer's system service (user `ollama`, empty store), which
  won the port after a reboot on 2026-10-30. A missing model is spoken as "isn't downloaded yet".
- **Models:** jiwo (OpenVINO int8, Intel iGPU) picks the tool; arguments come from the tool's code parser, then jiwo for
  a fixed choice, then the big model (qwen3.5:4b on NVIDIA). A device, model or context change unloads the old model;
  so does quitting (`Engine.stop()`, first, before MCP servers close), else Ollama keeps it for `keep_alive`.
  Small models load through `engine/small_models.py` (`jiwo.py`, `laya.py`: both torch-free, `ask(state, question,
  options)`); downloadable OpenVINO models are listed in `downloads.OPENVINO_MODELS`.
- **Cues** (`engine/sounds.py`, pure Python, pw-play; each is also given to the echo canceller): listen, ok, fail, unclear, big
  (a swell and a soft flame), sleep, timer. A finished action chimes instead of speaking (`action_feedback`; a tool's
  `Result.cue` can pick another sound); failures and answers are spoken. Called and then nothing said: the sleep
  sound when the follow-up window closes (`Engine.called`, `_check_followup`); spoken but no words heard: unclear.
  The Voice page's Sounds group previews each one (rendered to the runtime dir).
- **Quick rules** (`engine/quick.py`, at most 9 words, before routing; `route` = "rule", chip "Quick reply", no model):
  sleep words (stop, that's all, go to sleep, never mind, bye, good night), greetings, thanks, "what can you do",
  "play X on YouTube", small talk ("what are you doing", "how are you feeling", "are you there", "who are you": varied
  replies, `_CHAT`), "don't sleep", "that's wrong". Anything with content ("thoughts on electric cars") still routes.
  Kept facts' rules run there too.
- **Sleep:** `go_to_sleep` sets `Engine.sleep_after`, so the end of the turn goes idle instead of reopening the
  follow-up window. `wake.strip_wake`: words before the name are the request ("Stop Phoenix" = "Stop") unless they
  are call words (`CALL_WORDS`: hey, hello, wake up, ...).
- **Cold big model:** when it isn't in memory, it says "Waking up the big model." before loading (~8 s) and plays the big sound
  (`Brain._cue_big`). The line goes out as a "wait" item, so the state returns to thinking (ember glow) after it.
- **Chat or command** (`Brain.route`, 2026-10-29): offering the action tools to the big model on plain chat made it
  call them ("hi" ran `get_time`). Now the router's letters decide first: "none" at least `CHAT_SURE` (0.5, big model
  routing) or the escalation threshold (small model routing) answers with no action tools; memory tools only when
  `about_self(text)` ("My sister lives in Pune"). Unsure about an action (`ASK_P` 0.5 up to the
  threshold): "Did you mean volume, 30?" through the confirm dialog or by voice, acting only on a yes (`_ask_first`;
  shell and MCP tools confirm their command anyway). Handing the model the likely tools made it guess ("don't sleep"
  slept, "play any sound" set the volume; 2026-10-30 log: wrong tools sat at 0.4-0.65). Below that: an answer with no
  action tools. Each turn keeps the router's top 3 guesses (`alts`); "that's wrong" marks the previous request
  (`status` wrong, `turn_update` event); `bench/router/export_history.py` writes marked and unsure requests to
  `bench/data/review.jsonl` (git-ignored) for relabelling into `eval.jsonl`. A memory call is saved quietly (`_note`) and a second stream without tools answers; with "I'll remember
  that" in its history the model kept saving instead of answering. Measured with fake actions: 17 of 18 multi-turn
  replies good, about 0.7 s; letters 26 of 28 chat and 24 of 24 tasks right, native tool calling 13 of 28 chat.
- **Hearing** (`engine/service.py`): the wake-free window (hotkey, follow-up) is judged at the utterance's start
  (pre-roll adjusted), not its end, so a long sentence begun in the window counts. During a confirmation a reply that
  isn't yes or no and isn't junk becomes a new request (`("heard", text, t)` work item), not lost. The mic opens after
  start-up even if the state changed meanwhile (`_get_ready` always calls `apply_listen_mode`; a failed warm-up is
  logged, not fatal). `_drain` keeps timer rings when a turn is cancelled.
- **Echo cancel** (`engine/echo.py`, 2026-10-31; numbers in `bench/README.md`): nothing is loaded into PipeWire and no
  quantum is forced. The mic is the plain default source. While we make a sound the reader thread runs each frame
  through WebRTC's AEC3 (pywebrtc-audio) with that exact sound as the reference, in lock-step 10 ms blocks, plus 0.5 s
  after it ends; otherwise the frame passes untouched, so this can't deafen the mic. The reference is fed just before
  `pw-play` starts, so it leads the echo (measured lag 118-180 ms) and AEC3 finds the delay. Cues and the Voice page's
  samples are references too. Library missing or broken: `_aec_ok` is False and Phoenix doesn't listen while speaking.
  PipeWire's `module-echo-cancel` was dropped: in power saver it lost most near speech even with nothing playing.
- **Barge-in (pause, then verdict; LiveKit's false-interruption idea):** while speaking, 5 frames of the last 12 at
  VAD 0.3 pause the reply. The reply is held, not dropped: every recording made during a hold is transcribed, however
  long, and stops the reply only if its words aren't our last two sentences (`echo_of`, `_recent_speech`), else the
  sentence is said again. The old "8 voiced frames means the user" shortcut skipped that check, and our own words
  became requests. After 2 echo pauses in one turn barge-in sits out the rest of that reply. A recording that starts
  within `ECHO_TAIL_S` of our last word is dropped if it is our own words (`own_words`). Each reply logs
  `while speaking: voice up to X, N of 12 frames`.
- **Confirmations** show a dialog and, with audio, can be answered by voice (`spoken_confirm`); `confirm_done` closes it.
  The pill shows the whole command ("Run this? <cmd> Say yes or no.") while the voice says "this command"
  (`Engine._say(text, shown)`).
- **Name:** the app is **Phoenix** wherever people see it (`paths.APP_NAME`: window, desktop entry, autostart, About,
  onboarding, tray, shortcut dialog). App id, folders, the `ai_linux` package and `bin/ai-linux-app` keep ai-linux.
- **Knowledge** (`knowledge.py`, gi-free, `~/.local/share/ai-linux/knowledge.json`, 0600, flock + re-read on mtime so
  app and engine share it): up to 20 kept facts (stay until deleted) and 30 learned ones (fade 14 days after their last
  update unless kept; the oldest goes when a 31st comes). The big model alone gets `remember` / `forget`
  (`tools.MEMORY_TOOLS`, family "assistant", not gated, never on the small model's routing menu) and the facts in its
  instructions, capped at 2400 characters, kept first. `user_name` stays a setting. The old `about_me` text is split
  into kept facts once at start (`migrate_about_me`). UI: `ui/knowledge.py`, a sidebar entry under Preferences; the
  engine's `{"ev": "knowledge"}` refreshes it.
- **Home** (`ui/pages/home.py`): one status row (bars in the `row` geometry, state, hint) with the controls on the
  right: a listen-mode dropdown (Wake word / Hotkey / Always on) and one Start/Stop button (Stop = the `muted`
  setting via `controller.mute`; also stopped when a profile's `listen_mode` is off). The header holds one row of
  three toggles (Save Battery, Automatic, Max Performance) that sets the speed of the profile in use; a progress bar shows while starting or waking the big model. It wraps on a narrow window. The empty state
  offers three example requests (sent as text); the stats line hides until there is a request. User-facing text says
  "request", not "turn". With no engine, a notice says it isn't installed and opens Health.
- **Speed** (`speed`, per profile, `schema.SPEEDS`): Save Battery = efficient (big model on demand, freed 2 min after),
  Automatic = auto (on demand, kept 30 min), Max Performance = full (kept loaded). Battery default efficient, charger
  auto. The engine never sees `speed`: `EngineClient.model_values()` sends `schema.loading(speed)`
  (`big_model_policy`, `ollama_keep_alive`) and `small_model` = "none" when the profile's `small_on` is off. Leaving
  instant unloads the big model.
- **Models page** (`ui/pages/models.py`, `models_overview.py`), top to bottom: Running Now (settings in use, speech,
  small and big model state from Ollama); Battery and Charger (a two-column table with only Speed and "Small model
  picks commands", each column edits its profile through `settings.set_for`, the one in use is marked, a line explains
  the speed in use); Ollama; Speech Recognition, Small Model, Big Model (pick, download, Runs On; one choice for both
  profiles); Advanced (Reasoning, Escalation Threshold, Context Length); Storage (Ollama models on disk, delete).
- **Starting:** the engine stays `loading` until speech recognition is warm and `Brain.warm()` (small model, plus the
  big one when kept loaded) is done; the detail names the step ("Starting speech recognition", "Loading the small
  model", "Loading the big model"). The mic opens only then; the hotkey and push-to-talk send a "still starting"
  notice. The pill shows while starting.
- **Big model at work:** once per turn, when the big model routes, fills arguments or answers, the state becomes
  `thinking` with detail `schema.BIG_WAKING` (not in memory yet) and then `BIG_WORKING` at its first output; only while
  still thinking. The bars play their "big" animation (all bars breathe together, ember colours); the pill capsule gets
  the `ail-big` class (an ember glow) until the turn's speaking ends; the "big" chime plays if it was asleep or the
  small model routed (`big_cue`).
- **Colours** (`ui/bar_model.palette`): you talking and the assistant listening in GNOME blues (idle, follow-up,
  listening), its reply in the phoenix fire, thinking a fire sweep over the blue, starting steel to blue; every state
  change blends the colours over 0.45 s (`COLOR_BLEND_S`).
- **New Session** (Home's header): the engine's `new_session` command clears the model's conversation; Home shows only
  turns after `bus.session_start`; History keeps everything. Its menu has **Delete This Session**
  (`controller.delete_session`): the turns on Home leave History too, then New Session; the toast's Undo
  (`restore_session`) puts them back and resets `session_start`, but the model stays fresh. Learned facts stay.
- A bare wake word in a wake-free window (after the hotkey, or in follow-up) opens follow-up instead of reaching the
  model (it once changed the voice). Speech without the wake word outside such a window is dropped by design; unless it's junk
  ("um", "okay"), the engine sends `{"ev": "no_wake"}` and Home's hint says "Heard you. Say “phoenix” first" for 4 s.
  Default name/wake word: Phoenix.
- **Wake word** (`wake.py`, shared by app and engine): the name must start within the first 4 words; similarity
  0.75; a name split in two by speech recognition ("Dora Emon") also matches. **Name test** (Voice page, Test beside
  the wake word; `ui/name_test.py`): the user reads 5 sentences with the name and 1 without; the engine's `name_test`
  command transcribes one utterance and sends `{"ev": "heard"}` back without acting on it (expires after 20 s).
- **Dead mic** (`engine/mic_watch.py`): 5 s of digital silence (peak at most 4 of 32768) or no frames while capturing
  first reopens the capture once, quietly (OpenWhispr's re-acquire), then sends an error notice naming the desktop's
  input (`wpctl inspect @DEFAULT_AUDIO_SOURCE@`); a working mic after echo cancelling never measured below 51. Seen
  2026-10-28: the USB card's profile flipped to its S/PDIF input.
- **Paste** (`engine/tools.py` `paste_text`, OpenWhispr's GNOME order): clipboard, then the RemoteDesktop portal if a
  restore token is saved (`~/.local/state/ai-linux/portal-paste-token`), else ydotool when its daemon runs, else the
  portal with its one-time permission dialog, else "press Control V". Terminals (found through AT-SPI) get
  Ctrl+Shift+V. The portal part is `paste_portal.py`, ported from OpenWhispr's `linux-fast-paste.c`; it runs on the
  system Python because the engine venv has no gi.
- **MCP** (`engine/mcp_client.py`, stdlib stdio JSON-RPC): each server's tools join as `<server>_<tool>`, gated as
  `mcp.<server>.<tool>` and confirmed unless the server sets `confirm: false`; reloaded live on a settings change.
  After starting them the engine sends `{"ev": "mcp_tools", "servers": {name: [{name, description}]}}` (`bus.mcp_tools`,
  signal `mcp-tools-changed`); the Tools page lists each server's tools with a switch (`disabled_tools` in the server
  entry). Only switched-on tools reach the menus, and a change to only `disabled_tools` re-filters them without
  restarting the servers (`Engine._filter_mcp`). Presets: `mcp_presets.py`. A server's description reaches the menu
  as its first sentence, at most 120 characters (`mcp_client.menu_line`; the filesystem server's run to 450).
- **Speech device** (`stt_device`, one value for both profiles; Voice page's first group and the Models page): Parakeet TDT's encoder runs on the Intel GPU through OpenVINO
  (`engine/asr/ov_encoder.py`), the decoder and joiner stay on the CPU. Input is padded to whole seconds (each new
  length costs ~0.6 s of compiling, cached in `~/.cache/ai-linux/openvino`, ~1.3 GB) and every length up to 15 s is
  warmed in the background at start (first ever ~10 s + ~11 s, later ~1.3 s + ~4 s). Any failure falls back to the CPU
  and the `{"ev": "model", "name": "speech"}` event says why. CTC always runs on the CPU.
- **Live words** (`engine/live_text.py`, `live_text` setting): while recording, the utterance so far is decoded again
  about every 0.6 s (slower if a decode takes longer), one decode at a time, under the same lock as the final decode.
  `{"ev": "partial", "text", "stable", "final"}`: `stable` counts the leading words two updates agree on. Without the
  wake word nothing is shown; once the name is heard the state turns to listening at once. Always-on with "Ignore
  other talk" shows nothing until the sentence is judged. The heard sentence goes out with `final: true` before
  thinking; a rejected one is cleared with an empty final. `{"ev": "saying", "text", "seconds"}` comes before each
  spoken sentence. The pill and Home (a lighter user bubble at the end of the list) show them. A reply that doesn't
  fit the pill turns pages in step with the voice (`ui/caption_model.pages` balances them, `page_starts` spreads
  them over `seconds`); the card grows at once so no line is clipped.
- **Timers** (`engine/timers.py`, tool `set_timer`): kept in `STATE_DIR/timers.json`, one `threading.Timer` each, re-armed at least every 30 s
  (`CHECK_S`) so a reminder due during suspend rings on resume; a
  ring plays the timer sound, sends `notify-send` and speaks the message even when muted (`("ring", text)` on the
  speech queue). One missed by under an hour while the app was closed rings at start; longer than 7 days is capped.
  Spans parse number words and halves ("an hour and a half" = 90 min); a bare hour already past ("at 5", 17:00) means
  the evening; "cancel the timer" with several running asks which (`which`: its length or message, or all).
- **Web Access** (`web_access`, off by default; `weather_city`): `engine/web.py` (stdlib, 6 s timeout, fixed https
  hosts): weather from Open-Meteo, look-up from DuckDuckGo Instant Answer then Wikipedia. While off, `WEB_TOOLS` are
  left off every routing menu, so questions still reach the big model; called anyway they say how to turn it on.
- **Todoist** (`engine/todoist.py`, API v1, stdlib, 8 s timeout; tool `todoist`, family assistant, not gated): add
  with Quick Add (Todoist reads the date from the sentence, so the model only picks the tool), list today / tomorrow
  / overdue / this week, complete after "Mark ... as done?" (`Tool.asks`, so an unsure router doesn't ask twice). On
  the menus only while the engine holds a token. The token lives in the login keyring (`secret_store.py`); only the
  `todoist_connected` flag is a setting, so the keyring is opened only for people who connected. The app sends it as
  `{"cmd": "secrets"}` (engine memory only, re-sent to every new engine by `Controller.secrets`). Independent of
  Web Access: saving a token is the opt-in.
- **App tools** (`engine/apps.py`): `installed_apps` (all apps by group, or what one is for, from the .desktop
  Comment), `list_folder` (newest items of a known folder), `switch_to_app` (D-Bus Activate, else `gtk-launch`),
  `close_app` (D-Bus `quit` action, else `flatpak kill`, else `pkill -TERM -x` for this user; web apps refused).
- **Top-bar icon** (`tray.py`, StatusNotifierItem): `IconName` + `IconThemePath` = `data/icons`, files
  `phoenix-tray-*-symbolic.svg` (`scripts/gen_tray_icons.py`) so the shell paints them in the panel's text colour:
  solid while working (a wave cycling at 5 fps while listening, thinking, speaking), faint while waiting, flat dots
  when stopped. The pixmap is only a fallback, drawn once per state.
- **Dictation** (`engine/dictation.py`, 2026-10-31): "Phoenix, dictate" (optionally followed by text), "start
  dictating", "let's dictate", "can you dictate", "I want to dictate" and the like, with fillers before them, turn it on
  with a chime and no model reply. A sentence that only talks about it ("dictate is not working", "can you dictate this
  letter") does not. A short one gets a fixed how-to from `engine/quick.py`, never the model, which once answered "I am
  ready to listen" with dictation off. What is said is collected (a pause ends a recording only after
  `DICTATE_END_SILENCE`, ~1.5 s, not 0.7 s) until "stop dictating" ("stop the dictation", "end dictation", "done
  dictating", the mishearing "stop dictated"; also at the end of a sentence). `DICTATE_IDLE_S` (5 min of silence) only
  ends a forgotten one, with a notice; the mic closing ends it at once. Past ~10 s a recording ends at the next ~0.25 s
  pause (`DICTATE_CUT_FRAMES`), so the 15 s stop rarely cuts a word. Words said over a timer's ring stay dictation. Typing
  "stop dictating" ends it too. Then the whole text is cleaned once (fillers out; spoken "comma", "full stop", "new line", "new paragraph", "question
  mark"), rewritten in one call to the big model (`Brain.polish`, temperature 0, no tools; grammar, fillers, false starts,
  self-corrections and rambling, with a prompt adapted from OpenWhispr's and the words sent as a transcript) and typed once
  through `paste_text`, never acted on. While it is on, speech skips wake-word stripping, so "Phoenix" in a sentence
  stays text; only "stop dictating" (with the name, "please" or "thanks" around it) and the name alone are not typed.
  The limit's end is queued to the Work thread (the model must not hold up the mic) and survives short noises and a
  timer ringing. `dictation.pick` keeps the words as said when the rewrite is empty, much longer or shorter, lost a
  question or changed the line breaks (a "?" on the same words is a fix, and dashes become commas); without the big model
  it types them as said.
  "Type what I say" / "type exactly" skips the model. Only Cancel drops what was collected, stops the model and types
  nothing; the hotkey during the wording fix lets it finish. Speech begun while dictating is never a request, even
  when dictation ended before it was transcribed (`_dictation_over`). Needs actions allowed (it types into other apps).
- **Short recordings** (`engine/service.py`, 2026-10-31): while waiting for the name, 2 voiced frames (`MIN_NAME`) are
  enough to transcribe, since only the name can act on the words. On the plain mic a quick "Phoenix" scored 3, 6 and 7
  frames: live words recognised it, the state lit up, then the old limit of 8 threw it away (0 such drops in 18 bare
  names on the old chain, 3 in 5 on the new; why it scores lower isn't traced, recordings aren't saved). In a window, or
  always on, `MIN_SPEECH` (8) stays, because there any words go to the model.
- **Names that sound alike** (`wake.py`, 2026-10-31): a name also matches when it is spelled differently but said the
  same (`_sound`: ph, oe, ee), since a bare "Phoenix" came back from Parakeet as "Fenix." and was ignored. Spelling
  distance stays as it was, so "Felix" doesn't wake it.
- **Mic and state after a turn** (`engine/service.py`, 2026-10-31): `_rest` gives the mic and state the listen mode
  asks for (hotkey and off close the mic; off says muted). `_settle` goes back to what is still going on (the hotkey
  or hold-to-talk, a window, else `_rest`): after a timer's ring, a noise or filler, the mic closing mid-sentence, or
  the end of dictation. A follow-up window closing, Cancel, a failed turn and refused dictation use `_rest`; Cancel
  also closes the no-name window. `listen_now` running out now works with frames flowing (it used to be checked only
  on an empty queue, which a live mic never has), and releasing hold-to-talk keeps what was recorded wake-free
  (`PTT_END_S`; it was dropped unless the name was said).
- Measurements and model choices: `bench/README.md`.

## Open work (2026-10-29)
- Routing re-measured on 2026-10-29 for chat (above); a full task re-run on the 27-tool menu is still open.
  Voice-not-heard follow-ups: follow a changed default mic, retry an empty transcription.
- **Not committed yet:** everything since `a6a1c9c` (live words, pill caption, Dark theme, Knowledge, load modes,
  starting and big-model effects, colour blends, New Session, the Phoenix rename, the bare-wake-word and shortcut-text
  fixes, and the 2026-10-29 Home row, Speed and Battery, Delete This Session, About and Tools changes, and batches 2 and 3: sleep and quick rules, sounds, pill
  paging, new tools, Web Access, Tools categories, voices, top-bar icons, Models overview; and batch 4: chat or
  command, hearing and barge-in fixes, 14 review fixes, Speed per profile with one model choice, Models page redesign). Ask before committing.
- Laya: uploaded to `itsDigvijaysing/laya-typed-decisions-openvino` (commit `c1014fc`, pinned); one full download
  check through the Models page is still to do (it was cut off by a weak connection).
- `pyproject.toml` is still the v2 `glados` package.
- User-only: a live voice run on the real desktop (including the name test), and a look at the new pieces live:
  the pill while starting and with the big model's glow, the colour blends, Home's row, Mute, New Session and
  Delete This Session (with Undo), the Speed row on Home and the Models page, the About window's credits, the
  Knowledge dialog (remember/forget by voice), and everything in the Open list of `MD/HANDOFF.md` (local only). The pill capsule is anchored to the window's edge (`_column` valign
  END/START), so a growing caption should not move it; check that live too.
- GLaDOS upstream (`ea018f5`) compared on 2026-10-28: ASR and VAD changed only in ONNX backend choice (see
  `REFERENCES.md`). Upstream's routing tree (area, server, tool) isn't ported; v3 routes over one flat menu.
