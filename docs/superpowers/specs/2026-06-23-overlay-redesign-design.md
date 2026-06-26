# Overlay redesign + startup-speed — design (2026-06-23)

## Problem
The on-screen overlay (GNOME Shell extension `ai-linux-assistant@local`) is a **permanent panel**
that sits on screen even when nothing is happening, shows a frozen `starting…` whenever the engine
isn't actively driving it, and presents `always / wake / click / mute` as four equal buttons —
which is confusing because the first three are a single mode choice and `mute` is an orthogonal
toggle. Separately, engine startup is slow (CPU ASR warm-up + 6 sequential MCP server spawns +
cold Ollama model load), and the overlay can't show that progress.

## Goals
1. Overlay is **hidden at idle**, summonable from a **GNOME top-bar icon**, and **auto-shows**
   during an actual conversation, then fades back to hidden.
2. Controls match their semantics: **mode = radio (always/wake/click)**, **mute = separate toggle**,
   laid out as a **cluster beside the orb**.
3. A real **`loading`** state so the user sees the engine booting instead of a dead `starting…`.
4. **Faster startup** by overlapping the slow init steps.

## Non-goals
- No change to the safety gate, the LLM/ASR/TTS pipeline, or the MCP tool set.
- No move off Wayland/GNOME; no Rive/browser UI.

## Design

### 1. States
`state.json.state` ∈ `{loading, idle, listening, thinking, speaking, muted, off}`.
New: **`loading`** — engine constructed but not yet ready to listen.

### 2. Visibility (hidden-until-summoned + auto-show)
- **Idle:** floating overlay hidden; only the top-bar icon is present.
- **Manual summon:** click the top-bar icon → overlay shown, **pinned** until clicked again or a
  **30 s** idle auto-hide.
- **Auto-show:** when a turn is active (`loading`, `thinking`, `speaking`, or the transcript changed
  within ~6 s) the overlay shows itself, then auto-hides ~4 s after returning to `idle`.
  Plain `always`-mode listening does **not** force it open — only real activity does.
- Manual pin overrides auto-hide; auto-show never fights a manual pin.

### 3. Top-bar icon (`PanelMenu.Button` in `Main.panel` status area)
- Glyph/tint reflects state at a glance (amber pulse = loading, dim = idle, lit = listening,
  active = thinking/speaking, struck = muted).
- Left-click toggles the floating overlay.
- The icon is the always-available anchor that replaces the permanent panel.

### 4. Orb + cluster controls
```
        ◯  always     ← 3 modes = radio (one filled = active)
   ╭───╮  ◯  wake
   │ ◉ │  ●  click
   ╰───╯  ─────────
         🔇 mute      ← separate on/off toggle
```
- Orb keeps the per-state pulse animation (existing `ease()` logic).
- Mode dots are a radio group → click writes `{ "mode": <m> }` to `control.json` (unchanged protocol);
  active mode rendered filled/highlighted.
- Mute is a distinct toggle → writes `{ "action": "toggle_mute" }`; shows an "active" style when muted.

### 5. Transcript panel
- Frosted panel (blur fixed: `radius` not `sigma`) shows live `You:` / `AI:` lines.
- Part of the auto-show group: visible during/just-after a conversation, hidden at rest.

### 6. Engine/bridge changes
- **Early loading state:** when `GLADOS_OVERLAY` is set, the engine writes a one-shot
  `state.json {state:"loading"}` at the very start of `__init__` (atomic write to the same
  runtime dir), so the overlay shows the loading pulse from the first moment. The full
  `OverlayBridge` still starts in `run()` and takes over with live state once ready.
- `OverlayBridge` already seeds the `control.json` mtime watermark on `start()` (stale-command fix
  from this session) — keep.
- No breaking change to `control.json`.

### 7. Startup-speed (all overlap the slow init)
- **Pre-warm Ollama:** `ai-linux` fires a background `/api/chat` (tiny prompt, `keep_alive`) right
  after Ollama is confirmed serving, so qwen3:4b loads into VRAM while the engine loads CPU models.
- **Background ASR warm-up:** run the `transcribe_file(data/0.wav)` warm-up on a thread started early
  in `__init__`, joined just before `run()` enters the listen loop.
- ~~Parallel MCP spawn~~ — **not needed**: `MCPManager.start()` already creates all session tasks
  concurrently via `asyncio.create_task` ([manager.py:204](../../../src/glados/mcp/manager.py)) and
  returns once the loop is ready (it does not block per-server). No change made.

## Protocol (unchanged except the new state value)
- `state.json`: `{state, mode, you, assistant, ts}` — `state` may now be `loading`.
- `control.json`: `{mode?, action?}` — `action` ∈ `{activate, mute, unmute, toggle_mute}`.
- `voice.json`: `{voice}` — unchanged.

## Testing
- **Bridge logic** (`_derive_state` loading, auto-show derivation if added there, mtime seeding):
  small pytest unit tests, no audio/GNOME needed.
- **Engine changes:** import/compile + a unit test for the early loading-state write.
- **GNOME extension:** `node --check` for syntax; manual verification via re-sync + re-login
  (cannot be unit-tested headlessly). Documented reload steps.
- **Perf:** `ai-linux` prewarm is a non-blocking background call; verified by `ollama ps` showing
  the model resident sooner; manual wall-clock check by the user.

## Rollout
- Edit repo source; re-sync the installed copy
  (`~/.local/share/gnome-shell/extensions/ai-linux-assistant@local/`) via `./ai-linux setup`;
  **log out / log back in** (Wayland loads extension code at login only).

## File map
- `ui/gnome-extension/ai-linux-assistant@local/extension.js` — top-bar button, visibility state
  machine, cluster controls, loading state, auto-show/auto-hide.
- `ui/gnome-extension/ai-linux-assistant@local/stylesheet.css` — cluster + radio/toggle styles,
  top-bar icon, loading pulse, transcript panel.
- `src/glados/overlay/bridge.py` — `loading` awareness; keep mtime seeding.
- `src/glados/core/engine.py` — early one-shot loading write; background ASR warm-up.
- `src/glados/mcp/manager.py` — parallel server spawn (if sequential).
- `ai-linux` — background Ollama pre-warm.
- `tests/` — bridge/engine unit tests.
