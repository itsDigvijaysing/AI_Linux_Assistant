#!/usr/bin/env python3
"""Whole-laptop power and latency of the small router model on NVIDIA vs Intel iGPU vs CPU.

Power = battery current x voltage (only valid on battery; RAPL needs root). Usage:
  power_test.py smoke   # check each device placement + latency, ~3 min, any power state
  power_test.py run     # full power test, ~30 min, on battery, Resources app closed
"""

import json
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path("/home/king/Documents/Projects/AI_Linux_Assistant")
sys.path.insert(0, str(REPO / "src"))
from ai_linux.tools_catalog import TOOLS  # noqa: E402

OUT = Path(__file__).resolve().parent / "results"
OLLAMA = str(Path.home() / ".local/bin/ollama")
MODEL = "qwen3.5:0.8b"
BAT = Path("/sys/class/power_supply/BAT1")
DGPU = Path("/sys/bus/pci/devices/0000:01:00.0/power/runtime_status")
PORTS = {"nvidia": 11441, "intel": 11442, "cpu": 11443}

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
OPTIONS = [(t.name, t.description) for t in TOOLS] + [("other", "Not a desktop command: chat, questions, anything else.")]
SYSTEM = ("You route one spoken request for a Linux desktop assistant. Answer with the letter of the best option only.\n"
          + "\n".join(f"{LETTERS[i]}) {n}: {d}" for i, (n, d) in enumerate(OPTIONS)))
UTTERANCES = [
    "turn the volume down a bit", "open firefox", "what's the capital of france", "take a screenshot",
    "lock my screen please", "uh can you make the screen brighter", "search the web for pizza near me",
    "pause the music", "turn on night light", "how are you today", "open the terminal",
    "show me my downloads folder", "do not disturb on", "open wifi settings", "copy that to the clipboard",
    "switch to a female voice", "go to sleep", "list files in my home directory", "tell me a joke",
    "why is my laptop so slow",
]


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(OUT / "power_test.log", "a") as f:
        f.write(line + "\n")


def http(port, path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def watts():
    try:
        return int((BAT / "current_now").read_text()) * int((BAT / "voltage_now").read_text()) / 1e12
    except (OSError, ValueError):
        return float("nan")


def dgpu():
    return DGPU.read_text().strip()


class Sampler(threading.Thread):
    """Samples whole-laptop watts and dGPU state every 2 s, tagged with the current phase."""

    def __init__(self):
        super().__init__(daemon=True)
        self.phase, self.samples, self.stop = None, [], False

    def run(self):
        while not self.stop:
            if self.phase:
                self.samples.append((self.phase, time.time(), watts(), dgpu()))
            time.sleep(2)


class Server:
    def __init__(self, device):
        self.device, self.port = device, PORTS[device]
        env = dict(os.environ, OLLAMA_HOST=f"127.0.0.1:{self.port}", OLLAMA_KEEP_ALIVE="-1",
                   OLLAMA_NO_CLOUD="1", OLLAMA_CONTEXT_LENGTH="4096", OLLAMA_NUM_PARALLEL="1",
                   OLLAMA_MAX_LOADED_MODELS="1")
        if device == "nvidia":
            env.update(OLLAMA_VULKAN="0")
        elif device == "intel":
            # Ollama skips integrated GPUs unless OLLAMA_IGPU_ENABLE is set (seen in its device log)
            env.update(CUDA_VISIBLE_DEVICES="-1", OLLAMA_VULKAN="1", OLLAMA_IGPU_ENABLE="1", GGML_VK_VISIBLE_DEVICES="0",
                       VK_DRIVER_FILES="/usr/share/vulkan/icd.d/intel_icd.json")
        else:
            env.update(CUDA_VISIBLE_DEVICES="-1", OLLAMA_VULKAN="0")
        self.logpath = OUT / f"ollama-{device}.log"
        self.proc = subprocess.Popen([OLLAMA, "serve"], env=env, stdout=open(self.logpath, "w"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(60):
            try:
                http(self.port, "/api/version", timeout=2)
                return
            except OSError:
                time.sleep(0.5)
        raise RuntimeError(f"ollama ({device}) did not start")

    def opts(self):
        o = {"temperature": 0, "num_predict": 1}
        if self.device == "cpu":
            o["num_gpu"] = 0
        return o

    def load(self):
        t = time.time()
        http(self.port, "/api/generate", {"model": MODEL, "prompt": "", "keep_alive": -1, "options": self.opts()})
        ps = http(self.port, "/api/ps").get("models", [])
        return time.time() - t, ps

    def route(self, text):
        t = time.time()
        r = http(self.port, "/api/chat", {
            "model": MODEL, "stream": False, "think": False, "keep_alive": -1, "options": self.opts(),
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]})
        return time.time() - t, r.get("message", {}).get("content", "").strip()[:3]

    def placement(self):
        keys = ("offloaded", "using device", "inference compute", "Vulkan0", "CUDA0", "model buffer size")
        lines = [ln.strip()[-160:] for ln in self.logpath.read_text(errors="replace").splitlines()
                 if any(k in ln for k in keys)]
        return lines[-6:]

    def nvidia_handles(self):
        """Counts NVIDIA device files and CUDA libraries held by this server and its runners."""
        pids, fds, libs = [self.proc.pid], 0, 0
        try:
            pids += [int(p) for p in subprocess.run(["pgrep", "-g", str(self.proc.pid)], capture_output=True,
                                                    text=True).stdout.split()]
        except OSError:
            pass
        for pid in set(pids):
            try:
                fds += sum("/dev/nvidia" in os.readlink(f"/proc/{pid}/fd/{fd}") for fd in os.listdir(f"/proc/{pid}/fd"))
                libs += sum(("libcuda" in ln or "ggml-cuda" in ln) for ln in open(f"/proc/{pid}/maps"))
            except OSError:
                pass
        return fds, libs

    def close(self):
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=15)
        except Exception:
            os.killpg(self.proc.pid, signal.SIGKILL)


def wait_dgpu_suspended(limit):
    t = time.time()
    while time.time() - t < limit:
        if dgpu() == "suspended":
            return round(time.time() - t, 1)
        time.sleep(1)
    return None


def latency_burst(srv, n=40):
    times, answers = [], []
    for i in range(n):
        dt, a = srv.route(UTTERANCES[i % len(UTTERANCES)])
        times.append(dt)
        answers.append(a)
    times.sort()
    return {"median_ms": round(1000 * statistics.median(times)), "p90_ms": round(1000 * times[int(0.9 * len(times)) - 1]),
            "answers": answers[:len(UTTERANCES)]}


def smoke():
    for device in sys.argv[2:] or ("intel", "cpu", "nvidia"):
        srv = Server(device)
        try:
            load_s, ps = srv.load()
            lat = latency_burst(srv, 20)
            log(f"[{device}] load {load_s:.1f}s ps={[(m['name'], round(m['size_vram'] / 1e6)) for m in ps]} MB-in-vram")
            log(f"[{device}] nvidia handles (fds, cuda libs) = {srv.nvidia_handles()}")
            log(f"[{device}] latency median {lat['median_ms']} ms p90 {lat['p90_ms']} ms; answers {lat['answers']}")
            for ln in srv.placement():
                log(f"[{device}]   {ln}")
        finally:
            srv.close()


def run():
    # Battery power is the only meter readable without root; Resources keeps the dGPU awake on its own.
    log("waiting for: charger unplugged and the Resources app closed")
    for _ in range(1800):
        ready = ((BAT / "status").read_text().strip() == "Discharging"
                 and subprocess.run(["pgrep", "-x", "resources"], capture_output=True).returncode != 0)
        if ready:
            break
        time.sleep(1)
    else:
        sys.exit("gave up: still on the charger or Resources still open after 30 min")
    s = Sampler()
    s.start()
    results = {"phases": {}, "latency": {}, "notes": []}

    def measure(name, seconds, srv=None, every=None):
        s.phase, end, next_req = name, time.time() + seconds, time.time()
        while time.time() < end:
            if srv and every and time.time() >= next_req:
                srv.route(UTTERANCES[int(time.time()) % len(UTTERANCES)])
                next_req += every
            time.sleep(0.5)
        s.phase = None
        summarize(name)

    def summarize(name):
        rows = [w for p, _, w, _ in s.samples if p == name]
        states = [st for p, _, _, st in s.samples if p == name]
        results["phases"][name] = {"watts": round(statistics.mean(rows), 2), "n": len(rows),
                                   "stdev": round(statistics.pstdev(rows), 2),
                                   "dgpu": {st: states.count(st) for st in set(states)}}
        log(f"{name}: {results['phases'][name]}")

    def baseline(name):
        waited = wait_dgpu_suspended(120)
        results["notes"].append(f"{name}: dGPU suspended after {waited}s")
        time.sleep(10)
        measure(name, 60)

    def burst(device, srv, n):
        # back-to-back requests: watts x time gives energy per request
        s.phase = f"{device}: back-to-back requests"
        t = time.time()
        results["latency"][device] = latency_burst(srv, n)
        s.phase = None
        summarize(f"{device}: back-to-back requests")
        results["latency"][device]["seconds_per_request"] = round((time.time() - t) / n, 3)
        log(f"{device} latency {results['latency'][device]}")

    log(f"start, battery {(BAT / 'capacity').read_text().strip()}%")
    baseline("A1 baseline, nothing loaded")
    for device in ("intel", "cpu", "nvidia"):
        srv = Server(device)
        try:
            load_s, ps = srv.load()
            results["notes"].append(f"{device}: load {load_s:.1f}s, nvidia handles {srv.nvidia_handles()}, "
                                    f"vram MB {[round(m['size_vram'] / 1e6) for m in ps]}, placement {srv.placement()}")
            if device != "nvidia":
                results["notes"].append(f"{device}: dGPU suspended after {wait_dgpu_suspended(90)}s")
            time.sleep(5)
            if device != "cpu":  # a model idling in RAM costs the CPU nothing, so baseline covers it
                measure(f"{device}: model loaded, idle", 60, srv)
            measure(f"{device}: one request every 10 s", 60, srv, every=10)
            burst(device, srv, 10 if device == "cpu" else 20)
        finally:
            srv.close()
    results["notes"].append(f"after nvidia: dGPU suspended after {wait_dgpu_suspended(120)}s")
    baseline("A2 baseline, nothing loaded")
    s.stop = True
    log(f"end, battery {(BAT / 'capacity').read_text().strip()}%")
    results["samples"] = s.samples
    (OUT / "power_results.json").write_text(json.dumps(results, indent=2))
    log(json.dumps(results, indent=2))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    {"smoke": smoke, "run": run}[sys.argv[1] if len(sys.argv) > 1 else "smoke"]()
