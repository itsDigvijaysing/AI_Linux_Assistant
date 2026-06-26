---
name: lock-suspend-or-logout
trigger: user asks to lock the screen, lock the computer, suspend, sleep the laptop, log out, or sign out
tools: [mcp.shell.run_command]
requires: systemd + GNOME (loginctl/systemctl/gnome-session-quit) — present on Ubuntu 26.04; runs as the user, no sudo
---

Run ONE command via mcp.shell.run_command (all user-level, no sudo):

- Lock the screen:   `loginctl lock-session`
- Suspend / sleep:   `systemctl suspend`
- Log out / sign out: `gnome-session-quit --logout --no-prompt`

Confirm in one short sentence before/after (lock and log out are instant; suspend sleeps the machine). Do NOT log out or suspend unless the user clearly asked for that specific action — prefer locking if they're vague.
