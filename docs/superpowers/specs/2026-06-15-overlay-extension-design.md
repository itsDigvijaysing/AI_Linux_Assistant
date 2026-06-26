# AI Linux Assistant — on-screen overlay + listening-mode control (design)

Date: 2026-06-15 · Status: **implemented** (Stage 1 engine bridge, Stage 2 extension, Stage 3 launcher wiring — all built & tested; extension renders at next login)

## Goal
A real, always-on-screen assistant overlay on GNOME/Wayland (top-right), driven live by the
engine, that also **controls the mic** so the USB sound card can be handed back to other apps.
Replaces the standalone `ui/persona.html` browser demo for actual on-display use.

## Why a GNOME Shell extension
Wayland forbids normal apps from drawing always-on-top surfaces. The only sanctioned way to put
persistent UI on the GNOME desktop is a **Shell extension** (runs inside the compositor). Extensions
are **GJS + St/Clutter**, not a browser — so the **Rive "Glint" WebGL animation cannot run here**.
The orb is reimplemented natively (a glowing Clutter circle that pulses and recolors per state). The
glass look uses St CSS + the Shell's blur effect.

## Architecture (file-based, decoupled)
```
engine  ──writes──►  $XDG_RUNTIME_DIR/ai-linux/state.json   ──watched by──►  extension (renders)
extension ──writes─►  $XDG_RUNTIME_DIR/ai-linux/control.json ──watched by──►  engine bridge (applies)
```
- **state.json** (engine → UI): `{ "state": "idle|listening|thinking|speaking|muted", "mode": "always|wake|click", "you": "<last user text>", "assistant": "<last reply>", "ts": <int> }`
- **control.json** (UI → engine): `{ "mode": "always|wake|click", "action": "activate|mute|unmute" }`
- Atomic writes (temp + rename); `Gio.FileMonitor` on the extension side, a small watcher thread on the engine side. Falls back to `~/.cache/ai-linux/` if `XDG_RUNTIME_DIR` is unset.

## Components (all OURS)
1. **Engine bridge** — `src/glados/overlay/bridge.py`
   - Started by the engine when `overlay: true` (config) / `GLADOS_OVERLAY=1`.
   - Subscribes to `observability_bus` + reads `asr_muted_event` / `processing_active_event` / TTS state to derive the current `state`, and captures the last user/assistant text (`kind="user_input"` + reply events).
   - Watches `control.json` and applies commands via existing engine APIs:
     - `mute`/`click`-idle → `audio_io.stop_listening()` → **frees the sound card for other apps**.
     - `unmute`/`activate` → `audio_io.start_listening()` (reacquire).
     - `set_asr_muted()` / `toggle_asr_muted()` for soft mute.
2. **GNOME Shell extension** — `ui/gnome-extension/ai-linux-assistant@local/`
   - `extension.js`, `metadata.json`, `stylesheet.css`.
   - Top-right frosted-glass panel (`addChrome`, St widgets, Shell blur) + animated orb + transcript labels.
   - Header buttons: **Always · Wake · Click** (+ a mute toggle). Clicking writes `control.json`.
   - In **Click** mode the orb is the push-to-talk button.
3. **Wiring** — `ai-linux`
   - `ensure_overlay()`: copy the extension to `~/.local/share/gnome-shell/extensions/…`, `gnome-extensions enable …`. Sets `overlay: true`.
   - Loading a new extension needs a **re-login** on Wayland (same constraint as the icon).

## Listening modes (the header)
- **Always active** — continuous listening; holds the mic. (today's behavior)
- **Wake word** — listens for a keyword, acts only on it; still holds the mic (uses GLaDOS's existing wake-word support — needs a configured phrase, default e.g. "computer").
- **Click to activate** — mic **released by default** (sound card free for other apps); click the orb → acquire mic → handle one turn → release. This is the "give the mic back" mode.
- **Mute** — immediate release/reacquire toggle, independent of mode.

## Build order (each stage independently testable)
1. **Engine bridge + state/control files + config flag.** Test from a terminal: write `control.json`, watch `state.json` update and `sudo lsof /dev/snd/*` / device free on `mute`/`click`. No UI needed.
2. **GNOME extension.** Enable, re-login, verify it renders state + buttons drive `control.json`.
3. **Wire `ai-linux`** to install/enable + flip the config flag; refresh docs.

## Tradeoffs / constraints (explicit)
- Native orb, **not** the Rive Glint art.
- Enabling the extension needs **one re-login** (Wayland).
- Wake-word mode still holds the mic (only Click/Mute fully free the device).
- Extension targets the installed GNOME Shell version (will pin `shell-version` in metadata.json).

## Out of scope (v1)
Multi-monitor placement options, theming UI, click-through regions, packaging to extensions.gnome.org.
