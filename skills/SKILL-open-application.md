---
name: open-application
trigger: user asks to open / launch / switch to an app or window
tools: [mcp.computer_use.*]
requires: computer_use MCP server enabled (Phase 3); all actions are confirm-gated
---

1. Identify the target application from the user's request.
2. Use the `mcp.computer_use` window/launch tools to open or focus it.
3. Each action requires user approval (the confirm-before-execute safety gate).
   If the user denies it, stop and say so — do not retry silently.
