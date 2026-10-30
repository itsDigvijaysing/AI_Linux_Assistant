"""Tools from the user's MCP servers (Model Context Protocol, stdio transport). Standard library only.

Each enabled server in the mcp_servers setting is started as a child process; its tools join the brain's menu as
``<server>_<tool>``. Every call is gated as ``mcp.<server>.<tool>`` (actions must be allowed) and, unless the
server's "confirm" is off, asks the user first. The result goes back to the big model to be put into words.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading

from ai_linux.engine.tools import TOOLS, Env, Result, Tool
from ai_linux.safety.gate import ConfirmRequest

log = logging.getLogger(__name__)

PROTOCOL = "2025-06-18"
START_S = 60.0  # npx/uvx may fetch the package on first start
CALL_S = 30.0


class McpError(Exception):
    pass


class McpServer:
    """One stdio server: newline-delimited JSON-RPC 2.0 over its stdin/stdout."""

    def __init__(self, spec: dict, start_timeout: float = START_S):
        self.name = str(spec["name"])
        self.timeout = float(spec.get("timeout") or CALL_S)
        env = {**os.environ, **{str(k): str(v) for k, v in (spec.get("env") or {}).items()}}
        argv = [str(spec["command"]), *(str(a) for a in spec.get("args") or [])]
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=env, start_new_session=True)
        self._id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, dict]] = {}
        threading.Thread(target=self._read, name=f"MCP {self.name}", daemon=True).start()
        threading.Thread(target=self._stderr, name=f"MCP {self.name} log", daemon=True).start()
        try:
            self.info = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                    "clientInfo": {"name": "ai-linux", "version": "3"}},
                                     start_timeout)
            self.notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def _send(self, msg: dict) -> None:
        data = (json.dumps(msg) + "\n").encode()
        with self._lock:
            if self.proc.stdin is None or self.proc.poll() is not None:
                raise McpError("the server has stopped")
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        ev, box = threading.Event(), {}
        with self._lock:
            self._id += 1
            rid = self._id
            self._pending[rid] = (ev, box)
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            if not ev.wait(timeout or self.timeout):
                raise McpError(f"{method}: no answer in {timeout or self.timeout:.0f} s")
        finally:
            self._pending.pop(rid, None)
        if "error" in box:
            err = box["error"] if isinstance(box["error"], dict) else {"message": str(box["error"])}
            raise McpError(str(err.get("message") or err))
        return box.get("result") or {}

    def _read(self) -> None:
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # some servers print banners on stdout
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:  # a request from the server
                reply = {"result": {}} if msg["method"] == "ping" else \
                    {"error": {"code": -32601, "message": "not supported"}}
                try:
                    self._send({"jsonrpc": "2.0", "id": msg["id"], **reply})
                except Exception:
                    pass
            elif msg.get("id") in self._pending:
                ev, box = self._pending[msg["id"]]
                box.update(msg)
                ev.set()
        for ev, box in list(self._pending.values()):  # the server exited: fail what's waiting
            box["error"] = {"message": "the server has stopped"}
            ev.set()

    def _stderr(self) -> None:
        for line in self.proc.stderr:
            log.debug("mcp %s: %s", self.name, line.decode(errors="replace").rstrip()[:300])

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        for _ in range(20):
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in page.get("tools") or [] if isinstance(t, dict) and t.get("name")]
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return tools

    def call(self, tool: str, args: dict) -> tuple[bool, str]:
        result = self.request("tools/call", {"name": tool, "arguments": args})
        parts = []
        for item in result.get("content") or []:
            kind = item.get("type")
            if kind == "text":
                parts.append(str(item.get("text", "")))
            elif kind == "resource":
                parts.append(str((item.get("resource") or {}).get("text") or "[a file]"))
            else:
                parts.append(f"[{kind}]")
        if not parts and result.get("structuredContent") is not None:
            parts.append(json.dumps(result["structuredContent"])[:4000])
        return not result.get("isError"), "\n".join(parts).strip()

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


def tool_name(server: str, tool: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}_{tool}")[:64]


def make_tool(server: McpServer, spec: dict, info: dict) -> Tool:
    schema = info.get("inputSchema") if isinstance(info.get("inputSchema"), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = tuple(str(r) for r in schema.get("required") or [] if r in props)
    raw, gate = str(info["name"]), f"mcp.{server.name}.{info['name']}"
    ask = spec.get("confirm", True) is not False

    def run(args: dict, env: Env) -> Result:
        if ask:
            what = f"{raw.replace('_', ' ')} from {server.name}"
            if env.confirm is None or not env.confirm(ConfirmRequest(gate, dict(args), command=what)):
                return Result(False, "Okay, I won't.")
        try:
            ok, text = server.call(raw, args)
        except Exception as exc:
            return Result(False, f"The {server.name} tool didn't answer.", str(exc))
        if not ok:
            return Result(False, f"The {server.name} tool failed.", text)
        return Result(True, text[:300] or "Done.", text, summarise=bool(text))

    description = " ".join(str(info.get("description") or raw).split())[:400]
    return Tool(tool_name(server.name, raw), description, props, run, family="mcp", required=required,
                needs_args=bool(props), gate_id=gate)


class McpTools:
    """Starts the enabled servers in parallel and collects their tools; a server that fails is reported and
    skipped."""

    def __init__(self, specs: list[dict], notice=None):
        self.servers: list[McpServer] = []
        self._tools: list[tuple[str, str, Tool]] = []  # (server, its own tool name, the tool)
        self.catalog: dict[str, list[dict]] = {}  # every tool each server offers, for the Tools page switches
        self._lock = threading.Lock()
        notice = notice or (lambda level, text: None)
        taken = {t.name for t in TOOLS}

        def start(spec: dict) -> None:
            server = None
            try:
                server = McpServer(spec)
                infos = server.list_tools()
            except Exception as exc:
                if server is not None:
                    server.close()  # started but unusable: don't leave its process running
                log.warning("mcp server %s: %s", spec.get("name"), exc)
                notice("warning", f"The {spec.get('name')} server didn't start: {exc}")
                return
            with self._lock:
                self.servers.append(server)
                listed = []
                for info in infos:
                    tool = make_tool(server, spec, info)
                    if tool.name in taken:
                        continue
                    taken.add(tool.name)
                    self._tools.append((server.name, str(info["name"]), tool))
                    listed.append({"name": str(info["name"]), "description": tool.description[:200]})
                self.catalog[server.name] = listed
            log.info("mcp server %s: %d tools", server.name, len(infos))

        threads = [threading.Thread(target=start, args=(s,), daemon=True) for s in specs
                   if s.get("command") and s.get("name")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(START_S + 15)

    def tools(self, disabled: dict[str, set[str]] | None = None) -> list[Tool]:
        """The tools to offer the models, leaving out the ones the user turned off per server."""
        disabled = disabled or {}
        with self._lock:
            return [t for server, raw, t in self._tools if raw not in disabled.get(server, ())]

    def close(self) -> None:
        for server in self.servers:
            server.close()

