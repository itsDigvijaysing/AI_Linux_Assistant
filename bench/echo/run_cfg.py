"""One live run: replies through the sink under test, a second voice played straight to the speaker as 'the user'."""
import json, os, re, subprocess as sp, sys, time
top = sys.argv[4] if len(sys.argv) > 4 else ""; here, name, props = sys.argv[1], sys.argv[2], sys.argv[3]  # props: extra node props, or "none" for no module
out = f"{here}/{name}"; os.makedirs(out, exist_ok=True)
HW_IN = "alsa_input.usb-ASUSTeK_COMPUTER_INC._C-Media_R__Audio-00.analog-stereo"
HW_OUT = "alsa_output.usb-ASUSTeK_COMPUTER_INC._C-Media_R__Audio-00.analog-stereo"
def nodes(): return sp.run(["pw-cli", "ls", "Node"], capture_output=True, text=True, timeout=5).stdout
mod = None; sink = HW_OUT
if props != "none":
    cmd = ("load-module libpipewire-module-echo-cancel { library.name = aec/libspa-aec-webrtc " + top + " "
           f'source.props = {{ node.name = ai_aec_source node.description = "AI AEC Mic" {props} }} '
           f'sink.props = {{ node.name = ai_aec_sink node.description = "AI AEC Speaker" {props} }} }}\n')
    mod = sp.Popen(["pw-cli"], stdin=sp.PIPE, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
    mod.stdin.write(cmd.encode()); mod.stdin.flush()
    for _ in range(40):
        if "ai_aec_source" in nodes(): break
        time.sleep(0.1)
    else: raise SystemExit("module did not load")
    sink = "ai_aec_sink"
    thr_sched = sp.run(f"ps -L -o tid,cls,rtprio,ni,comm -p {mod.pid}", shell=True, capture_output=True, text=True).stdout
    print("  pw-cli threads:", " | ".join(" ".join(l.split()) for l in thr_sched.splitlines()[1:]))
    print("  pipewire daemon rt:", " | ".join(" ".join(l.split()) for l in sp.run("ps -L -o tid,cls,rtprio,ni,comm -C pipewire", shell=True, capture_output=True, text=True).stdout.splitlines()[1:]))
def rec(target, fn, extra=()):
    f = open(f"{out}/{fn}.raw", "wb")
    return sp.Popen(["pw-record", "--container", "raw", "--target", target, *extra, "--rate", "16000", "--channels", "1",
                     "--format", "s16", "-"], stdout=f, stderr=sp.DEVNULL)
def dl_tid():
    if not mod: return None
    for t in os.listdir(f"/proc/{mod.pid}/task"):
        if open(f"/proc/{mod.pid}/task/{t}/comm").read().startswith("data-loop"): return t
def cpu(t):
    f = open(f"/proc/{mod.pid}/task/{t}/stat").read().rsplit(")", 1)[1].split(); return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK"), int(f[36])
tid = dl_tid(); c0 = cpu(tid)[0] if tid else 0
wall0 = time.time(); recs = [rec(HW_IN, "raw")]
if mod: recs.append(rec("ai_aec_source", "aec"))
t0 = time.monotonic(); ev = []
def play(clip, target, wait=True, tag=""):
    t = time.monotonic() - t0
    p = sp.Popen(["pw-play", "--target", target, f"{here}/{clip}.wav"], stderr=sp.DEVNULL)
    ev.append({"clip": clip, "tag": tag, "t": t})
    if wait: p.wait(); ev[-1]["end"] = time.monotonic() - t0
    return p
time.sleep(2.0)
play("reply0", sink, tag="echo"); play("reply1", sink, tag="echo")
time.sleep(2.5)
play("user0", HW_OUT, tag="user")
time.sleep(2.0)
dt = {"clip": "reply2", "tag": "dt", "t": time.monotonic() - t0}
p = sp.Popen(["pw-play", "--target", sink, f"{here}/reply2.wav"], stderr=sp.DEVNULL); ev.append(dt)
time.sleep(0.8)
q = play("user1", HW_OUT, wait=False, tag="user_dt")
top = sp.run(["pw-top", "-b", "-n", "3"], capture_output=True, text=True, timeout=12).stdout.splitlines()
hw = open("/proc/asound/card1/pcm0p/sub0/hw_params").read().split("\n")
p.wait(); dt["end"] = time.monotonic() - t0
play("reply3", sink, tag="echo")
q.wait(); [e for e in ev if e["tag"] == "user_dt"][0]["end"] = None
time.sleep(1.5)
top2 = sp.run(["pw-top", "-b", "-n", "2"], capture_output=True, text=True, timeout=8).stdout.splitlines()
dur = time.monotonic() - t0
dl_cpu = (cpu(tid)[0] - c0) / dur * 100 if tid else 0; dl_core = cpu(tid)[1] if tid else -1
for r in recs: r.terminate()
for r in recs: r.wait(timeout=3)
if mod:
    mod.stdin.close(); mod.terminate(); mod.wait(timeout=3)
time.sleep(1.0)
j = sp.run(["journalctl", "--user", "-u", "pipewire", "--since", f"@{int(wall0)}", "--no-pager", "-o", "cat"],
           capture_output=True, text=True, timeout=20).stdout
hits = re.findall(r"front:1p: follower avail:(\d+) delay:(-?\d+) target:(\d+) thr:(\d+), resync \((\d+) suppressed", j)
resync = sum(1 + int(h[4]) for h in hits)
jt = sp.run(["journalctl", "--user", "-u", "pipewire", "--since", f"@{int(wall0)}", "--no-pager", "-o", "short-unix"], capture_output=True, text=True, timeout=20).stdout
print("  resync times (s after start: n):", " ".join(f"{float(l.split()[0]) - wall0:.0f}:{1 + int(re.search(r'[(](\d+) suppressed', l).group(1))}" for l in jt.splitlines() if "resync" in l))
tops = [re.sub(r"\s+", " ", l)[:95] for l in top2[-14:] if "alsa_" in l or "ai_aec" in l or "echo-cancel" in l or "pw-" in l]
print(name, "profile", sp.run(["powerprofilesctl","get"],capture_output=True,text=True).stdout.strip(), "aec_thread_cpu%", round(dl_cpu,1), "core", dl_core, "dur", round(dur, 1), "resyncs", resync, "per_s", round(resync / dur, 1),
      "delay_seen", sorted({int(h[1]) for h in hits})[:6], "thr", sorted({h[3] for h in hits}))
print(" ", [l for l in hw if l.startswith(("period_size", "buffer_size"))]); print("  " + "\n  ".join(tops))
json.dump({"top": tops, "ev": ev, "dur": dur, "resync": resync, "props": props, "wall0": wall0, "dl_cpu": dl_cpu}, open(f"{out}/meta.json", "w"), indent=0)
