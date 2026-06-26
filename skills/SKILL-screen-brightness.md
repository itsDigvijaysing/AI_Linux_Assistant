---
name: change-screen-brightness
trigger: user asks to change / raise / lower / set the screen or display brightness, or to dim or brighten the screen
tools: [mcp.shell.run_command]
requires: brightnessctl (one-time install) — the raw backlight is root-only and GNOME exposes no screen-brightness D-Bus interface on this machine
---

Screen brightness needs the `brightnessctl` helper (it ships a udev rule so a normal user can set brightness; the
`/sys/class/backlight/intel_backlight` file is otherwise root-only here). Use `mcp.shell.run_command`:

1. Check it is installed: `command -v brightnessctl`.
   - If missing, tell the user it needs a one-time install (`sudo apt install brightnessctl`) and that the
     install needs their password, so you can't do it silently. Don't claim you changed brightness if it isn't installed.
2. Once installed:
   - Set a level:  `brightnessctl set 50%`   (use the percent the user asked for)
   - Brighter:     `brightnessctl set 10%+`
   - Dimmer:       `brightnessctl set 10%-`
   - Check:        `brightnessctl get` / `brightnessctl max`

(Keyboard backlight, if asked, is separate: `brightnessctl --device='*::kbd_backlight' set 50%`.) Confirm in one short sentence.
