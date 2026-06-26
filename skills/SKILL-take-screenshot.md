---
name: take-screenshot
trigger: user asks to take a screenshot, grab the screen, capture the screen, screenshot a window or area, or snap a picture of the screen
tools: [mcp.shell.run_command]
requires: gnome-screenshot — install once with `./ai-linux setup --extras` (no runtime sudo). If missing, say so.
---

Take a screenshot via mcp.shell.run_command. If `command -v gnome-screenshot` is empty, tell the user it needs a one-time `./ai-linux setup --extras` and do NOT pretend it worked.

- Whole screen: `gnome-screenshot -f "$HOME/Pictures/shot-$(date +%s).png"`
- Select an area: `gnome-screenshot -a -f "$HOME/Pictures/shot-$(date +%s).png"`
- Active window:  `gnome-screenshot -w -f "$HOME/Pictures/shot-$(date +%s).png"`

Tell the user where it was saved in one short sentence.
