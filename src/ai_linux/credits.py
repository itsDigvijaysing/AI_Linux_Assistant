"""Every open-source project the app adapts code from, runs, is built on or learned from: one list for the About
screens and scripts/sync_refs.py. Licences checked at the source (repo, model card, package metadata) on 2026-10-28.
"""

from __future__ import annotations

from dataclasses import dataclass

KINDS = (
    ("code", "Code adapted from", "Their code, changed for this app; full licence texts are included."),
    ("model", "Models", "Downloaded and run on this computer."),
    ("library", "Built with", "Libraries and services the app runs on."),
    ("reference", "Learned from", "Studied for ideas and design; no code copied."),
)

# SPDX id -> Gtk.License member name; anything else is shown as custom text with a link.
GTK_LICENCES = {
    "MIT": "MIT_X11", "Apache-2.0": "APACHE_2_0", "BSD-3-Clause": "BSD_3", "BSD-2-Clause": "BSD",
    "LGPL-2.1-or-later": "LGPL_2_1", "GPL-3.0": "GPL_3_0",
}


@dataclass(frozen=True)
class Credit:
    name: str
    role: str
    licence: str  # SPDX id where one exists
    url: str
    kind: str
    owner: str = ""
    licence_file: str = ""  # full text bundled at the repo root
    git: str = ""  # cloned into refs/ for study
    featured: bool = False  # listed first in its About section


CREDITS: tuple[Credit, ...] = (
    # code
    Credit("GLaDOS", "Speech recognition and voice detection code; the safety checks grew from it", "MIT",
           "https://github.com/dnhkng/GLaDOS", "code", "David Ng", "LICENSE.GLaDOS",
           "https://github.com/dnhkng/GLaDOS.git", featured=True),
    Credit("OpenWhispr", "Pasting at the cursor through the desktop portal; reopening a silent microphone; "
           "pacing of the words shown while you speak", "MIT",
           "https://github.com/OpenWhispr/openwhispr", "code", "OpenWhispr Team", "LICENSE.OpenWhispr",
           "https://github.com/OpenWhispr/openwhispr.git"),
    Credit("whisper-flow", "Which words shown while you speak have settled", "MIT",
           "https://github.com/dimastatz/whisper-flow", "code", "Dima Statz", "LICENSE.whisper-flow",
           "https://github.com/dimastatz/whisper-flow.git"),
    Credit("Laya", "Prompt layout and calibration of the optional small decision model", "Apache-2.0",
           "https://huggingface.co/convaiinnovations/laya", "code", "Convai Innovations", "LICENSE.Laya"),
    Credit("computer-use-linux", "Screenshots through the screen-capture portal; window control", "MIT",
           "https://github.com/agent-sh/computer-use-linux", "code", "Avi Fenesh", "LICENSE.computer-use-linux",
           "https://github.com/agent-sh/computer-use-linux.git"),
    # models
    Credit("Parakeet TDT 0.6B v3", "Speech recognition", "CC-BY-4.0",
           "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3", "model", "NVIDIA", featured=True),
    Credit("Silero VAD", "Hears when you start and stop speaking", "MIT",
           "https://github.com/snakers4/silero-vad", "model", "Silero Team"),
    Credit("SuperTonic 3", "The assistant's voices", "OpenRAIL",
           "https://huggingface.co/Supertone/supertonic-3", "model", "Supertone", featured=True),
    Credit("Qwen3.5-4B", "The big model: answers, writing, harder requests", "Apache-2.0",
           "https://huggingface.co/Qwen/Qwen3.5-4B", "model", "Qwen Team, Alibaba Cloud", featured=True),
    Credit("jiwo-0.8b", "Picks the right tool on battery (built on Qwen3.5-0.8B)", "Apache-2.0",
           "https://huggingface.co/eljiwo/jiwo-0.8b", "model", "eljiwo"),
    Credit("Laya typed decisions", "Optional small model for fixed choices", "Apache-2.0",
           "https://huggingface.co/convaiinnovations/laya", "model", "Convai Innovations"),
    # libraries and services
    Credit("GTK 4 and libadwaita", "The app's window and controls", "LGPL-2.1-or-later",
           "https://gnome.pages.gitlab.gnome.org/libadwaita/", "library", "The GNOME Project"),
    Credit("PyGObject", "Python bindings for GTK", "LGPL-2.1-or-later",
           "https://pygobject.gnome.org/", "library", "The PyGObject authors"),
    Credit("Ollama", "Runs the big model", "MIT", "https://github.com/ollama/ollama", "library", "Ollama",
           featured=True),
    Credit("PipeWire", "Microphone, speakers and echo cancelling", "MIT", "https://pipewire.org/", "library",
           "The PipeWire authors"),
    Credit("ONNX Runtime", "Runs speech recognition, voice detection and the voices", "MIT",
           "https://onnxruntime.ai/", "library", "Microsoft"),
    Credit("OpenVINO", "Runs the small models on the Intel GPU", "Apache-2.0",
           "https://github.com/openvinotoolkit/openvino", "library", "Intel"),
    Credit("supertonic", "Text-to-speech library", "MIT", "https://github.com/supertone-inc/supertonic",
           "library", "Supertone"),
    Credit("Tokenizers", "Turns text into model tokens", "Apache-2.0", "https://github.com/huggingface/tokenizers",
           "library", "Hugging Face"),
    Credit("huggingface_hub", "Downloads the voices", "Apache-2.0",
           "https://github.com/huggingface/huggingface_hub", "library", "Hugging Face"),
    Credit("NumPy", "Audio maths", "BSD-3-Clause", "https://numpy.org/", "library", "NumPy Developers"),
    Credit("Numba", "Fast speech features", "BSD-2-Clause", "https://numba.pydata.org/", "library",
           "Anaconda and contributors"),
    Credit("llvmlite", "Compiler used by Numba", "BSD-2-Clause AND Apache-2.0 WITH LLVM-exception",
           "https://github.com/numba/llvmlite", "library", "Anaconda and contributors"),
    Credit("python-soundfile", "Reads and writes sound files", "BSD-3-Clause",
           "https://github.com/bastibe/python-soundfile", "library", "Bastian Bechtold"),
    Credit("pydantic", "Settings checks", "MIT", "https://github.com/pydantic/pydantic", "library",
           "Pydantic Services"),
    Credit("PyYAML", "Reads model settings", "MIT", "https://github.com/yaml/pyyaml", "library",
           "Ingy döt Net and Kirill Simonov"),
    Credit("threadpoolctl", "Keeps maths libraries from using every core", "BSD-3-Clause",
           "https://github.com/joblib/threadpoolctl", "library", "threadpoolctl developers"),
    # references
    Credit("RealtimeVoiceChat", "Turn-taking and talking over the assistant", "No licence file (README says MIT)",
           "https://github.com/KoljaB/RealtimeVoiceChat", "reference",
           git="https://github.com/KoljaB/RealtimeVoiceChat.git"),
    Credit("Newelle", "GNOME assistant: approving commands, GTK 4 design", "GPL-3.0",
           "https://github.com/qwersyk/Newelle", "reference", git="https://github.com/qwersyk/Newelle.git"),
    Credit("DuckDuckGo MCP server", "Web lookup without an API key", "MIT",
           "https://github.com/nickclyde/duckduckgo-mcp-server", "reference",
           git="https://github.com/nickclyde/duckduckgo-mcp-server.git"),
    Credit("AIChat", "Local retrieval and an Ollama front end", "Apache-2.0", "https://github.com/sigoden/aichat",
           "reference", git="https://github.com/sigoden/aichat.git"),
    Credit("Fabric", "Task prompt patterns", "MIT", "https://github.com/danielmiessler/Fabric", "reference",
           git="https://github.com/danielmiessler/fabric.git"),
)


def featured() -> list[Credit]:
    return [c for c in CREDITS if c.featured]


def notices_text() -> str:
    """Every credit with its licence, as one plain-text page."""
    lines = ["Thank you to the open-source community. This app is built on the work of these projects and the "
             "people behind them.", ""]
    for kind, title, description in KINDS:
        lines += [title.upper(), description, ""]
        for c in by_kind(kind):
            owner = f", {c.owner}" if c.owner else ""
            lines += [f"{c.name} ({c.licence}{owner})", f"  {c.role}", f"  {c.url}", ""]
    return "\n".join(lines).rstrip() + "\n"


def by_kind(kind: str) -> list[Credit]:
    return [c for c in CREDITS if c.kind == kind]


def ref_dir(credit: Credit) -> str:
    """Folder name under refs/, from the clone URL (keeps the names of the clones made in June)."""
    return credit.git.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def gtk_licence(credit: Credit) -> str | None:
    return GTK_LICENCES.get(credit.licence)
