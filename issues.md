# AI Linux Assistant — Issues Backlog (pre-completion)

> **Status (updated 2026-07-04):** **RESOLVED**, partly superseded by the native-tools pivot (305a97c).
> All issues validated against live code (14 confirmed, 1 confirmed-with-corrections, **LOW-3 refuted**)
> and fixed. **LLM-1/LLM-2 are now COMMITTED:** the "pending live gate" prompt work was subsumed by the
> 305a97c redesign (skills as native function-calling tools, rewritten system prompt, plain-text few-shot;
> reasoning ON, verified 9/9 tool calls). A post-pivot validation pass on 2026-07-04 fixed further items
> and opened two deep ones — see **§7**. LOW-3 is a no-op-today defensive note, not a fix.
> **Created:** 2026-06-30 · **Source:** full-project verification + small-LLM/safety re-validation pass.
> **Scope reminder:** small local brain (`qwen3:4b` default, `qwen3:1.7b` lighter) — every prompt/flow must
> stay within small-model capability. Runtime is sudo-free; the only privileged step is `./ai-linux setup`.

Line numbers are accurate as of 2026-06-30 and may drift as code changes — search by symbol if they don't match.

---

## 0. How to use this file
- Each issue has a stable **ID** (e.g. `SEC-1`). Reference it in commits/PRs/branches.
- **Severity:** HIGH (fix before completion) · MEDIUM (should fix) · LOW (nice-to-have / housekeeping).
- **Status:** all **Fixed** (LLM-1/LLM-2 landed via 305a97c) except **LOW-3 (Refuted)**; new open items live in §7.
- "Docs to update" lists which markdown files must change when the issue is fixed, so docs and reality stay in sync.
- Verification snippets are in [§5](#5-verification--repro-harness). Re-run them after any fix.

---

## 1. Existing documentation map (context)

Top-level and in-tree markdown docs, what each is for, and which issues touch them.

| File | Purpose | Touched by |
|---|---|---|
| `CLAUDE.md` | Onboarding / context for future sessions. Architecture, status, conventions, gotchas, decision log. **Read first.** | DOC-1, SEC-*, LLM-* |
| `PLAN.md` | Master plan & single source of truth: decisions, phases, rationale, history. | DOC-1 |
| `README.md` | User-facing intro: features, install (`./ai-linux`), usage, safety summary, barge-in. | DOC-1, SEC-1/2 |
| `SECURITY.md` | Threat model, guarantees, action gate + autonomy hard-floor + **destructive denylist** description, residual risk. | SEC-1, SEC-2, SEC-3 |
| `TOP10.md` | Landscape research used to pick the base project (GLaDOS) and references. | — |
| `MD/` (`Res_Clau.md`, `Res_Perplex.md`, `Res_Gemi.md`, `CANDIDATES.md`) | Raw landscape-research notes (project selection). | — |
| `skills/README.md` | Explains the SKILL-*.md procedure format + the skills MCP retriever. | LLM-2 (skill/few-shot consistency) |
| `skills/SKILL-*.md` (16) | The executable skill library; the matched skill's command is injected per turn. | LLM-1/2, LOW-4 |
| `docs/superpowers/specs/2026-06-15-overlay-extension-design.md` | Design doc: GNOME overlay extension (orb + transcript). | AUD/UI context |
| `docs/superpowers/specs/2026-06-23-overlay-redesign-design.md` | Design doc: overlay redesign. | UI-1 context |
| `src/glados/glados_ui/images/README.md` | Asset/license note for vendored UI images (upstream GLaDOS). | — |

**Key doc claims that the issues below contradict (must be reconciled):**
- `SECURITY.md` §"How actions are controlled" (lines ~37-40) states the denylist refuses `rm -rf /`/`~`/`$HOME`/`/home`
  "regardless of how the command was produced." → **SEC-1 / SEC-2 show long-form and chained `rm` forms slip through.**
- `README.md` (lines 17, 119) and `PLAN.md` (line 38) call **`llama3.2` (3B)** the "lighter fallback," but the
  implemented lighter model (Settings panel / `GLADOS_LLM_MODEL`) is **`qwen3:1.7b`**. → **DOC-1.**

---

## 2. Issue summary (by priority)

| ID | Title | Severity | Area | Primary file |
|---|---|---|---|---|
| SEC-1 | Denylist misses long-form `rm --recursive --force` | HIGH | Safety | `src/glados/mcp/shell_server.py` |
| SEC-2 | Denylist only checks the first `rm` in a chained command | HIGH | Safety | `src/glados/mcp/shell_server.py` |
| LLM-1 | Dense system prompt; destructive-refusal clause is last (most-dropped) | MEDIUM | Prompt/safety | `configs/ai_linux_config.yaml`, `configs/ai_linux_groq.yaml` |
| LLM-2 | Few-shot depicts tool calls as prose, not real tool calls | MEDIUM | Prompt | both configs |
| BUG-1 | `/tidy` returns empty report on Groq/OpenAI brain | MEDIUM | Correctness | `src/glados/core/engine.py` |
| BUG-2 | `skills_feedback.recent()` drops all history on one corrupt line | MEDIUM | Robustness | `src/glados/core/skills_feedback.py` |
| AUD-1 | Dropped/failed TTS playback reported as "fully heard" | MEDIUM | Audio | `pipewire_io.py`, `sounddevice_io.py` |
| AUD-2 | PipeWire capture reader thread never joined (restart race) | MEDIUM | Audio | `src/glados/audio_io/pipewire_io.py` |
| UI-1 | Voice switch marked applied before apply; never retried on failure | MEDIUM | Overlay | `src/glados/overlay/bridge.py` |
| SEC-3 | Other mass-delete forms not covered (`find -delete`, `dd of= ` w/ space, `truncate`) | LOW | Safety | `src/glados/mcp/shell_server.py` |
| BUG-3 | `shell_outcome()` logs non-JSON results as success | LOW | Robustness | `src/glados/core/skills_feedback.py` |
| AUD-3 | Capture at non-16 kHz without soxr can crash Silero VAD | LOW | Audio | `src/glados/audio_io/sounddevice_io.py` |
| DOC-1 | `llama3.2` "lighter fallback" vs implemented `qwen3:1.7b` | LOW | Docs | `README.md`, `PLAN.md` |
| LOW-1 | `skills_embed` `_mem` cache has no lock | LOW | Concurrency | `src/glados/core/skills_embed.py` |
| LOW-2 | ASR warm-up `join()` has no timeout | LOW | Startup | `src/glados/core/engine.py` |
| LOW-3 | Skill matching stringifies non-string (multimodal) content | LOW | Retrieval | `src/glados/core/llm_processor.py` |
| LOW-4 | `SKILL-open-application.md` filename ≠ its `name:`/content | LOW | Skills hygiene | `skills/SKILL-open-application.md` |

---

## 3. Detailed issues

### Safety / Security

#### SEC-1 — Denylist misses long-form `rm` flags  [HIGH · Fixed]
- **Location:** `src/glados/mcp/shell_server.py`, `_destructive_reason()` lines ~63-67 (flag detection).
- **Symptom:** `rm -rf ~` is blocked, but the equivalent long-form/mixed forms are **allowed**:
  `rm --recursive --force ~`, `rm -r --force ~`, `rm -f --recursive /home`, `rm --force --recursive /`.
- **Root cause:** flags are collected only from short clusters via `re.findall(r"(?:^|\s)-([A-Za-z]+)", rm_args)`,
  so `--recursive` / `--force` never set `r`/`f`, and the top-level-target check is skipped.
- **Why it matters:** In normal interactive use `./ai-linux` **arms actions by default**, so the denylist + prompt
  are the *live* safety net — not a rarely-hit backstop. This directly contradicts the `SECURITY.md` guarantee.
- **Recommended fix:** before flag analysis, normalize long options to short (`--recursive`→`r`, `--force`→`f`,
  also handle `--recursive=...` and GNU `--` end-of-options); then keep the existing top-level-target / glob logic.
- **Acceptance criteria:** all SEC-1 forms blocked; the benign allow-set (§5) still passes; no new false positives.
- **Docs to update:** none (this makes reality match `SECURITY.md`); optionally note coverage in `CLAUDE.md` §8.

#### SEC-2 — Denylist only inspects the first `rm` in a chained command  [HIGH · Fixed]
- **Location:** `src/glados/mcp/shell_server.py`, `_destructive_reason()` lines ~63-65
  (`m = re.search(r"\brm\b(.*)", c)` + `re.split(r"[;&|]", m.group(1))[0]`).
- **Symptom (verified):** a benign first `rm` lets a catastrophic later one through —
  `rm -rf /tmp/x; rm -rf /` → allowed; `rm -rf /tmp/x && rm -rf ~` → allowed.
- **Root cause:** only the first `\brm` match is taken, and only its args up to the first separator are analyzed;
  subsequent `rm` segments are never evaluated.
- **Why it matters:** same as SEC-1 — armed-by-default means this is the live guard.
- **Recommended fix:** split the whole command on `;`, `&&`, `||`, `|`, newline; run the destructive check on
  **each** segment; block if **any** segment matches.
- **Acceptance criteria:** both SEC-2 examples blocked; `cd ~/p && rm -rf *` and `rm -rf ~/Downloads/old` still allowed.
- **Docs to update:** none required; align `CLAUDE.md` §8 wording if behavior notes change.

#### SEC-3 — Other mass-delete forms not covered  [LOW · Fixed]
- **Location:** `src/glados/mcp/shell_server.py`, `_DENY` list + `_destructive_reason()`.
- **Symptom:** `find ~ -delete` / `find / -delete`, `truncate -s0` of a device, and `dd if=… of= /dev/sda`
  (space after `of=`) are not blocked. (The `dd of= ` form is not a functional command, so it's informational.)
- **Why it matters:** `find <home> -delete` is a real home-wipe via a different tool; currently out of denylist scope.
- **Recommended fix (optional, keep conservative):** add a narrow `find … -delete` rule targeting `/`, `~`, `$HOME`,
  `/home`; optionally tighten the `dd of=` regex to tolerate whitespace. Do **not** broaden into normal deletes.
- **Acceptance criteria:** added forms blocked for top-level targets only; benign `find . -name '*.tmp' -delete` allowed.
- **Docs to update:** `SECURITY.md` denylist example list (mention `find -delete`).

---

### Small-LLM suitability (prompts)

#### LLM-1 — System prompt is dense; destructive-refusal clause is last  [MEDIUM · Fixed (uncommitted — pending live gate)]
- **Location:** `configs/ai_linux_config.yaml` `personality_preprompt` (system, line ~31);
  `configs/ai_linux_groq.yaml` (system, line ~32).
- **Symptom:** ~11 distinct directives packed into one paragraph; the "don't run destructive commands" instruction
  is the **final** clause. Small models (esp. `qwen3:1.7b`) reliably follow early instructions and drop later ones,
  so the model-side destructive guard is the most likely to be ignored.
- **Why it matters:** combines with SEC-1/SEC-2 — if the prompt guard is dropped *and* the denylist has gaps, there
  is no remaining guard for long-form/chained destructive commands while armed.
- **Recommended fix:** move the safety clauses (untrusted-data + destructive-refusal) **earlier**; consider splitting
  the system prompt into short, ordered bullet directives; keep total length tight. Verify behavior unchanged on 4b.
- **Acceptance criteria:** identical task-execution behavior on `qwen3:4b` (skills still fire 1-shot); destructive
  refusal observed on a manual injection probe with `qwen3:1.7b`.
- **Docs to update:** `CLAUDE.md` §6 (note prompt ordering rationale) if the structure changes.

#### LLM-2 — Few-shot depicts tool calls as prose, not real tool calls  [MEDIUM · Fixed (uncommitted — pending live gate)]
- **Location:** both configs `personality_preprompt`, the assistant example:
  `"(calls mcp.shell.run_command with command 'wpctl set-volume @DEFAULT_AUDIO_SINK@ 10%-') Done, I lowered the volume."`
- **Symptom:** the example teaches the model that an assistant turn can *describe* a tool call in prose. It works
  today because the injected command suffix + system instruction dominate (project's 4/4 test on 4b), but it's a
  known anti-pattern that raises the chance of **narration instead of a real call** on the lighter `qwen3:1.7b`.
- **Recommended fix:** replace the prose example with a real structured tool-call example (or remove the few-shot and
  rely on the command-injection suffix). Whatever the choice, keep skill commands and the few-shot consistent (the
  current `wpctl`/`brightnessctl` commands already match the skills — preserve that).
- **Acceptance criteria:** on both `qwen3:4b` and `qwen3:1.7b`, "turn the volume down" / "set brightness to 30%" /
  "lock the screen" each produce a single real `mcp.shell.run_command` call (no narration).
- **Docs to update:** `CLAUDE.md` decision log (skills-execute-via-injection note) if the few-shot format changes.

---

### Correctness bugs

#### BUG-1 — `/tidy` returns an empty report on the Groq/OpenAI brain  [MEDIUM · Fixed]
- **Location:** `src/glados/core/engine.py`, `_oneshot_llm()` lines ~1455-1470.
- **Symptom:** parses only the Ollama response shape (`resp["message"]` / `resp["response"]`); the OpenAI/Groq shape
  is `resp["choices"][0]["message"]["content"]`, so `./ai-linux --groq` + `/tidy` always writes "(no report generated)".
  It also unconditionally sends `"think": False` (an Ollama-only field) to all endpoints.
- **Root cause:** `_oneshot_llm` does not reuse the endpoint detection that the streaming path uses
  (`llm_processor._is_ollama_endpoint`, path ends with `/api/chat`).
- **Recommended fix:** detect endpoint type (or try both shapes); parse `choices[0].message.content` for OpenAI/Groq;
  only include `think` for native Ollama.
- **Acceptance criteria:** `/tidy` writes a non-empty report on both local Ollama and `--groq`.
- **Docs to update:** none.

#### BUG-2 — `skills_feedback.recent()` drops all history on one corrupt line  [MEDIUM · Fixed]
- **Location:** `src/glados/core/skills_feedback.py`, `recent()` lines ~57-58
  (`return [json.loads(line) for line in lines if line.strip()]`).
- **Symptom:** a single malformed JSONL line raises inside the comprehension; the `except` returns `[]`, so `/tidy`
  and success-ranking see **zero** history instead of skipping the bad line.
- **Recommended fix:** parse line-by-line, skipping lines that fail `json.loads`.
- **Acceptance criteria:** a file with one bad line still returns all the good entries.
- **Docs to update:** none.

#### BUG-3 — `shell_outcome()` logs non-JSON results as success  [LOW · Fixed]
- **Location:** `src/glados/core/skills_feedback.py`, `shell_outcome()` lines ~24-31 (`except → (True, None)`).
- **Symptom:** any non-JSON result is recorded as `ok=True`. Mostly theoretical today (`run_command` always returns
  JSON), but if the MCP layer ever wraps the result, failures would be mislabeled as successes and skew ranking.
- **Recommended fix:** treat unparseable results as unknown (don't assert success); or record `ok=None`/skip.
- **Acceptance criteria:** a non-JSON result is not counted as a success.
- **Docs to update:** none.

---

### Audio / playback / overlay

#### AUD-1 — Dropped/failed TTS playback reported as "fully heard"  [MEDIUM · Fixed]
- **Location:** `src/glados/audio_io/pipewire_io.py` `measure_percentage_spoken()` lines ~228-229 & ~248
  (`pw-play` missing → `FileNotFoundError` logged, then `return False, 100`); temp-write failure lines ~207-210
  (`return False, 0`). Same `(interrupted=False, …)`-on-drop pattern exists in
  `src/glados/audio_io/sounddevice_io.py` playback (~lines 442-466).
- **Symptom:** the caller (`speech_player`) treats `interrupted=False` as a completed utterance and appends the full
  reply text to history even though no audio played.
- **Mitigation today:** `./ai-linux` checks `pw-record`/`pw-play` exist before enabling the PipeWire backend, so the
  `FileNotFoundError` path mainly bites manual `GLADOS_AUDIO_BACKEND=pipewire` use.
- **Recommended fix:** define a clear contract for "audio dropped / never played" (e.g. a sentinel distinct from
  normal completion) and have `speech_player` not record the reply as spoken in that case.
- **Acceptance criteria:** when `pw-play` is absent or temp-write fails, the reply is not logged as fully spoken.
- **Docs to update:** none.

#### AUD-2 — PipeWire capture reader thread never joined (restart race)  [MEDIUM · Fixed]
- **Location:** `src/glados/audio_io/pipewire_io.py` `stop_listening()` (terminates `pw-record`, no `self._reader.join()`).
- **Symptom:** a quick `stop_listening()` → `start_listening()` can spawn a second reader while the first still pushes
  to `_sample_queue` (duplicate/garbled frames). Also, an uncaught VAD exception in `_read_loop` can silently kill the
  daemon reader while `input_stream` still looks active.
- **Recommended fix:** join the reader (with timeout) in `stop_listening()`; guard the VAD call in `_read_loop`.
- **Acceptance criteria:** rapid stop/start never runs two readers; a VAD error doesn't silently end capture.
- **Docs to update:** none.

#### AUD-3 — Capture at non-16 kHz without soxr can crash Silero VAD  [LOW · Fixed]
- **Location:** `src/glados/audio_io/sounddevice_io.py` capture path (~lines 236-237, 271-272).
- **Symptom:** if `_capture_rate != 16 kHz` and soxr is unavailable (import failure), the non-resample path can hand
  VAD a non-512-sample / wrong-rate frame → `ValueError`. soxr is a declared dependency, so this is unlikely.
- **Recommended fix:** if resampling is required but soxr is missing, fail clearly (disable voice with a message)
  rather than feeding VAD a bad frame.
- **Acceptance criteria:** missing soxr produces a clear log + graceful degrade, not a callback crash.
- **Docs to update:** `CLAUDE.md` gotchas (audio) if behavior changes.

#### UI-1 — Voice switch marked applied before apply; never retried on failure  [MEDIUM · Fixed]
- **Location:** `src/glados/overlay/bridge.py` lines ~245-251 (sets `_last_voice_applied` before calling
  `engine.set_voice`); `src/glados/core/engine.py` `set_voice()` lines ~1105-1131 returns `False` **without raising**.
- **Symptom:** because `set_voice` never raises, the bridge's `try/except` can't detect failure; `_last_voice_applied`
  is already set, so a failed switch (engine still loading, TTS without runtime switching, bad voice id) is permanently
  skipped — the user's "use a female voice" silently does nothing.
- **Recommended fix:** only mark `_last_voice_applied` **after** `set_voice` returns `True`; on `False`, leave it
  unset so the next poll retries (optionally with a small backoff to avoid hot-looping on a permanently-bad id).
- **Acceptance criteria:** a transient failure (engine not ready) is retried and eventually applies; a permanently
  invalid voice id doesn't hot-loop.
- **Docs to update:** none.

---

### Documentation

#### DOC-1 — `llama3.2` "lighter fallback" vs implemented `qwen3:1.7b`  [LOW · Fixed]
- **Location:** `README.md` lines 17 & 119; `PLAN.md` line 38. (Decision/runtime reality: `CLAUDE.md` §2/§6 +
  Settings panel write `GLADOS_LLM_MODEL=qwen3:1.7b`.)
- **Symptom:** docs name `llama3.2` (3B) as the lighter fallback, but the implemented lighter model is `qwen3:1.7b`;
  nothing in setup pulls `llama3.2`.
- **Recommended fix:** pick one and make docs consistent (recommend standardizing on `qwen3:1.7b` to match the
  Settings panel and `ensure_model`); update `README.md` + `PLAN.md`.
- **Acceptance criteria:** all docs agree on the lighter model; `./ai-linux doctor`/setup references match.
- **Docs to update:** `README.md`, `PLAN.md` (and `CLAUDE.md` if wording needs alignment).

---

### Low / housekeeping

#### LOW-1 — `skills_embed` `_mem` cache has no lock  [LOW · Fixed]
- **Location:** `src/glados/core/skills_embed.py` (`_mem` ~line 20; read/update ~lines 58-59).
- **Symptom:** concurrent hybrid retrieval (overlapping MCP + engine threads on a cache miss) can race on `_mem`,
  duplicating Ollama embed calls / leaving a partial cache. Unlikely to crash.
- **Recommended fix:** guard reads/writes with a `threading.Lock` (mirror `skills_feedback._LOCK`).
- **Docs to update:** none.

#### LOW-2 — ASR warm-up `join()` has no timeout  [LOW · Fixed]
- **Location:** `src/glados/core/engine.py` (~lines 988-990).
- **Symptom:** if warm-up hangs, `run()` blocks indefinitely before the listen loop starts.
- **Recommended fix:** `join(timeout=…)` and proceed with a warning if it didn't finish.
- **Docs to update:** none.

#### LOW-3 — Skill matching stringifies non-string (multimodal) content  [LOW · Refuted — no live bug]
- **VALIDATION (2026-06-30):** **Refuted.** No multimodal/list user-content path exists in this build — ASR
  (`speech_listener`) and text (`text_listener`) both enqueue plain-string `content`, and vision is injected as
  a separate **system** message, never a user turn. So `str()` here can never mangle a list today. Left as-is;
  the genuinely higher-risk sibling (if multimodal is ever added) is `_filter_tools_for_message`'s
  `content.casefold()` at `llm_processor.py:~323`. No code change made.
- **Location:** `src/glados/core/llm_processor.py` (~line 748, `content = str(llm_message.get("content", ""))`).
- **Symptom:** list/multimodal content becomes a Python repr; keyword/semantic retrieval and command-suffix injection
  can mis-match. v1 is text/audio so impact is minimal.
- **Recommended fix:** extract text parts before matching; skip injection if content isn't text.
- **Docs to update:** none.

#### LOW-4 — `SKILL-open-application.md` filename ≠ its `name:`/content  [LOW · Fixed]
- **Location:** `skills/SKILL-open-application.md` (front-matter `name: focus-or-control-window`,
  `tools: [mcp.computer_use.*]`, content about controlling already-open windows). Launching apps is actually
  `skills/SKILL-open-link-or-app.md`.
- **Symptom:** misleading filename for maintainers (retrieval uses `name`/`trigger`, not filename, so runtime is fine).
- **Recommended fix:** rename the file to match its `name:` (e.g. `SKILL-focus-or-control-window.md`), or repoint
  its content to actually be "open application." Keep filename↔name consistent across `skills/`.
- **Docs to update:** none (skills are self-describing).

---

## 4. Cross-cutting notes for "final completion"
- **Armed-by-default posture:** `./ai-linux` arms `mcp.shell.*` / `mcp.computer_use.*` for interactive `start`/`tui`
  (`ai-linux` arg handling). This is intended, but it means **SEC-1/SEC-2 + LLM-1 are the active safety net**, not a
  dormant backstop. Treat SEC-1/SEC-2 as completion gates.
- **What's already solid (no action needed):** Python compiles clean; both configs load; launcher passes `bash -n`;
  `bin/ai-linux-screenshot` is correct (the `computer-use-linux` MCP `screenshot` tool accepts `title`, and
  `data_url_payload` returns raw base64); skill commands are consistent with the few-shot; the tool-narrowing +
  command-injection design is appropriate for small models; the action gate hook calls `confirm_tool_call`
  unconditionally (no regression); autonomy hard-floor intact.

---

## 5. Verification / repro harness

Run from the repo root in the `AI_Linux` conda env.

**Denylist (SEC-1/SEC-2/SEC-3) — must block all, must keep benign allowed:**
```bash
conda run --no-capture-output -n AI_Linux python - <<'PY'
from glados.mcp.shell_server import _destructive_reason as d
must_block = [
    "rm -rf ~", "rm -rf /", "rm -rf $HOME", "rm -rf /home",
    "rm --recursive --force ~", "rm -r --force ~", "rm -f --recursive /home",
    "rm --force --recursive /", "rm -rf /tmp/x; rm -rf /", "rm -rf /tmp/x && rm -rf ~",
]
must_allow = ["rm -rf ~/Downloads/old", "cd ~/p && rm -rf *", "rm file", "git clean -fdx", "npm cache clean"]
print("BLOCK gaps:", [c for c in must_block if not d(c)])
print("ALLOW false-positives:", [c for c in must_allow if d(c)])
PY
```
Target after fix: `BLOCK gaps: []` and `ALLOW false-positives: []`.

**Compile + config load (regression guard):**
```bash
conda run -n AI_Linux python -m compileall -q src/glados bin
conda run --no-capture-output -n AI_Linux python -c "import glados.core.engine as e; \
  [e.GladosConfig.from_yaml(p) for p in ('configs/ai_linux_config.yaml','configs/ai_linux_groq.yaml')]; print('configs OK')"
bash -n ai-linux && echo "launcher OK"
```

**Manual checks (need a running brain / session):**
- BUG-1: `./ai-linux --groq` then `/tidy` → report file is non-empty.
- LLM-1/LLM-2: with `GLADOS_LLM_MODEL=qwen3:1.7b`, say/type "turn the volume down", "set brightness to 30%",
  "lock the screen" → each yields exactly one real `mcp.shell.run_command` (no narration); then probe a benign-looking
  injected destructive instruction and confirm refusal.
- UI-1: request "use a female voice" before the engine is fully ready → confirm it applies after readiness.

---

## 6. Definition of done (suggested gate for completion)
1. **HIGH:** SEC-1 + SEC-2 fixed; denylist harness shows zero gaps and zero false positives; `SECURITY.md` claim holds.
2. **MEDIUM:** LLM-1 + LLM-2 addressed and verified on `qwen3:1.7b`; BUG-1, BUG-2, AUD-1, AUD-2, UI-1 fixed.
3. **LOW:** SEC-3, BUG-3, AUD-3, DOC-1, LOW-1..4 as time permits.
4. Re-run §5 regression guard; update the "Docs to update" targets for every fixed issue.

> Reminder: **do not start any of the above until the owner approves.**

---

## 7. Post-pivot validation pass — 2026-07-04

A full code-vs-docs audit after the native-tools pivot (`305a97c`…`745f20e`). Everything below was
verified against the live code before fixing.

### Fixed in this pass (uncommitted)
| ID | What | Where |
|---|---|---|
| SEC-4 | Denylist glob gaps: `rm -rf $HOME/*`, `${HOME}/*`, and dotfile wipes `<root>/.*` were not refused; glob rule now derives from a shared root-prefix alternation | `mcp/shell_exec.py` |
| UI-2 | Settings panel persisted the merged `think:false` default on ANY save, so the launcher (honors only explicit values, 7c20abd) forced reasoning off — re-breaking tool-calling. Defaults now applied at display time only, never written back | `ui/gnome-extension/…/extension.js` |
| ACT-1 | Direct-binary app launches (and `open_settings`) ran foreground GUI apps under `run_shell`'s 20s timeout → turn blocked ~20s, then the just-opened app was SIGKILLed. Now launched detached (`setsid -f … >/dev/null 2>&1`) | `mcp/skills_actions_server.py` |
| FBK-1 | `_ACTION_PREFIXES` lacked `mcp.skills_actions.` → the primary action family was never recorded to skills_feedback (blinding `/tidy`); its run_shell JSON results are now parsed by `shell_outcome` too | `core/tool_executor.py` |
| AUD-4 | pw-play poll loop had no absolute deadline: a hung `pw-play` stalled the SpeechPlayer thread forever. Killed at clip-duration+5s and counted as heard (clip time fully elapsed) | `audio_io/pipewire_io.py` |
| AUD-5 | One VAD/frame exception permanently stopped the capture reader while `pw-record` kept running ("listening" but deaf). Now drops the frame; stops only after 10 consecutive failures | `audio_io/pipewire_io.py` |
| AUD-6 | Sample queue never drained across `stop_listening`/`start_listening` → stale pre-stop frames leaked into the next session | `audio_io/pipewire_io.py` |
| UI-3 | `control.json` change detection was float-mtime-based (two commands within one timestamp tick could drop the second). Now ns-mtime + content watermark (startup stale-file seeding preserved) | `overlay/bridge.py` |
| UI-4 | Transcript dedup by text equality hid a REPEATED identical utterance/reply. state.json now carries `you_ts`/`reply_ts` (reply_ts bumps only on `reply` events, not TTS liveness) and the extension dedups on (text, ts) | `overlay/bridge.py` + `extension.js` |
| LNCH-1 | `apply_settings` interpolated the settings path inside `python -c '…'` string literals (breaks on a quote in the path); now passed via `sys.argv` | `ai-linux` |
| LNCH-2 | `ensure_weights` checked only `models/TTS/*.onnx`, so missing ASR/VAD weights skipped the download; now requires TTS AND ASR | `ai-linux` |
| LNCH-3 | `--groq` had no API-key preflight (engine failed later, opaquely); start/tui now exit early without `GROQ_API_KEY`/`GLADOS_API_KEY` | `ai-linux` |
| LNCH-4 | Desktop-launch EXIT trap said "Failed to start" for ANY nonzero exit (even a crash hours in, or Ctrl-C). Now skips 130/143 and labels by runtime (<60s = startup failure) | `ai-linux` |
| SCF-1 | Dead post-pivot scaffolding removed/relabeled: `render_command_suffix`/`has_shell_commands` + stale docstring (skills_index), dead `skills_hint_*` params (llm_processor), `skills_retrieval` config comment, nomic-embed pull now skipped while the optional skills server is disabled, doctor label fixed | several |
| DOC-2 | Stale docs synced: SECURITY.md (3 gated families incl. `mcp.skills_actions.*`; denylist lives in `shell_exec.py`), skills/README.md (library is reference material post-pivot), `SKILL-lock-or-suspend.md` → `SKILL-lock-screen.md` (lock-only policy, 745f20e) | docs |

### Newly tracked — OPEN (deep; fix deliberately deferred, do not fix blind)
- **INT-1 [MEDIUM] Interrupted tool call leaves dangling `tool_calls` history.** `tool_executor.run()`
  discards a queued tool call when `processing_active_event` is cleared (barge-in) without enqueueing any
  tool-result, so the ConversationStore keeps an assistant `tool_calls` message with no matching `tool`
  reply. Ollama tolerates it; strict OpenAI-compatible endpoints may not, and it can confuse the next turn.
  Fix needs care: writing a synthetic tool-result must NOT re-trigger generation post-interrupt.
- **INT-2 [MEDIUM] Barge-in between sentences merges turns.** A stop that lands between sentences (or inside
  `start_speaking`'s event swap — the race deferred in the 2026-06-25 review) lets already-synthesized
  sentences keep playing, and the suppressed EOS can fold the old turn's spoken text into the next turn's
  assistant history. Needs a synthesis/playback-generation ID; touches listener + player + processor.
- **UI-5 [LOW, known-deferred] `set_voice` returns ok for any underscore-style (Kokoro) name** without
  validating against the live engine (deferred on purpose in the 2026-06-25 review; bridge retry caps the blast).
- **SLP-1 [LOW, design note] `go_to_sleep` in always-listening mode releases the mic with no voice recovery**
  (recovery = overlay click/unmute). Documented behavior for now; revisit if it bites in practice.
