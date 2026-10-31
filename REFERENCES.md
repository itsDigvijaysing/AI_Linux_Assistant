# References

The projects this app adapts code from, runs on or learned from are listed once, in `src/ai_linux/credits.py`, with
licences checked at the source. The app shows five of them by name under Preferences, About (plus an "And N more" page with the full list
and a thank-you), and the About dialog lists the licences of the code we ship.

## The refs/ folder

`refs/` holds shallow clones of the reference repos for reading. It is git-ignored: never commit it, never run its
code. Refresh or add clones with:

    scripts/sync_refs.py            # every credit with a git URL; --dry-run to preview, or name folders
    scripts/sync_refs.py openwhispr

A clone with local edits is skipped, never reset. Copy code only from MIT, Apache-2.0 or BSD projects, keep their
notice and add their licence text as `LICENSES/<name>.txt` (kind "code" in credits.py). Newelle is
GPL-3.0 and RealtimeVoiceChat has no licence file: read them, copy nothing.

## Study of 2026-10-28 (clones refreshed the same day, about 95 MB)

**GLaDOS** (`ea018f5`, 2026-10-10). Since our June copy, speech recognition and voice detection changed in one way
only: `src/glados/onnx_runtime.py` picks a GPU backend (CUDA, MIGraphX, ROCm, OpenVINO) with CPU fallback for all
audio models. No accuracy or bug fixes to port. The OpenVINO provider is the route for running Parakeet on the Intel
GPU, if `onnxruntime-openvino` replaces the CPU-only `onnxruntime`.

**computer-use-linux** (0.7.31, 2026-10-28). The `screenshot` tool and its `title` argument are unchanged, so
`bin/ai-linux-screenshot` keeps working. The installed binary is the June build. Newer builds shrink screenshots by
default (`max_width`, `max_height`, `max_bytes`), so pass larger limits if we update it.

**OpenWhispr** (2026-10-28), the model for dictation:
- Clean-up prompt (`src/locales/en/prompts.json`, `cleanupPrompt`; MIT): "the speaker is never talking to you", fillers,
  false starts, self-corrections, a delimited transcript and zero temperature. `POLISH` in `engine/brain.py` is adapted
  from it (2026-10-31), results in `bench/README.md`. whisper-flow in `refs/` has no rewriting; it isn't the commercial
  Wispr Flow.
- Mic recovery (`src/helpers/activeMicRecovery.js`, `micTrackHealth.js`): re-opens the mic when the default input
  changes or the stream ends, and re-opens it once if a new stream stays silent after the system idled the device,
  then falls back to the system default. Our engine only warns (`engine/mic_watch.py`); one automatic re-open before
  the warning is the next step.
- Parakeet runs through sherpa-onnx as an **int8** build of TDT 0.6B v3 (680 MB download, encoder about 620 MB);
  ours is FP16 (encoder 1.24 GB). Measured in `bench/README.md`: int8 is not more accurate, only smaller and faster on
  the CPU; the Intel GPU beats both. It also offers NVIDIA's streaming models
  (`nemotron-speech-streaming-en-0.6b`, `nemotron-3.5-asr-streaming-0.6b`, 560 ms chunks) and an English-only
  `parakeet-unified-en-0.6b` (`src/models/modelRegistryData.json`).
- Live text (`src/helpers/ipcHandlers.js` `transcribeDictationPreviewChunk`, its project notes, "Live Transcription
  Preview"): the streaming models send partial text over sherpa-onnx's online websocket server. Offline models like
  ours decode only the newest 1.5 s every 1.5 s, skip chunks below RMS 0.002, never start a second decode while one
  runs, and append the words; the final text still comes from decoding the whole recording. Their other live text is
  from cloud services. No word lists (hotwords) for Parakeet; their custom dictionary is Whisper-only.
- Long recordings are cut at pauses and each piece decoded on its own; an empty piece with sound in it is retried
  once (`src/helpers/parakeetSegmentation.js`, `parakeetServer.js`).
- Paste on GNOME Wayland (`resources/linux-fast-paste.c`, `src/helpers/clipboard.js`): the text goes on the
  clipboard, then the xdg-desktop-portal RemoteDesktop keyboard sends Ctrl+V, or Ctrl+Shift+V / Shift+Insert for
  terminals it recognises by window class. A saved restore token means the permission dialog shows once. No uinput,
  no ydotool daemon. Ours uses `wl-copy` plus `ydotool` Ctrl+V (`engine/tools.py`), which needs the daemon and
  doesn't paste into terminals.

**whisper-flow** (2026-10-26). Streaming on top of a non-streaming model (`whisperflow/streaming.py`): re-transcribe
the growing window, commit the words two passes agree on (`common_prefix`), keep the last second open, drop
committed audio. The commit only pays off past our 15 s command limit; the agreed prefix marks which live words have
settled. Small and MIT.

**DuckDuckGo MCP server** (0.7.0). Tools `search` and `fetch_content`, built-in rate limits, needs `httpx`,
`beautifulsoup4` and `mcp`. Fits as an off-by-default MCP preset for web lookup.

**Newelle, Fabric, AIChat** were refreshed but not studied this round; RealtimeVoiceChat has had no commits since
July 2025.

## Study of 2026-10-31: how others keep a voice assistant from hearing itself

Nobody in the set puts all listening behind PipeWire's module. **GLaDOS**: production says "use headphones or disable
`interruptible`"; its Rust plan (`docs/rust-migration.md`, `examples/sonora_aec_probe`) is in-process AEC3, one render
frame then one mic frame, NS and AGC off. **LiveKit** and **RealtimeVoiceChat**: the browser's AEC3 in the client;
LiveKit adds `aec_warmup_duration`, `false_interruption_timeout` and `min_words`. **Pipecat**: `AlwaysUserMuteStrategy`
(half-duplex) and an echo guard of about 300 ms. **Wyoming**: mutes the mic around playback. **OpenWhispr**: raw mic for
dictation, since it never speaks; its changelog warns that audio-only echo evidence discarded real speech, so it
compares transcripts. Adopted: GLaDOS's lock-step shape through pywebrtc-audio (Apache-2.0, AEC3), the raw mic when
silent, and comparing transcripts on every path. Measurements in `bench/README.md`.

## Adopted 2026-10-28

- **Portal paste** from OpenWhispr (`resources/linux-fast-paste.c`, `src/helpers/clipboard.js`), ported to Python
  as `src/ai_linux/paste_portal.py`: the same CreateSession, SelectDevices (keyboard, persistent), Start and keysym
  chain, the 3 s call bound and 10 s watchdog, the saved restore token, their terminal name list and AT-SPI
  terminal check, and their GNOME order (silent portal, ydotool, portal with dialog). ydotool is now optional.
- **Silent-mic re-open** from OpenWhispr (`micTrackHealth.js`): one quiet reopen before the warning.
- **Checked and left out:** GLaDOS's `SpokenTextConverter` (SuperTonic already reads numbers, money, dates, phone
  numbers and units, per its README); GLaDOS's `audio_providers()` (it would move speech recognition onto the
  NVIDIA card, which we keep for the big model); OpenWhispr's long-audio splitting (our commands stop at 15 s; it
  comes back with dictation).
- **Parakeet's encoder on the Intel GPU** through OpenVINO (GLaDOS's backend idea, our own `ov_encoder.py`), padded
  to whole seconds and warmed at start; the same words as the CPU on all 96 test clips. int8 isn't used: it only
  helps the CPU path.
- **Live words:** OpenWhispr's preview pacing (never two decodes at once, skip too-short audio) on a re-decode of the
  whole utterance, and whisper-flow's `common_prefix`/`normalize` (MIT, `LICENSES/whisper-flow.txt`) in
  `engine/live_text.py` to mark settled words.
- **Pill caption:** Blur my Shell (GPL-3.0) was read for its look only, nothing copied or linked. An app window gets
  no pixels from behind it on GNOME, so real blur works only inside the compositor; ours is frosted glass (tinted
  fill, sheen, hairline edge, soft shadow) drawn with GTK.
