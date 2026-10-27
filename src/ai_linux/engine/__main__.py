"""Engine process entry point. PROTOCOL (one JSON object per line):

app -> engine: {"cmd": "settings", "values": {...}} (first line, then on every change) · "listen_now" ·
  {"cmd": "ptt", "pressed": bool} · "cancel" · {"cmd": "text", "text": str} ·
  {"cmd": "confirm_reply", "id": str, "allowed": bool} · "quit"
engine -> app: hello · state · level · turn · notice · confirm · followup · power · model · voice
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading

PROTOCOL = 1


def _protocol_streams():
    """Moves the app's pipes off fds 0/1, so child processes and native libraries can't read or write them."""
    rin, rout = os.dup(0), os.dup(1)  # dup'd fds are close-on-exec
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)  # stray prints from anywhere land in the log
    return open(rin, encoding="utf-8", errors="replace"), open(rout, "w", encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=os.environ.get("AI_LINUX_LOG", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    commands, out = _protocol_streams()
    lock = threading.Lock()

    def emit(obj: dict) -> None:
        line = json.dumps(obj, default=str)
        with lock:
            try:
                out.write(line + "\n")
                out.flush()
            except (BrokenPipeError, ValueError):
                os._exit(0)

    from ai_linux import __version__
    from ai_linux.engine.jiwo import prepare_openvino
    prepare_openvino()
    emit({"ev": "hello", "protocol": PROTOCOL, "version": __version__})

    engine = None
    for raw in commands:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        cmd = msg.get("cmd")
        if cmd == "settings":
            if engine is None:
                from ai_linux.engine.service import Engine
                engine = Engine(emit, msg.get("values") or {})
                engine.start()
            else:
                engine.update_settings(msg.get("values") or {})
        elif cmd == "quit":
            break
        elif engine is None:
            continue
        elif cmd == "listen_now":
            engine.listen_now()
        elif cmd == "ptt":
            engine.push_to_talk(bool(msg.get("pressed")))
        elif cmd == "cancel":
            engine.cancel_turn()
        elif cmd == "text":
            engine.submit_text(str(msg.get("text") or ""))
        elif cmd == "confirm_reply":
            engine.confirm_reply(str(msg.get("id")), bool(msg.get("allowed")))
    if engine is not None:
        engine.stop()
    sys.stderr.flush()
    os._exit(0)  # skip interpreter teardown: OpenVINO may still be compiling GPU kernels in the background


if __name__ == "__main__":
    sys.exit(main())
