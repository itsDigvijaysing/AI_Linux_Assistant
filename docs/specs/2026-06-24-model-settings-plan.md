# Model selection + thinking toggle — plan (2026-06-24)

## Decision (two options, per user)
- **Smart — `qwen3:4b`** (default). Best small-model tool-calling; supports a **thinking on/off** toggle.
  Default: thinking **OFF** (snappy for voice); turn ON for harder questions.
- **Fast — `lfm2:1.2b`** (Liquid LFM2). Non-reasoning, fastest, **top** small-model tool-calling and
  best-in-class 1B RAG (Veerman tool bench + the 1B-RAG test). No toggle (always non-thinking).

Why these: tool-calling is the assistant's whole point. qwen3 = most reliable tool format + optional
reasoning; lfm2 = the standout sub-2B for tools/RAG/latency. (qwen3.5 deferred — not clean on Ollama
yet; llama3.2 dropped — poor tool restraint. Both documented in chat.)

## Thinking control (mechanism)
- Ollama `think` param ← engine `llm_think` ← `GLADOS_LLM_THINK` env (already wired, engine.py:160-165;
  sent at llm_processor.py:786).
- **Fix needed:** `_extract_thinking_standard` (llm_processor.py:492) only strips when it sees an
  OPENING `<think>`. With `think:false`, qwen3:4b dumps reasoning into `content` ending in a bare
  `</think>` → spoken aloud. Add: a closing tag seen while not in_thinking ⇒ treat everything before
  it as thinking and drop it. One small guard; makes "think off" clean.

## Model control (mechanism)
- Add `GLADOS_LLM_MODEL` env override in engine config load (mirror GLADOS_LLM_THINK). Lets Settings
  pick the model with no YAML edit.

## Settings storage + apply flow
- Extension writes `~/.config/ai-linux/settings.json`: `{"model": "qwen3:4b"|"lfm2:1.2b", "think": bool}`.
- `ai-linux` reads it at Start → exports `GLADOS_LLM_MODEL` + `GLADOS_LLM_THINK`; `ensure_model` pulls
  the chosen model if missing.
- Applies on **Start**. Changing settings while running ⇒ restart via the menu (Stop → Start).

## Settings UI (in the existing top-bar menu — no separate window)
- `Model ▸` submenu: ◉ Smart (qwen3:4b) / ○ Fast (lfm2:1.2b).
- `Thinking` switch (PopupSwitchMenuItem) — shown for Smart only; hidden/disabled for Fast.
- Both write settings.json; menu shows "applies on Start".

## Steps
1. engine.py: `GLADOS_LLM_MODEL` override + thinking-stripper bare-`</think>` fix.
2. ai-linux: read settings.json → env + pull chosen model.
3. extension.js: Model submenu + Thinking switch → settings.json.
4. Pull `lfm2:1.2b`; set config default back to `qwen3:4b` (think off).
5. Verify: py_compile / node --check, on-box latency test of both models, re-sync; user re-login.

## Files
engine.py · llm_processor.py · ai-linux · ui/gnome-extension/ai-linux-assistant@local/extension.js ·
configs/ai_linux_config.yaml
