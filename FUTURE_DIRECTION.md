# Future Direction

Date: 2026-10-26 (v2; v1 was 2026-10-21) · Repo version 2.5.2 (HEAD `9c00ff2`) · Machine: RTX 3060 Mobile 6 GB, 16 GB RAM, Ubuntu 26.04 GNOME Wayland.

**Verdict:** keep the engine architecture (vendored GLaDOS, typed MCP tools, gate + denylist). The slowness comes from the model and the turn flow: 4.4–8.5 s of LLM time per command today, about 0.6–1.3 s after D1–D4 (§6). **New in v2:**
- The GNOME-extension UI is replaced by a **desktop app** (GTK4/libadwaita). It talks to the engine, which becomes a background service, over a private Unix socket (D9, D11).
- Shipping starts as a **one-command installer**, then a .deb, then a UI-only Flatpak on Flathub (D10).
- **Jev-style routing** moves up the plan as a gate (ignore / reply / act). It is never an authority for actions (D6).

Every claim cites [n] or a local measurement [M], or is marked **[Assumption]** with its reason.

---

## 0. Your calls (2026-10-26) and corrections to v1

| # | Your call | Plan |
|---|---|---|
| D1 template say-lines | Agree | Phase 1 |
| D2 model | Qwen, or Gemma 4 like GLaDOS? | Bake-off in Phase 1 including `gemma4:e4b-it-qat`. Preliminary answer: stay on Qwen (§6) |
| D3 trim built-ins | (you used this slot for the UI) | Still Phase 1, a small change. The UI is the new D9 |
| D4 context | Yes, important | Phase 1, first step |
| D5 sentence templates | Needs testing | Phase 6: measure against the router, adopt only if it wins |
| D6 Jev router | Very important | **Phase 5, moved up from "Later"**. Stage-1 gate first (§7) |
| D7 Smart Turn | Think about it | Phase 6 |
| D8 AT-SPI desktop control | Can be in the UI | Phase 6, as an off-by-default switch in the app |
| D9–D11 (new) | Desktop app + easy install | Phases 2–4 |

**Corrections to v1:**
- **Gemma did not fail on VRAM.** v1 ruled out `gemma4:e4b` as "larger than 6 GB VRAM". That was wrong: 6.6 GB is the download size. llama.cpp always keeps the embedding tables on the CPU, including Gemma's per-layer table (PLE) [25]. Upstream measured **4,118 MiB of GPU memory** for E4B Q4_0 plus its image/audio projector at 4k context [1]. Whether Ollama places it the same way is unverified, and the fit test in §6 settles it.
- **Upstream's "JS" is a browser console, not a desktop app.** It is a web console served by the engine (stdlib HTTP server + Server-Sent Events, plain JS/HTML/CSS, started with `glados webapp`, off by default) [15].
- **The router saves less time than its reputation suggests.** Its value is quality and scale. On upstream's own hardware (RTX 3080), routing took 125–316 ms of a 2.3–3.0 s input-to-playback time [1]. The big speed win remains the non-thinking model (§6).

## 1. Goal

- A local, offline voice assistant for everyday desktop tasks (volume, apps, search, media, settings, to-dos, system info) plus short chat.
- Success = **right tool, done, spoken confirmation, fast**. Target: under 1.5 s of LLM time for a simple command. **[Assumption]** Home Assistant's local path answers device commands "in under a second" [2][3], plus about 0.5 s for ASR/TTS.
- **New:** install with one command, then control everything from a desktop app, with no terminal needed.
- Not a goal: autonomous multi-step agent work.

## 2. Measured 2026-10-21 [M]

| What | Value |
|---|---|
| Model running | `qwen3:4b` = **Qwen3-4B-Thinking-2507** (thinking-only; blob matches `qwen3:4b-thinking-2507-q4_K_M`) [4] |
| Tools every turn | **35**, including 7 unused upstream built-ins (17.8 % of the tool JSON) |
| Prompt | 2,556–2,606 tokens of a **4,096** default context (62 % used before any history) [5] |
| LLM time per simple command | **4.4–8.5 s**: round 1 picks the tool (2.3–5.7 s), round 2 speaks the result (2.1–2.8 s). Almost all of it is thinking tokens |
| Prefill | ~0.3 s (not the bottleneck). Changing `num_ctx` per request forces a model reload (+4.7 s) |

**Diagnosis:** time goes into **thinking tokens, paid twice per command**. "Reasoning ON is required for tool calls" (project CLAUDE.md §6) is a side effect of the thinking-only build.

## 3. Decisions

| # | Change | Gain | Risk | Phase |
|---|---|---|---|---|
| D1 | Typed tools return a short `say` line; skip LLM round 2 when every call in the turn is a typed tool that succeeded | −2.1 to −2.8 s per command [M] | Low | 1 |
| D2 | Non-thinking brain, picked by bake-off (§6) | Round 1 2.3–5.7 s → 0.6–1.3 s [M] | Medium: must hold on the full eval | 1 |
| D3 | Drop the 7 unused upstream built-ins | 17.8 % of the tool JSON; fewer wrong picks | Low | 1 |
| D4 | `OLLAMA_CONTEXT_LENGTH=8192` + `OLLAMA_KV_CACHE_TYPE=q8_0` in the `ollama serve` env | Room for history; same VRAM as today's 4k f16; no reloads [5] | Low | 1 (first) |
| D5 | Full-utterance sentence templates before the LLM (Home Assistant pattern) | ~0.6 s per matched command, after D1+D2 | Medium: false matches on background talk | 6, if the eval says so |
| D6 | **Jev-style gate on the same model**: one token, logprobs, confidence + margin (§7) | Background speech dropped with no LLM/TTS turn; replies go out without tool schemas; later the menu can grow without bloating the prompt | Medium: a confident wrong "ignore" drops a real command | 5 |
| D7 | Smart Turn v3 end-of-turn model (8 MB ONNX, BSD-2) [7] | Fewer cut-offs **[Assumption: untested]** | Low | 6 |
| D8 | AT-SPI desktop control (computer-use-linux upstream) [8], off by default, switch in the app | "Click X in app Y" tasks | Medium: +16 tools; needs D6 stage 2 | 6 |
| **D9** | **Desktop app**: GTK4/libadwaita in Python (PyGObject), its own process (§8) | Full UI, first-run setup, confirmations, no terminal | Medium: new code | 3 |
| **D10** | **Packaging**: pinned one-command installer → .deb → UI-only Flatpak (§9) | Easy install for other people | Medium: supply chain, distro spread | 4 |
| **D11** | **Engine as a systemd --user service + Unix-socket JSON-RPC control API** (§8) | One clean API for the app, Flatpak UI, tests; replaces state.json/control.json polling | Low–medium | 2 |

## 4. How others do it (only what we borrow)

- **GLaDOS upstream (MIT, pushed 2026-10-10) [1]:**
  - Engine: Gemma 4 E4B with thinking off on llama.cpp, q8_0 KV, stable-prefix caching.
  - Routing: hierarchical letter routing (area → server → tool, at most 19 options per node, a "none of these" fallback at each node).
  - Web console: HTTP + SSE on 127.0.0.1:8050 [15].
  - **Borrow:** the non-thinking model, the routing math and gate, the observability event fan-out, and the console's MIT CSS/JS look.
  - **Don't copy:** the console as it stands. It reads about 12 engine members our older vendored engine lacks (it would drag in upstream's ~22-module core refactor), and its control API has **no auth** and accepts any request without an Origin header [15].
- **Home Assistant [2][3]:** a local intent engine runs first, and the LLM is the fallback. This is D5.
- **Linux AI apps:**
  - Alpaca and Newelle are GTK4/libadwaita Python apps on Flathub. Speech Note is Qt.
  - **None of them ships with the Flatpak sandbox escape.** Newelle asks users to grant host access by hand [16][17].
  - Ollama ships with `curl | sh`. Jan, LM Studio and GPT4All ship AppImages [18].
  - **Borrow:** the UI-only Flatpak that talks to a host service, the way Alpaca talks to Ollama.
- **Mac/Windows desktop agents [9][10]:** read the accessibility tree first, screenshots only as a fallback. On Linux that means AT-SPI, which computer-use-linux already wraps [8]. This is D8.
- **Jev family [14]:** closed or weak open look-alikes. We port the *method* (upstream's routing) onto our own model; we don't adopt a separate router model.

## 5. Target architecture

```
 Desktop app (GTK4/libadwaita, own process): orb + transcript · settings · models
   · activity log · skills · health · confirm dialog · router view · desktop-control switch
        │ JSON-RPC 2.0 over $XDG_RUNTIME_DIR/ai-linux/engine.sock (0700 dir, 0600 socket, same uid only)
        ▼
 Engine = systemd --user service (host, full access, sudo-free)
 mic ─► PipeWire AEC ─► Silero VAD (+ Smart Turn, D7) ─► Parakeet ASR (CPU)
                                         │
              D6 gate, 1 token on the brain:  ignore │ reply │ act / unsure
                                   drop turn ◄┘  tools=[] ◄┘   └► full lean menu (D3)
                         non-thinking 4B (D2) · 8k ctx q8 KV (D4) · Ollama
                                         │ tool call
              tool_safety gate + run_shell denylist + systemd caps  (the ONLY authority)
                                         │
              D1 template say-line, or LLM round 2 ─► SuperTonic TTS (CPU) ─► speaker
```

## 6. Model choice (D2): Qwen or Gemma 4?

| Model | Size | VRAM | Tools (6) | Round 1 | Router 40 | Confident wrong | Notes |
|---|---|---|---|---|---|---|---|
| `qwen3:4b` (current) | 2.5 GB | 3.2 GB | 6/6 | 2.3–5.7 s | can't route | n/a | thinking-only |
| `qwen3:4b-instruct` | 2.5 GB | 3.2 GB | 6/6 | **0.57–0.79 s** | 34/40 | **5 at p = 1.00** | BFCL-v3 61.9 [20] |
| `qwen3.5:4b` think off | 3.3 GB | 2.9 GB | 6/6 | 1.0–1.3 s | **36/40** | **0** | vision encoder; Apache-2.0 [20] |
| `gemma4:e4b-it-qat` | 6.1 GB | ? (upstream: ~4.1 GB on llama.cpp [1]) | not run | not run | not run | not run | audio + vision input; Tau2 42.2 [19]; Apache-2.0 |
| `gemma4:e2b-it-qat` | 4.3 GB | ? | not run | | | | fallback only; Tau2 24.5 [19] |

- **Why upstream picked Gemma:** one model that takes audio directly (no Parakeet) and drives its llama.cpp router, on a **10 GB RTX 3080**. **It never compared Gemma against Qwen** [1]. None of those reasons applies to our Parakeet + Ollama + function-calling pipeline today.
- **Published scores don't settle it:** BFCL v3 vs V4, Tau2 averaged vs per domain, and the thinking mode isn't stated [19][20].
- **Preliminary call:** stay on Qwen. Because you want the router, **`qwen3.5:4b` is the favourite**, the only model whose confidence held (0 confident wrong). `qwen3:4b-instruct` is faster but was confidently wrong 5 times at p = 1.00. With D1, qwen3.5 lands at about 1.0–1.3 s per command.
- **Bake-off (Phase 1), same harness for every model, D4 env on:**
  1. **Fit:** warm at 8k context, `ollama ps` must show 100 % GPU with ≥ 0.8 GB VRAM free. Also record system RAM, since Gemma's PLE table lives there.
  2. **Thinking off:** the thinking field is empty and no thought text appears in the content.
  3. **Tools:** exact tool + argument accuracy on the ~150-utterance eval; wrong actions = 0.
  4. **Latency:** 5 warm repeats; median and p90 for rounds 1 and 2; cold load.
  5. **Router:** confirm the option letters are single tokens, then run the 40 + background set and count confident-wrong picks.
  6. **Contention:** repeat round 1 with ASR + TTS running.
- **Decision rule:** adopt Gemma E4B only if it passes the fit test, matches qwen3.5 on tools, has 0 confident-wrong picks and a round-1 median ≤ 1.0 s. If Gemma wins on quality but Ollama spills it to the CPU, try a user-level llama-server (single slot, 8k, no projector) as a last resort.
- Whatever wins: change the `llm_think` default, the Settings panel and CLAUDE.md §6/§11 **in the same change set**.

## 7. Router (D6): how upstream does it, and our port

- **Upstream method [1]:**
  - **Prompt:** "intent classifier, choose ONE option letter"; options as `A: …`; the last 4 turns as context; `CURRENT input` last, so the cached prefix is reused.
  - **Scoring:** one token. Letter probabilities are renormalised, then accepted only if best ≥ **0.8** and margin ≥ **0.2**. Missing or NaN scores mean "no decision authorised".
- **Our port:** about 120 lines, `core/option_router.py`, crediting upstream (MIT).
  - **Request:** the existing Ollama `/api/chat` with `think:false, logprobs:true, top_logprobs:20, num_predict:1`. No different `num_ctx`, or Ollama reloads the model.
  - **Ollama 0.30.8 limits:** it has no `logit_bias`, so if the model's first token isn't a letter, the router declines. That costs a missed saving, never a wrong action [26].
  - **Gate:** the same math, plus a minimum letter-probability floor.
- **Stage 1 (build first, off by default):**
  - **Options:** `ignore` (background or not addressed) / `reply` (chat, send `tools=[]`) / `act` / `unclear`.
  - **Uncertain or failed** → today's full menu, unchanged.
  - `ignore` applies to spoken input only; typed input is never ignored. Barge-in continuation fragments bypass the gate.
- **Stage 2 (only when the menu passes ~40 tools, e.g. D8 on):**
  - Family sub-menus (media, display, apps, system, shell, todoist, voice, desktop) with "none fits" at each node.
  - If the scoped call makes no tool call, retry once with the full menu.
  - Do **not** port upstream's "[Capability routing]" system message, which suppresses tool calls on small qwen (CLAUDE.md §13).
- **Never an authority:**
  - The router may drop, reply, or scope. Only the LLM's tool call, passing `confirm_tool_call` + the denylist, runs anything.
  - Optional zero-argument bindings (lock, media pause/next, DND) still pass the gate.
- **Efficiency, honestly:**
  - `ignore` and `reply` save a whole turn or the tool schemas.
  - On the `act` path it adds about 0.1–0.3 s per stage **[Assumption: estimate, not measured on the 3060]**.
  - It lets skills grow without prompt growth.
- **Ship rule:** enable by default only after **0 false ignores** on our own recordings (TV, side talk, echo residue) with the wake window open.

## 8. Desktop app (D9) + engine API (D11)

**Engine side (Phase 2):**
- Port upstream's `ObservabilityBus.subscribe()` fan-out (MIT, small). Emit events for state, transcript, tool start/finish (route, latency split) and audio level.
- **Control socket** `$XDG_RUNTIME_DIR/ai-linux/engine.sock`:
  - 0700 dir, 0600 socket, peer uid checked with `SO_PEERCRED`.
  - Newline-delimited JSON-RPC 2.0, 64 KB message cap, a version handshake.
  - **Method allowlist:** status, subscribe, submit_text, set_mode, activate, mute/unmute, set_voice, get/set_settings (explicit keys only, never defaults), confirm (prompt_fn answer), quit.
  - **No "run tool" method.** Arming actions stays a start-time setting.
- **Why not upstream's TCP API:** a localhost HTTP/WebSocket API reaches armed shell tools from any local process. The MCP spec itself says local HTTP servers MUST check Origin and SHOULD authenticate [21]. A Unix socket can't be reached from a browser at all.
- **systemd --user unit:** move the launcher env (PATH, `GLADOS_*`), the Ollama env (D4) and the held-open `pw-cli` AEC module into the unit. Closing the window doesn't stop the assistant. Keep `overlay/bridge.py` until the app replaces it.

**App side (Phase 3):**
- **Toolkit:** GTK4/libadwaita, Python (PyGObject, system `python3-gi`), single instance, async `Gio.SocketClient`. It **never imports engine code** (the engine stays in the conda 3.12 env).
- **Pages:**
  - **Home:** orb, live transcript, mic/mode buttons, start/stop.
  - **Activity:** what ran, result, route, latency split.
  - **Settings:** model, voice with previews, wake word, listening mode, barge-in, actions armed.
  - **Models:** download/remove, size warning, fit check.
  - **Skills:** typed tools, on/off, risk tier.
  - **Health:** mic, AEC, Ollama, GPU (today's `doctor`).
  - **Router:** on/off, threshold and margin sliders, and per-option probabilities for the last decision.
  - **Desktop control:** the D8 switch, off by default.
- **Confirm dialog** for risky actions: on-screen Yes/No, plus a spoken yes/no through the reserved `prompt_fn`. It never uses blocking stdin.
- **First-run wizard:** mic test, model download with progress and size (warn on a metered connection), what "arming actions" means.
- **The console look you liked (needs your decision, §14.1):**
  - **(a) Yes to an embedded webview:** a `WebKit.WebView` inside the app window (not a browser tab), loading only bundled files over a custom `ailinux://` scheme. Hardening: strict CSP, no remote content, no TCP port, JS reaches native code only through the allowed bridge methods, text rendered with `textContent`. It adapts upstream's MIT themes, wire log and canvas avatar. Setup adds `gir1.2-webkit-6.0`.
  - **(b) No:** port the Cairo orb (`extension.js`) to `Gtk.DrawingArea`, with a native transcript list and log.
  - **Rejected:** Tauri (GTK3 WebKit 4.1 + a Rust toolchain [23]), Electron (Chromium + Node, and it breaks Ubuntu's AppArmor userns rule as an AppImage [18]), Qt (non-Adwaita look, 782 MiB Flatpak base).
- **GNOME has no tray.** While the engine runs in the background, show it with a notification or the Background portal, or keep the current extension as a slim indicator on the socket (§14.5).

## 9. Packaging (D10)

- **Stage 1, one command (Phase 4):**
  - Grow `./ai-linux setup` into a release-pinned `install.sh` with a published SHA-256. Document both `curl -fsSLO … && sha256sum -c && sh install.sh` (download, inspect, run) and `git clone` + `./ai-linux setup`.
  - Installs to `~/.local/share/ai-linux`, plus the app's .desktop entry and the systemd user unit.
  - **One sudo prompt, at install only:** apt tools, video group, uinput udev rule (as today). The install manifest and uninstall stay.
  - **Ollama:** reuse a running official install on :11434; otherwise install a rootless tarball **pinned with a checksum** (today it fetches GitHub "latest" unverified, `ai-linux:212`).
  - **Models:** downloaded in the app's first run with checksums and resume. Drop the unused Vision weights (651 MB) from the default.
  - **End-user Python env:** a pinned uv env or a bundled venv (Ubuntu 26.04's system Python is 3.14; onnxruntime/numba need 3.12). Keep conda for development (§14.2).
- **Stage 2:** a `.deb` (`/opt/ai-linux` + bundled venv; Depends on brightnessctl, playerctl, ydotool, wl-clipboard, pipewire-bin; postinst installs the udev rule and enables the user service).
- **Stage 3:** a Flathub Flatpak of the **UI only** (GNOME 50 runtime already has GTK 4.22, libadwaita, WebKitGTK 6, Python 3.13, PyGObject), reaching the engine with `--filesystem=xdg-run/ai-linux` and no sandbox escape [17][24].
- **Rejected:**
  - **Full-engine Flatpak:** it needs `org.freedesktop.Flatpak`, which Flathub flags as a sandbox escape [16]. Prefixing `run_shell` with `flatpak-spawn --host` isn't enough either: `pipewire_io`, the `shutil.which` checks, the systemd-run probe and computer-use-linux all spawn outside it.
  - **Snap:** classic confinement is not granted for this case [22].
  - **AppImage:** it can't install host tools, the udev rule or Ollama, so an installer is still needed.

## 10. Safety: keep, fix, add

- **Keep:** the arm gate + autonomy hard floor, the catastrophic-only denylist in the single chokepoint `run_shell`, systemd-run caps, the sudo-free runtime, the untrusted-content prompt line.
- **Fix (verified gaps [M]):** `rm -rf $HOME/Desktop/..` and `echo … | base64 -d | sh` pass the denylist. Normalise `..`; treat decode-then-pipe-to-shell like `curl | sh`. Restrict `open_app_or_link` to `.desktop` apps, URLs and paths.
- **Add:**
  - Risk tiers per tool, with the confirm dialog for state-changing ones (D9).
  - The router is never an authority (D6).
  - No TCP control API (D11).
  - Webview hardening, if (a).
  - A pinned + checksummed installer and downloads (D10).
  - An announcement + half-duplex fallback when the AEC node is missing.

## 11. Plan

Phases run in order, except Phase 5, which can start once Phase 1 picks a non-thinking model. Sizes are **[Assumption]** S = days, M = 1–2 weeks, L = several weeks.

| Phase | Work | Exit check | Size |
|---|---|---|---|
| **0. Baseline** | Rebuild the eval harness into `tests/eval/`. The scratchpad copies were wiped: `bench.py` and `router.py` survive in the 2026-10-21 session transcript, `round2.py` must be rewritten. Grow it to ~150 utterances + a recorded background-speech set. Denylist fixes + `open_app_or_link` restriction, with tests | Harness runs on today's model; denylist tests pass (42 blocked / 19 allowed + 2 new) | S |
| **1. Speed** | D4 → D3 → D1 → D2 bake-off (§6) → swap model + update the `llm_think` docs/settings | ≤ 1.5 s LLM time per simple command; tool accuracy ≥ today on the eval | S–M |
| **2. Engine API** | D11: bus fan-out, control socket, systemd user unit, tests (foreign uid rejected, size cap, unknown method rejected, no default persisted) | `submit_text` over the socket → transcript event arrives; doctor checks the socket | M |
| **3. Desktop app** | D9: shell, pages, confirm dialog, first-run wizard; console (a) or (b) per your call; retire or slim the GNOME extension | Everything the extension and `./ai-linux` flags do today works from the app | L |
| **4. Packaging** | D10 Stage 1 installer; then the .deb; Flatpak UI later | A fresh Ubuntu VM goes from one command to a working assistant; uninstall leaves nothing | M |
| **5. Router** | D6 stage 1 behind a flag + the router page in the app; stage 2 only with D8 | 0 false ignores on recordings; added ms measured; reply-route gain measured | M |
| **6. Later** | D5 templates (measure against the router), D7 Smart Turn, D8 AT-SPI switch, upstream turn continuation (INT-2) | Each one must beat the current eval numbers | per item |

## 12. Testing

- The eval harness is the gate for every model, router or template change: tool + argument accuracy, wrong actions = 0, latency median/p90, confident-wrong router picks.
- Log per turn: route (gate result / template / LLM), tool, success, and the latency split (VAD end, ASR, router, LLM, tool, TTS first audio). This feeds the app's Activity page.
- Unit tests: denylist, control socket, router (missing logprobs → no route; typed input never ignored; an ignored turn never enters history; gate failure → full menu; a binding is still gated).

## 13. Will not do

- No cloud brain in the runtime path.
- No heavy agent framework as the runtime.
- No classifier-only action authority.
- **No TCP control API without auth**; no re-pull of upstream's console as it stands.
- No full-engine Flatpak, no Snap, no Electron.
- No model that doesn't run 100 % on the GPU at 8k (replaces v1's wrong "6.6 GB > VRAM" rule).

## 14. Open decisions (yours)

1. **Webview:** may the GLaDOS-style console render in an embedded WebKitGTK view inside the app window? This lifts the recorded "no browser/webview" rule. Recommendation: **yes** if you want that look, with the §8 hardening. Otherwise go native (b).
2. **End-user Python:** pinned uv env or bundled venv for the installer, while conda stays for development? This touches the "conda, not uv" decision. Recommendation: **uv for end users** (upstream does the same).
3. **Router narrowing:** stage 1 only removes tools on `reply` turns. Stage 2 family sub-menus reverse the recorded "no per-turn narrowing" rule. Recommendation: allow stage 1 now; decide stage 2 when D8 lands.
4. **Gemma test downloads:** `gemma4:e4b-it-qat` 6.1 GB + `gemma4:e2b-it-qat` 4.3 GB (not on the hotspot). Recommendation: **yes**, in the Phase 1 bake-off.
5. **GNOME extension:** retire it once the app ships, or keep it as a slim top-bar indicator on the socket? Recommendation: slim indicator (GNOME has no tray).

## 15. Risks

- A non-thinking model may lose accuracy on the full eval (only 6 prompts so far).
- A confident wrong `ignore` silently drops a command. Thresholds can't catch p = 1.00 errors (instruct made 5 in 40).
- Router calibration is tied to the model: re-tune after any model change in Settings.
- Ollama's gemma4 placement (PLE on CPU or GPU) and its tool-call parser are untested here.
- Router and reply calls share one Ollama slot and evict each other's prefix cache. `OLLAMA_NUM_PARALLEL=2` fixes that but doubles KV memory.
- A webview inside a process path that can trigger shell tools: injected text could call `submit_text`. Mitigated by the bridge allowlist + CSP + `textContent`.
- The background service keeps the mic/sound card after the window closes; it needs a visible indicator.
- A store-updated Flatpak UI can drift from the host engine: version the protocol and refuse mismatches.
- First run downloads about 2 GB of ONNX plus 2.5–6 GB of LLM: needs resume and a size warning.
- Upstream is moving to Rust and changes fast: pin what we take and re-sync by hand.

## 16. Assumptions (with reasons)

1. The 1.5 s target: HA's local path answers in under a second [2][3]; ASR + TTS add about 0.5 s (not measured).
2. Router cost of 0.1–0.3 s per stage on the 3060: scaled down from upstream's 97–228 ms per stage on a 3080 [1]; not measured.
3. Smart Turn helps on our mic: model card only [7].
4. WebKitGTK's RAM is small next to the engine's ONNX models: not measured.
5. Phase sizes are rough estimates for one developer.

## 17. References

[1] GLaDOS upstream, https://github.com/dnhkng/GLaDOS (commit `ea018f5`, 2026-10-10): README routing section, docs/gemma4.md (E4B Q4_0 4,118 MiB at 4k, RTX 3080, no Qwen comparison), docs/native-inference.md, src/glados/core/option_scores.py + routing.py + routing_tree.py, tests/test_hierarchical_routing.py, plans/architecture-simplification-review.md (routing 125–316 ms of 2.3–3.0 s).
[2] Home Assistant, "Building the AI-powered local smart home", 2025-09-11, https://www.home-assistant.io/blog/2025/09/11/ai-in-home-assistant
[3] https://community.home-assistant.io/t/prefer-handling-commands-locally-does-not-seem-to-work/1016858 · https://github.com/home-assistant/core/issues/162768
[4] Ollama registry: https://ollama.com/library/qwen3 · https://ollama.com/library/qwen3.5 · https://ollama.com/library/gemma4/tags (e4b-it-qat 6.1 GB, e2b-it-qat 4.3 GB, e4b q4_K_M 6.6 GB)
[5] Ollama FAQ (context length, KV quantisation, flash attention, keep_alive), https://docs.ollama.com/faq · https://docs.ollama.com/context-length
[7] Smart Turn v3, https://huggingface.co/pipecat-ai/smart-turn-v3
[8] computer-use-linux (AT-SPI, MIT), https://github.com/agent-sh/computer-use-linux
[9] https://github.com/mediar-ai/mcp-server-macos-use · https://github.com/CursorTouch/Windows-MCP
[10] Ghost OS, https://github.com/ghostwright/ghost-os
[14] https://huggingface.co/convaiinnovations/laya · https://huggingface.co/apus-ailab/APUS-OpenJev-v1
[15] Upstream web console: docs/webapp.md, plans/webapp-architecture.md, src/glados/webapp/server.py (127.0.0.1:8050, Host allowlist, no auth, a request without Origin passes)
[16] Flathub requirements and linter (`finish-args-flatpak-spawn-access`; no shell extensions; minimal static permissions), https://docs.flathub.org/docs/for-app-authors/requirements · https://docs.flathub.org/docs/for-app-authors/linter
[17] Newelle README (users grant `--talk-name=org.freedesktop.Flatpak` by hand), https://github.com/qwersyk/Newelle · Alpaca, https://github.com/Jeffser/Alpaca · Speech Note, https://github.com/mkiol/dsnote · Flathub metadata checked with `flatpak remote-info --show-metadata`, 2026-10-26
[18] AppImage static type2 runtime (no libfuse2 needed), https://github.com/AppImage/type2-runtime · Electron AppImages vs Ubuntu's AppArmor userns restriction, https://forum.cursor.com/t/appimage-run-fails-without-no-sandbox-on-linux/168960
[19] Gemma 4 model card (E4B 4.5B effective / 8B with embeddings, Tau2 42.2, E2B 24.5, Apache-2.0), https://ai.google.dev/gemma/docs/core/model_card_4 · GGUF sizes, https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF
[20] https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 (BFCL-v3 61.9) · https://huggingface.co/Qwen/Qwen3.5-4B (BFCL-V4 50.3, TAU2 79.9)
[21] MCP transport spec (local HTTP MUST validate Origin, SHOULD authenticate), https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
[22] Snap classic confinement review policy, https://forum.snapcraft.io/t/process-for-reviewing-classic-confinement-snaps/1460
[23] Tauri 2 Linux prerequisites (WebKitGTK 4.1), https://v2.tauri.app/start/prerequisites/
[24] Flatpak sandbox permissions (`xdg-run/<path>`), https://docs.flatpak.org/en/latest/sandbox-permissions.html
[25] llama.cpp keeps input-layer tensors (token_embd, per_layer_token_embd) on the CPU: src/llama-arch.cpp, src/llama-model.cpp, https://github.com/ggml-org/llama.cpp
[26] Ollama /api/chat logprobs (top_logprobs 0–20), https://docs.ollama.com/api/chat
[M] Local measurement on this laptop, 2026-10-21 (Ollama 0.30.8, real 35-tool menu and system prompt).
