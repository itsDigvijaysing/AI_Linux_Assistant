"""
NVIDIA PersonaPlex Pipeline - Speech-to-Speech Voice Assistant.

PersonaPlex is a real-time, full-duplex speech-to-speech conversational model
(nvidia/personaplex-7b-v1) built on the Moshi architecture (7B params).

It replaces the traditional ASR -> LLM -> TTS pipeline with a single
end-to-end model that can listen and speak simultaneously.

Features:
- Real-time full-duplex conversation
- Persona control via voice prompts and text prompts
- 16 voice presets (natural/variety, male/female)
- ~257ms response latency

This module provides:
1. Offline inference (process WAV files)
2. Server-based real-time conversation
"""

import os
import sys
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("voice_assistant.personaplex")

# Voice presets available in PersonaPlex
VOICE_PRESETS = {
    "Natural Female 0": "NATF0",
    "Natural Female 1": "NATF1",
    "Natural Female 2": "NATF2",
    "Natural Female 3": "NATF3",
    "Natural Male 0": "NATM0",
    "Natural Male 1": "NATM1",
    "Natural Male 2": "NATM2",
    "Natural Male 3": "NATM3",
    "Variety Female 0": "VARF0",
    "Variety Female 1": "VARF1",
    "Variety Female 2": "VARF2",
    "Variety Female 3": "VARF3",
    "Variety Female 4": "VARF4",
    "Variety Male 0": "VARM0",
    "Variety Male 1": "VARM1",
    "Variety Male 2": "VARM2",
    "Variety Male 3": "VARM3",
    "Variety Male 4": "VARM4",
}

DEFAULT_TEXT_PROMPTS = {
    "General Assistant": (
        "You are a helpful, friendly AI assistant. "
        "Answer questions clearly and concisely."
    ),
    "Customer Service": (
        "You are a professional customer service representative. "
        "Be polite, empathetic, and solution-oriented."
    ),
    "Teacher": (
        "You are a wise and friendly teacher. "
        "Answer questions or provide advice in a clear and engaging way."
    ),
    "Storyteller": (
        "You are an engaging storyteller. "
        "Tell stories with vivid descriptions and expressive narration."
    ),
}


def get_personaplex_dir() -> Path:
    """Get the PersonaPlex installation directory."""
    project_dir = Path(__file__).parent
    return project_dir / "personaplex"


def is_personaplex_installed() -> bool:
    """Check if PersonaPlex is properly installed."""
    try:
        import moshi  # noqa: F401
        return True
    except ImportError:
        return False


def check_hf_token() -> bool:
    """Check if HuggingFace token is set."""
    return bool(os.environ.get("HF_TOKEN"))


def get_voice_prompt_path(voice_id: str) -> str:
    """
    Get the voice prompt file path for a given voice ID.

    Args:
        voice_id: Voice preset ID (e.g., 'NATF2', 'NATM1')

    Returns:
        Voice prompt identifier to pass to PersonaPlex
    """
    return f"{voice_id}.pt"


class PersonaPlexPipeline:
    """
    NVIDIA PersonaPlex speech-to-speech pipeline.

    Uses the moshi.offline module for batch processing of audio files.
    """

    def __init__(
        self,
        voice_preset: str = "NATF2",
        text_prompt: str = "",
        gpu_id: int = 0,
        seed: int = 42424242,
    ):
        """
        Initialize PersonaPlex pipeline.

        Args:
            voice_preset: Voice ID (e.g., 'NATF2', 'NATM1')
            text_prompt: Text prompt defining the persona
            gpu_id: GPU device ID to use
            seed: Random seed for reproducibility
        """
        self.voice_preset = voice_preset
        self.text_prompt = text_prompt
        self.gpu_id = gpu_id
        self.seed = seed
        self._personaplex_dir = get_personaplex_dir()

        if not is_personaplex_installed():
            raise RuntimeError(
                "PersonaPlex is not installed. Run setup_env.sh or:\n"
                "  git clone https://github.com/NVIDIA/personaplex.git\n"
                "  pip install personaplex/moshi/."
            )

        if not check_hf_token():
            logger.warning(
                "HF_TOKEN not set. PersonaPlex requires a HuggingFace token. "
                "Set it with: export HF_TOKEN=<your_token>"
            )

    def process_audio(
        self,
        input_audio_path: str,
        output_dir: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Process an audio file through PersonaPlex (offline mode).

        Args:
            input_audio_path: Path to input WAV file (24kHz recommended)
            output_dir: Directory for output files (auto-created if None)

        Returns:
            Tuple of (output_wav_path, output_text_json) or (None, None) on failure
        """
        if not os.path.exists(input_audio_path):
            logger.error(f"Input audio file not found: {input_audio_path}")
            return None, None

        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="personaplex_")

        output_wav = os.path.join(output_dir, "response.wav")
        output_text = os.path.join(output_dir, "response.json")

        # Build the command
        cmd = [
            sys.executable, "-m", "moshi.offline",
            "--voice-prompt", get_voice_prompt_path(self.voice_preset),
            "--input-wav", input_audio_path,
            "--seed", str(self.seed),
            "--output-wav", output_wav,
            "--output-text", output_text,
        ]

        if self.text_prompt:
            cmd.extend(["--text-prompt", self.text_prompt])

        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(self.gpu_id)

        logger.info(f"Running PersonaPlex offline inference on GPU {self.gpu_id}")
        logger.debug(f"Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                env=env,
            )

            if result.returncode != 0:
                logger.error(f"PersonaPlex failed: {result.stderr}")
                return None, None

            logger.info(f"PersonaPlex output saved to: {output_wav}")

            # Read the transcript if available
            transcript = None
            if os.path.exists(output_text):
                with open(output_text, "r") as f:
                    transcript = f.read()

            return output_wav, transcript

        except subprocess.TimeoutExpired:
            logger.error("PersonaPlex inference timed out (300s)")
            return None, None
        except Exception as e:
            logger.error(f"PersonaPlex error: {e}")
            return None, None

    def start_server(
        self,
        port: int = 8998,
        cpu_offload: bool = False,
    ) -> Optional[subprocess.Popen]:
        """
        Start the PersonaPlex real-time server.

        Args:
            port: Port number for the server
            cpu_offload: Enable CPU offloading for memory-constrained GPUs

        Returns:
            Popen process handle or None on failure
        """
        ssl_dir = tempfile.mkdtemp(prefix="personaplex_ssl_")

        cmd = [
            sys.executable, "-m", "moshi.server",
            "--ssl", ssl_dir,
        ]

        if cpu_offload:
            cmd.append("--cpu-offload")

        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(self.gpu_id)

        logger.info(f"Starting PersonaPlex server on port {port}, GPU {self.gpu_id}")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            logger.info(
                f"PersonaPlex server started. "
                f"Open https://localhost:{port} in your browser."
            )
            return process

        except Exception as e:
            logger.error(f"Failed to start PersonaPlex server: {e}")
            return None


def convert_audio_to_wav24k(input_path: str, output_path: str) -> bool:
    """
    Convert audio file to 24kHz WAV (required by PersonaPlex).

    Args:
        input_path: Input audio file path
        output_path: Output WAV file path

    Returns:
        True if successful
    """
    try:
        import soundfile as sf

        data, sr = sf.read(input_path)
        if sr != 24000:
            import scipy.signal
            num_samples = int(len(data) * 24000 / sr)
            data = scipy.signal.resample(data, num_samples)
        # Ensure mono
        if len(data.shape) > 1:
            data = data.mean(axis=1)
        sf.write(output_path, data, 24000)
        return True
    except Exception as e:
        logger.error(f"Audio conversion failed: {e}")
        return False


def process_audio_with_personaplex(
    input_audio_path: str,
    voice_preset: str = "NATF2",
    text_prompt: str = "",
    gpu_id: int = 2,
) -> Tuple[Optional[str], Optional[str]]:
    """
    High-level function to process audio through PersonaPlex.

    Handles audio conversion and inference in one call.

    Args:
        input_audio_path: Path to input audio file (any format)
        voice_preset: Voice preset ID
        text_prompt: Persona text prompt
        gpu_id: GPU device ID

    Returns:
        Tuple of (output_wav_path, transcript_text)
    """
    # Convert to 24kHz WAV if needed
    tmp_dir = tempfile.mkdtemp(prefix="personaplex_")
    converted_path = os.path.join(tmp_dir, "input_24k.wav")

    if not convert_audio_to_wav24k(input_audio_path, converted_path):
        logger.error("Failed to convert input audio")
        return None, None

    pipeline = PersonaPlexPipeline(
        voice_preset=voice_preset,
        text_prompt=text_prompt,
        gpu_id=gpu_id,
    )

    output_wav, transcript = pipeline.process_audio(
        converted_path,
        output_dir=tmp_dir,
    )

    return output_wav, transcript
