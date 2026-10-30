# Handoff: where Phoenix stands (2026-10-30)

Start a new session with: "Read `HANDOFF.md`, then `src/ai_linux/ARCHITECTURE.md`." `CLAUDE.md` (rules, test commands)
loads by itself. This file says what was done last, what is uncommitted and what is open; ARCHITECTURE.md has the
details of each piece.

## State
- Last commit `eed63fc` (the user committed the four 2026-10-29 batches; `VERSION` still 3.1.1). **2026-10-30 work
  below is uncommitted**; 3.2.0 suggested. The user decides when to commit (ask each time; no AI attribution).
- Tests: `/usr/bin/python3 -m unittest discover -s tests` gives 393 OK, 3 skipped (2026-10-30). Engine venv (108 OK):
  `~/.local/share/ai-linux/venv/bin/python -m unittest tests.test_laya tests.test_engine tests.test_new_tools`
  (`test_knowledge` needs gi, so it runs on the system Python only).
- Ollama: Phoenix now starts its own server on 11435 (`ai-linux-ollama` user unit). The system `ollama.service`
  (official installer, user `ollama`, empty store) keeps 11434; `ollama` on the command line talks to that one unless
  `OLLAMA_HOST=127.0.0.1:11435`.
- A manual `pw-metadata -n settings 0 clock.force-quantum 480` was set on 2026-10-30 while testing; the engine now
  asks for 480 itself, so reset it to 0 after Phoenix restarts (it also resets on reboot).
- **How the user wants replies:** short key-point bullets; clarify every doubt (AskUserQuestion) before coding.

## Done on 2026-10-30, cloud session (branch `cloud/project-review`, draft PR #1; VERSION 3.3.1)
| Change | Files |
|---|---|
| 13 review fixes with failing-test-first proof (speech tied to its turn, cancel ends an on-screen confirm, echo cancel loaded once, MCP leaks, timers across suspend, denylist `/home`, muted listen loop sleeps, ...) | see the PR table |
| Volume and brightness accept "40%", "fifty" and floats from the big model (it said "Sorry, something went wrong") | `engine/tools.py` (`_level`) |
| Todoist: add, read, finish tasks; token in the login keyring; Tools page section | `engine/todoist.py`, `engine/tools.py`, `secret_store.py`, `todoist_link.py`, `ui/pages/tools.py` |
| MCP: a switch per tool (fewer tools on the menu), presets Files / Git | `engine/mcp_client.py`, `engine/service.py`, `mcp_presets.py`, `ui/pages/tools.py` |
| Live checks still open: the keyring's unlock dialog (auto-login), a real Todoist token, a preset server started with npx/uvx, routing accuracy with Todoist and MCP tools on the menu | |

## Done on 2026-10-30 (uncommitted)
| Change | Files |
|---|---|
| Not hearing after the first reply: PipeWire ran the WebRTC echo cancel at 256-sample cycles (its block is 480); one CPU spike desynced it for good and it passed 2 to 18 of ~140 speech frames. The module now forces 480 (`node.force-quantum`); live check passed | `engine/audio.py` (`AEC_QUANTUM`) |
| Big model "not found" after reboot: the official installer's system `ollama.service` (empty store) took 11434. Phoenix's own server on 11435, auto-started by the app when down; "isn't downloaded yet" spoken for a missing model | `ollama.py` (`DEFAULT_URL`, `_maybe_autostart`), `schema.py`, `engine/brain.py` |
| Small talk without the big model ("what are you doing", "how are you feeling", "are you there", "who are you", ...), "don't sleep", "Hello?" | `engine/quick.py` (`_CHAT`, `_AWAKE`) |
| Wrong actions: below the 0.85 threshold the big model got 4 likely tools and called one on a guess. Now an unsure action (0.5 up to the threshold) asks "Did you mean ...?" (dialog or voice, Yes/No); lower answers with no action tools | `engine/brain.py` (`ASK_P`, `_ask_first`), `engine/service.py` (`detail` in confirm), `app.py` (`_confirm_finish`) |
| Mistake log: top 3 router guesses per turn (`alts`), "that's wrong" marks the previous turn, export of marked and unsure turns to `bench/data/review.jsonl` (git-ignored) | `state.py` (`Turn.alts`, tolerant `from_dict`), `engine_client.py` (`turn_update`), `bench/router/export_history.py` |

## Learned on 2026-10-30
- Echo cancel: a fresh module passed 136 of 143 speech frames, the engine's passed 2; after a 15 s CPU load a fresh
  one broke the same way; forcing 480 stopped the dropouts at once. Claude, OpenWhispr and whisper-flow use the raw
  mic (no AEC) because they never speak; Phoenix needs AEC only for barge-in.
- 2026-10-30 log: of 40 big-model routes, wrong tool choices sat below 0.65 (6 of 11); above it the tool was right and
  misses were arguments or mishearing. Web check: ask-when-unsure is standard (Lex top-5 + disambiguation, conformal
  clarification); hierarchical routing evidence is mixed (errors move to the family step), so it was not built.
- Browser actions all ran but opened behind other windows: GNOME blocks raising without an xdg-activation token.

## Done on 2026-10-29, batch 4 (user-approved)
| Change | Files |
|---|---|
| Chat no longer runs tools: the router's "none" decides first; action tools only when unsure (top 4), memory tools only for statements about the user; a memory call is saved quietly and a second stream answers | `engine/brain.py` (`CHAT_SURE`, `about_self`, `_note`, `_converse(tools)`) |
| Not hearing after the first request: wake-free window judged at the utterance's start; a non-yes/no reply during a confirmation becomes a new request; mic opens after start-up even if the state changed | `engine/service.py` (`_wake_free(at)`, `("heard", ...)` work items, `_get_ready`) |
| Barge-in: 5 of 12 frames at VAD 0.3 pause the reply; speech stops it, our own echo resumes it (sentence said again, max 2 per turn) | `engine/service.py` (`BARGE_*`, `echo_of`, `_barge_verdict`) |
| Reply caption: words fade before the card collapses (it cropped them on every side) | `ui/pill.py` (`_Caption._on_tick`) |
| "One moment" keeps the thinking state and ember glow afterwards | `engine/service.py` (`_say_wait`), `engine/brain.py` |
| Ultra-review: 14 of 15 findings valid and fixed, plus the 7 small items (timer parsing and cancel, switch/close safety, portal timeout, forget/merge in Knowledge, cached app list, tray redraw) | `engine/tools.py`, `engine/timers.py`, `engine/apps.py`, `knowledge.py`, `paste_portal.py`, `wake.py`, `tray.py` |
| Models are one choice for battery and charger; per profile only Speed and "Small model picks commands" (old settings migrate) | `schema.py` (`SPEEDS`, `loading`, `SHARED_NOW`), `config.py` (`_migrate`), `engine_client.py` (`model_values`), `ollama.py`, `doctor.py` |
| Home: one row Save Battery / Automatic / Max Performance in the header (speed of the profile in use) | `ui/pages/home.py` |
| Models page redesigned: Running Now, Battery and Charger table (2 rows), Ollama, the three models, Advanced, Storage; Voice page's speech rows moved to the shared first group | `ui/pages/models.py`, `ui/pages/models_overview.py`, `ui/pages/voice.py` |
| Tests: migration, `model_values`, Running Now, `loading`, review fixes | `tests/test_settings.py`, `test_knowledge.py`, `test_engine.py`, `test_new_tools.py` |

## Learned on 2026-10-29 (batch 4)
- Wrong answers came from offering 25 action tools on every chat turn; qwen3.5:4b's native tool calling picks a tool
  for 13 of 28 plain chat lines, its one-token letters only 2 of 28.
- With "I'll remember that" in the history the model keeps calling `remember` instead of answering.
- Echo cancelling (2026-10-30): settled, our voice peaks at VAD 0.12; just after a restart it leaks (3 false barge-ins
  at 0.96-0.99). During double talk it drops ~half the user's frames, so 0.5 / 8 of 12 missed most short phrases.
- Not hearing: confirmations swallowed the next request, and the follow-up window was judged when a sentence ended.

## Done on 2026-10-29, batches 2 and 3 (user-approved)
| Change | Files |
|---|---|
| "Phoenix, then blank": VAD 0.5 while it listens (0.8 only while it speaks, for barge-in); each utterance and state change is logged (`recorded 1.2s, 30 voiced frames, peak ...`) | `engine/service.py`, `engine/audio.py` (VAD probability in the queue) |
| Voice previews play through the echo-cancel sink, so the mic doesn't hear them | `ui/pages/voice.py` (`AEC_SINK`) |
| Sleep works: "stop", "that's all", "go to sleep", "never mind", "bye" need no model; the turn's end no longer reopens the follow-up window (`sleep_after`) | `engine/quick.py`, `engine/service.py` (`_sleep`, `_speak_loop`) |
| "Stop Phoenix" / "Thanks Phoenix": words before the name are the request ("Hey/Hello/Wake up Phoenix" still just calls it) | `wake.py` (`CALL_WORDS`, `strip_wake`) |
| Rules before any model (`route` = "rule", chip "Quick reply"): greetings, thanks, "what can you do", "play X on YouTube" | `engine/quick.py`, `engine/brain.py` (`_handle`), `ui/widgets/conversation.py`, `css/window.css` |
| Cold big model says "One moment." (load ~8 s), with the rising chime and the pill's ember glow | `engine/brain.py` (`_cue_big`) |
| Sounds redone, pure Python (no numpy, so the app can preview): listen, big (swell + flame), ok, fail, unclear, sleep, timer. Called and nothing said: "sleep" sound when the window closes; spoken but no words: "unclear" | `engine/sounds.py`, `engine/service.py` (`called`, `_check_followup`), Voice page "Sounds" group |
| Confirmations: the pill shows the whole command ("Run this? ... Say yes or no.") while the voice says "this command" | `engine/service.py` (`_say(text, shown)`) |
| Long replies: the pill turns pages in step with the voice (engine sends `seconds`); cards grow at once, so lines are never clipped; fewer lines with large text | `ui/caption_model.py` (`pages`, `page_starts`), `ui/pill.py`, `state.py` (`saying` has seconds) |
| New tools: installed apps (+ what one is for), what's in a folder, switch to / close an app, timers and reminders (kept across restarts), weather (Open-Meteo), quick look-up (DuckDuckGo, Wikipedia) | `engine/tools.py`, `engine/apps.py` (`comment`, `exe`, `categories`, `pwa`, `on_bus`, `running`, `summary`), `engine/timers.py`, `engine/web.py` |
| Web Access: off by default (`web_access`, `weather_city`); while off, weather and look-up are off the menus so questions still reach the big model | `schema.py`, `engine/brain.py` (`tools`), `engine/tools.py` (`WEB_TOOLS`) |
| Tools page: 7 collapsed categories, tag "Changes your PC" (was "Needs actions armed"), "Online" tag, Web Access switch + home city inside its category | `tools_catalog.py` (`CATEGORIES`, `category`, `online`), `ui/pages/tools.py` |
| Home: one Start/Stop button (Stop = mic off); speed menu in the header; progress bar while starting or waking the big model; "Max Performance" (was Full) | `ui/pages/home.py` |
| Models: "Running Now" and "Battery and Charger, Side by Side" (editable columns); stale "on the CPU" texts fixed | `ui/pages/models_overview.py`, `ui/pages/models.py`, `models_catalog.py`, `ui/onboarding.py` |
| Profile wording: Switch with the Charger / Battery Settings Always / Charger Settings Always | `ui/profile_group.py` |
| Top bar: named symbolic icons the shell paints in the panel's text colour: solid at work (wave at 5 fps while listening, thinking, speaking), faint while waiting, flat dots when stopped. The pixmap stays only as a fallback, drawn once per state | `tray.py` (`icon_name`), `data/icons/phoenix-tray-*-symbolic.svg`, `scripts/gen_tray_icons.py` |
| Voice: female default (F1); all 9 SuperTonic voices with samples | `schema.py`, `engine/tts.py`, `ui/pages/voice.py`, `data/samples/*.ogg`, `scripts/gen_voice_samples.py` |
| Welcome tour shows the Phoenix icon; tray menu "Talk Now" / "Stop Listening" | `ui/onboarding.py`, `tray.py` |
| Tests: `tests/test_new_tools.py`; sleep, rules, "One moment", unclear, web menus, confirm display in `test_engine.py`; tray icon names | |

## Learned on 2026-10-29 (batches 2 and 3, from `history.db` and the code)
- Every logged turn went to the big model: "hi" ran `get_time`, "what can you do" ran `go_to_sleep`, sleep commands
  ran but the follow-up window reopened at the end of the turn, an apps question made up a `run_command`.
- "Stop Phoenix" was read as calling it (only words after the name counted).
- Long replies were cut at 4 lines with "..."; the clipped top and bottom lines came from the card growing slower
  than the new text appeared.
- The AppIndicator extension loads a named icon from `IconThemePath` before the pixmap; files ending in
  `-symbolic.svg` take the panel's colour.
- The routing menu grew from 20 to 27 tools (bench M26 had similar ones: timer, weather, switch window).

## Done on 2026-10-29, batch 1
| Change | Files |
|---|---|
| Home: one status row (bars left, controls right; wraps when narrow); new bar geometry `row` (batch 2: Start/Stop) | `ui/pages/home.py`, `ui/bar_model.py`, `ui/bars.py`, `css/window.css` |
| Load mode moved off Home to Models, "Speed and Battery": Save Battery / Automatic / Max Performance (replaced in batch 4: Speed per profile, Home row) | `ui/pages/models.py` |
| Delete This Session (New Session's menu): leaves History too, Undo toast | `ui/pages/home.py`, `controller.py` (`delete_session`, `restore_session`), `state.py` (`restore_session`) |
| About in one place: Preferences' About tab removed; the About window lists every credit by kind | `ui/preferences.py`, `app.py` (`_about`), `credits.py`, `css/health-prefs.css` |
| Engine notice: "isn't installed" + Open Health (was the stale "--demo" text) | `ui/pages/home.py` |
| Wake-word hint: dropped speech sends `no_wake`; Home shows "Heard you. Say “phoenix” first" for 4 s | `engine/service.py`, `engine_client.py`, `state.py` (`wake-missed`), `ui/pages/home.py` |
| Empty Home: 3 example requests; stats line hidden until there is one; "turn" says "request" in Home and History | `ui/pages/home.py`, `ui/pages/history.py`, `ui/widgets/conversation.py` |
| Tools page lists Remember and Forget | `tools_catalog.py`, `tests/test_tools_catalog.py` |
| Tests: `tests/test_session.py` (delete, undo, signal), two wake-hint tests in `test_engine.py` | |

## Learned on 2026-10-29
- "Whisper" in the app: there is none. Speech recognition is Parakeet TDT. whisper-flow and OpenWhispr gave plumbing
  (live words, paste at the cursor, mic reopen), not tools.
- VS Code's Claude panel can't open `file:///` links (its markdown filter blanks them on Linux); plain absolute or
  relative path links work. Rule noted in `/home/king/CLAUDE.md`.

## Done on 2026-10-28 (where to look)
| Feature | Files |
|---|---|
| Live words while you talk, the pill's caption card, Home's live bubble | `engine/live_text.py`, `ui/caption_model.py`, `ui/pill.py`, `ui/pages/home.py` |
| Dark theme (Light / Dark / System) | `ui/theme.py`, `ui/preferences.py`, `css/pill-safety.css` |
| Speech recognition on the Intel GPU (`stt_device`) | `engine/asr/ov_encoder.py`, `ui/pages/voice.py` |
| Knowledge: kept and learned facts, remember/forget tools, dialog | `knowledge.py`, `engine/tools.py` (`MEMORY_TOOLS`), `engine/brain.py`, `ui/knowledge.py` |
| Load mode on Home (Efficient / Auto / Full; replaced in batch 4) | `schema.py`, `engine_client.py`, `ui/pages/home.py` |
| "Starting" until everything is ready | `engine/service.py` (`_get_ready`, `_starting`), `brain.warm(step)` |
| Big model's weight: status, bars "big" pulse, pill ember glow, chime | `brain.py` (`_cue_big`, `_working`), `schema.py` (`BIG_WAKING`, `BIG_WORKING`), `ui/bars.py`, `ui/bar_model.py`, `ui/pill.py`, `css/pill-safety.css` |
| Colours: blue for you, phoenix fire for its reply, 0.45 s blends | `ui/bar_model.py` (`palette`, `COLOR_BLEND_S`) |
| New Session (Home header) | `engine/service.py` (`new_session`), `controller.py`, `state.py` (`session_start`), `ui/pages/home.py` |
| Rename to Phoenix (only what people see) | `paths.APP_NAME`, window, onboarding, desktop entry, autostart, pill, health, ollama |
| Fixes: a bare "Phoenix" changed the voice; the Voice page's shortcut text broke markup | `engine/service.py` (`_called`), `hotkey.py` (`readable_trigger`) |
| Credits and copied licences (whisper-flow, OpenWhispr, Laya) | `credits.py`, `LICENSE.*`, `REFERENCES.md` |

## What "the model failed" turned out to be (2026-10-28 logs)
- A cold big model: the first request loaded qwen3.5:4b for 8 s. Now covered by the load modes and the "Waking the
  big model" state.
- A bare "Phoenix" in a wake-free window went to the model, which called `set_voice`. Fixed: it opens follow-up.
- "Hello" and similar without the wake word were dropped on purpose, silently. Still the design; a visible hint was
  not added.

## Open
- **Restart Phoenix, then live-check (user):** an unsure request asking "Did you mean ...?", "that's wrong", small
  talk, and reset the manual quantum (`pw-metadata -n settings 0 clock.force-quantum 0`).
- **Browser hidden behind windows:** pass the GlobalShortcuts portal's activation token (portal 1.21.1) for hotkey
  requests, plus a "Opened in Chrome" notification for wake-word ones; extension decision left to the user.
- **Argument fixes:** "change your voice" picks the next voice, "default" = F1; YouTube rule accepts "can you" and
  plays the first video with Web Access on; "play a song" with no player searches YouTube; a timer without a time asks.
- **One-command installer** (`scripts/install.sh`): apt packages (sudo once), venv, launcher, user-only Ollama, models.
- **Cloud review (whole project):** draft PR on `cloud/project-review`: 13 fixes with tests (speech tied to its
  turn, cancel ends an on-screen confirm, echo cancel loaded once, MCP leaks, timers across suspend, denylist
  `/home`, muted listen loop sleeps). Its "needs live check" rows are for the desktop.
- **Live check on the desktop (user), batch 4:** talking over a reply (the log line `while speaking: voice up to`
  says what the mic heard), plain chat, a request right after a confirmation, the Home speed row, the Models page.
- **Live check on the desktop (user):** sleep words ("Phoenix, that's all"), "Stop Phoenix", greetings, "One moment"
  on a cold big model, the new sounds (Voice page, Sounds), a long reply paging in the pill, a confirmation showing
  its command, the top-bar icon (solid / faint / dots; needs the app restarted), timers ("remind me in 2 minutes to
  stretch"), Tools categories, Web Access (weather needs a home city), Models' Running Now and side-by-side table,
  the female voice (pick another from the samples if F1 isn't right).
- Re-measure task routing with the 27-tool menu (bench `router/` uses its own menus); chat was re-measured.
- Voice-not-heard follow-ups not done yet: follow a changed default microphone (OpenWhispr does), retry an empty
  transcription once.
- Close app: GNOME may show "is ready" instead of raising a window for Switch (focus-stealing rules); untested live.
- **Dictation** (type what I say at the cursor): approved for later, as its own task.
- Laya: one full download check through the Models page.
- `SECURITY.md` still describes v2; `pyproject.toml` is still the v2 `glados` package; `FUTURE_DIRECTION.md` predates
  v3.
- Previews: `scripts/headless-run.sh` (command in `CLAUDE.md`); `--demo` doesn't reach the screenshot path, so
  headless Home always shows the "isn't installed" notice.
