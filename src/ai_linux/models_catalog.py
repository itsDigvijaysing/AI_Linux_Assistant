"""The models the app offers: a small decision model for battery mode and a big model on the GPU."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_linux.paths import MODELS_DIR


@dataclass(frozen=True)
class ModelSpec:
    id: str
    slot: str  # small | big | speech
    name: str
    family: str
    licence: str
    download_gb: float
    runtime: str  # ollama | openvino | onnx
    source: str  # Ollama tag or Hugging Face repo
    summary: str
    recommended: bool = False
    vram_gb: float = 0.0  # NVIDIA memory; 0 = runs on the Intel GPU or the CPU
    note: str = ""
    vision: bool = False


CATALOG: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="jiwo-0.8b", slot="small", name="jiwo 0.8B", family="Qwen3.5 decision model",
        licence="Apache-2.0", download_gb=0.78, runtime="openvino", source="itsDigvijaysing/jiwo-0.8b-openvino-int8",
        summary="Picks the command with a calibrated confidence in about half a second. Used on battery, on the "
                "Intel GPU (or the CPU), so the NVIDIA GPU can sleep.",
        recommended=True,
        note="8-bit OpenVINO build of eljiwo/jiwo-0.8b. Handled 74 % of commands alone in our tests.",
    ),
    ModelSpec(
        id="laya", slot="small", name="Laya", family="ModernBERT decision model",
        licence="Apache-2.0", download_gb=0.85, runtime="openvino",
        source="itsDigvijaysing/laya-typed-decisions-openvino",
        summary="Optional: answers in about a tenth of a second, but picks the right command less often than jiwo.",
        note="OpenVINO build of convaiinnovations/laya (typed-decisions). Right on 61 % of commands in our tests.",
    ),
    ModelSpec(
        id="parakeet-tdt", slot="speech", name="Parakeet TDT 0.6B", family="NVIDIA Parakeet",
        licence="CC-BY-4.0", download_gb=1.28, runtime="onnx", source="nvidia/parakeet-tdt-0.6b-v3",
        summary="Turns your speech into text on the Intel GPU (or the processor). The most accurate choice.", recommended=True,
    ),
    ModelSpec(
        id="parakeet-ctc", slot="speech", name="Parakeet CTC 110M", family="NVIDIA Parakeet",
        licence="CC-BY-4.0", download_gb=0.46, runtime="onnx", source="nvidia/parakeet-tdt_ctc-110m",
        summary="Faster and smaller, a little less accurate. For slower computers.",
    ),
    ModelSpec(
        id="qwen3.5:4b", slot="big", name="Qwen3.5 4B", family="Qwen3.5",
        licence="Apache-2.0", download_gb=3.3, runtime="ollama", source="qwen3.5:4b",
        summary="Answers questions and chat, fills in details, and can look at the screen. Fits a 6 GB GPU.",
        recommended=True, vram_gb=3.5, vision=True,
    ),
    ModelSpec(
        id="gemma4:e4b", slot="big", name="Gemma 4 E4B", family="Gemma 4",
        licence="Apache-2.0", download_gb=6.6, runtime="ollama", source="gemma4:e4b",
        summary="Alternative: quickest on yes/no and short decisions, slightly weaker at picking commands. "
                "Can look at the screen.",
        vram_gb=4.0, vision=True,
    ),
)


def get(model_id: str) -> ModelSpec | None:
    return next((m for m in CATALOG if m.id == model_id), None)


def for_slot(slot: str) -> list[ModelSpec]:
    return [m for m in CATALOG if m.slot == slot]


SLOT_KEYS = {"small": "small_model", "big": "big_model", "speech": "stt_engine"}


def local_path(spec: ModelSpec, base: Path = MODELS_DIR) -> Path:
    """Where a downloaded (non-Ollama) model lives."""
    return Path(base) / spec.source.split("/")[-1]
