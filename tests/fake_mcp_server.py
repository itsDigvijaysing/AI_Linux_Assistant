"""A tiny stdio MCP server for the engine tests: tools echo and fail."""

import json
import sys

TOOLS = [
    {"name": "echo", "description": "Repeat the text.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "fail", "description": "Always fails.", "inputSchema": {"type": "object", "properties": {}}},
]

print("banner text a sloppy server prints", flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method, params = msg["method"], msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call" and params["name"] == "echo":
        result = {"content": [{"type": "text", "text": "echo: " + params["arguments"]["text"]}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "it broke"}], "isError": True}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
