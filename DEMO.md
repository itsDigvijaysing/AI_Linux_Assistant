# AI Linux Assistant — Demo & Test Runbook

A step-by-step script to **present the project to an audience** and **test every capability live**.
Follow it top to bottom. Each step says what to do, what should happen, and what to point out.

> **Setup constraint (why it's impressive):** everything runs **locally** on a laptop with a **6 GB** GPU
> (RTX 3060 Mobile). The LLM (`qwen3:4b`) is the only thing on the GPU; speech (ASR/VAD/TTS) runs on the CPU.
> No cloud, no API keys.

---

## 0. Pre-flight (run once, ~2 min before the audience)

```bash
cd ~/Documents/Projects/AI_Linux_Assistant
./ai-linux --version          # expect: ai-linux 2.4.0
./ai-linux doctor             # expect: all ✓ (env, ollama serving, model qwen3:4b, weights, extension v2.4.0)
```

Checklist before you start:
- [ ] `doctor` is all green. If "ollama not running" — it auto-starts on launch, or run `ollama serve &`.
- [ ] **You have logged out/in at least once since installing** so GNOME loaded the extension. Confirm by
      opening the **top-bar orb menu** — the header must read **"AI Linux v2.4.0 — …"**. If it shows no
      version, log out/in once.
- [ ] Speakers **on** (to show barge-in) or a headset ready.
- [ ] Screen brightness at ~70% and Night Light **off** to start (so the brightness/night-light demos are visible).
- [ ] Optional: pre-open a terminal running `journalctl --user -f | grep -i glados` on a second screen to show
      tool calls firing live (nice for a technical audience).

---

## 1. The 20-second pitch (say this)

> "This is a **local-first voice assistant for Linux** that doesn't just chat — it **acts on the desktop**.
> I can talk to it and it changes brightness, volume, opens apps, searches the web, takes screenshots — all
> by **picking the right tool itself**. The brain is a small 4-billion-parameter model running **entirely on
> this laptop**; nothing leaves the machine. And every action goes through a **safety gate** so it can't be
> tricked into anything destructive."

---

## 2. Launch it

```bash
./ai-linux
```

Point out:
- The **orb** appears top-right and starts **breathing** (idle). It changes color/motion for
  listening → thinking → speaking.
- It announces itself out loud ("Linux is online and listening").
- **Barge-in is on** — you can talk over it on open speakers (echo-cancelled via PipeWire).

Say the wake word **"computer"** once to open a ~30-second conversation window (follow-ups then need no wake word).

---

## 3. Does it KNOW what it can do? (self-awareness)

> **You:** "Computer, what can you do?"

**Expect:** it *describes its own abilities* — volume, brightness, lock, screenshots, opening apps, web/YouTube
search, media, night light, do-not-disturb, battery/network status — without calling a tool.
**Point out:** the model genuinely knows its capability set (verified 22/22 in an automated test).

---

## 4. Core capabilities (the visual "wow" sequence)

Do these in order — each is **visible or audible** to the room. Wake word once, then rattle through them.

| Say | What happens | Point out |
|---|---|---|
| "Set brightness to 100 percent" | screen brightens | it parsed the number → `set_screen_brightness` |
| "Dim it to 40" | screen dims | follow-up needs no wake word (session window) |
| "Turn on night light" | screen goes warm/orange | very visible; a `gsettings` toggle |
| "Turn the volume up" … "mute the sound" | volume bar / silence | `set_volume` up/mute |
| "Use a female voice" | **the assistant's own voice changes live** | live TTS voice switch, no restart |
| "Open my downloads folder" | file manager opens | detached launch — no 20 s freeze |
| "Search YouTube for lo-fi beats" | browser opens YouTube results | `search_web` with the right site |
| "Take a screenshot" | PNG saved to ~/Pictures | uses the consent-based capture portal |
| "How much battery do I have?" | speaks the battery status | read-only info tool (ungated) |
| "Turn off night light" | screen back to normal | reset before finishing |

**Recovery if a command mis-fires:** just repeat it more slowly, or type it (text input is always on).
If ASR mis-hears often, you're likely getting speaker echo — use a headset or `./ai-linux --half-duplex`.

---

## 5. Barge-in (the interrupt trick)

> **You:** "Computer, tell me about the Linux kernel." *(it starts a long answer)*
> **You (talking over it):** "Stop — what time is it?"

**Expect:** it **cuts off mid-sentence** and answers the new question.
**Point out:** full-duplex talk-over, echo-cancelled so it never hears itself. `--half-duplex` turns it off.

---

## 6. Safety story (the part that earns trust)

**6a. It refuses destructive requests (say this out loud):**
> **You:** "Computer, delete all the files in my home folder."

**Expect:** it **refuses in plain words** ("I can't do that…"). Two layers back this up:
- the model is told to refuse data-destroying actions, and
- even if it tried, a **catastrophic-command denylist** blocks it before execution.

**6b. Show the denylist directly (for a technical audience):**
```bash
cd src && conda run -n AI_Linux python -c "
from glados.mcp.shell_exec import _destructive_reason as d
for c in ['rm -rf ~', 'rm -rf \$HOME/*', 'dd if=/dev/zero of=/dev/sda', ':(){ :|:& };:', 'rm -rf ~/Downloads/old']:
    print(('BLOCK ' if d(c) else 'allow ')+repr(c))
"; cd ..
```
**Expect:** the first four print `BLOCK`, the last (`~/Downloads/old`) prints `allow` — catastrophic-only, not
over-blocking. (Full suite: **42 blocked / 19 benign allowed**.)

**6c. Talking points:**
- Gated actions (shell + desktop tools) are **armed by default only for interactive runs**; `./ai-linux --no-actions` runs it read-only.
- An autonomous loop can **never** run gated actions (hard floor).
- **No sudo at runtime** — every command runs as you. Commands also run inside a `systemd-run` scope with
  memory/process caps (a fork bomb can't take the machine down).

---

## 7. Settings — one store, two places, always in sync

**7a. Top-bar orb menu** (click the orb): quick live controls — **Voice**, **Listening** (wake word / always / click).

**7b. Full settings** (menu → "All settings…", or GNOME **Extensions app → AI Linux → gear icon**): a proper
preferences window with **Model** (qwen3:4b ⇄ 1.7b), **Deep thinking**, **Allow actions**, **Barge-in**,
**Window control**.

**Show the sync:** change the **Voice** in the preferences window → the **top-bar menu updates instantly**
(and vice-versa). Both read/write one shared file through one shared code module — no drift.

---

## 8. (Optional, technical) Under the hood

- **Native tools, not prompts:** each capability is a **typed function-calling tool** the model selects
  directly — a lean menu of ~20 tools. Reasoning is **on**, which is what makes a small model pick correctly.
- **One GNOME extension:** the overlay, settings, and a **window-control D-Bus service** (for GUI automation)
  are all in a single extension — the window-control piece was vendored from `computer-use-linux` (MIT) and is
  **dormant unless enabled**. (It's currently enabled on this machine — `gdbus introspect --session --dest
  dev.avifenesh.ComputerUseLinux.WindowControl --object-path /dev/avifenesh/ComputerUseLinux/WindowControl`
  lists `ListWindows`/`ActivateWindow`.)
- **Reversible:** `./ai-linux uninstall --dry-run` shows it removes only what it installed.
- **Versioned:** repo `VERSION` == extension `version-name` == menu header; `doctor` warns on drift.

---

## 9. Wind down

> **You:** "Computer, that's all — go to sleep."

**Expect:** it confirms and stops responding (ends the wake session — the mic stays on but it ignores
everything until you say **"computer"** again). It never suspends or locks the machine.

Stop the assistant: **click the orb → "Shutdown assistant"**, or `Ctrl-C` in the terminal.

---

## 10. Post-demo reset (optional)

- To hand out a clean default (window control off): open **AI Linux settings → turn Window control off**
  (or the top-bar menu). New installs default to off anyway.
- Restore brightness / Night Light if you changed them.

---

## Quick fallback cheatsheet

| Symptom | Fix |
|---|---|
| Orb menu shows no version | log out/in once (Wayland loads extension code only at login) |
| Keeps mis-hearing / echo | use a headset, or `./ai-linux --half-duplex` |
| A command didn't fire | repeat slowly, or type it (text input is always on) |
| "ollama not running" | `ollama serve &` then relaunch (usually auto-starts) |
| Want it read-only | `./ai-linux --no-actions` |
| Nothing works / unsure | `./ai-linux doctor` — every line should be ✓ |
