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
import time
import atexit
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


class PersonaPlexServerManager:
    """Manages the PersonaPlex moshi.server process for real-time conversation."""

    _instance = None

    @classmethod
    def get_instance(cls) -> "PersonaPlexServerManager":
        """Singleton to prevent multiple server instances."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._port: int = 8998
        self._gpu_id: int = 2
        self._host: str = "0.0.0.0"
        self._ssl_dir: Optional[str] = None
        self._log_lines: list = []
        atexit.register(self._cleanup)

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(
        self,
        port: int = 8998,
        gpu_id: int = 2,
        host: str = "0.0.0.0",
        cpu_offload: bool = False,
    ) -> Tuple[bool, str]:
        """Start the moshi.server. Returns (success, status_message)."""
        if self.is_running:
            url = self._get_url()
            return True, f"Server already running at {url}"

        if not is_personaplex_installed():
            return False, (
                "PersonaPlex (moshi) is not installed.\n"
                "Run: pip install personaplex/moshi/."
            )

        if not check_hf_token():
            return False, (
                "HF_TOKEN not set. PersonaPlex needs a HuggingFace token.\n"
                "Run: export HF_TOKEN=<your_token>"
            )

        self._port = port
        self._gpu_id = gpu_id
        self._host = host
        self._log_lines = []

        # Run without SSL (avoids mkcert needing sudo)
        # Microphone access works on localhost without HTTPS
        cmd = [
            sys.executable, "-m", "moshi.server",
            "--host", host,
            "--port", str(port),
            "--device", "cuda",
        ]
        if cpu_offload:
            cmd.append("--cpu-offload")

        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

        logger.info(f"Starting PersonaPlex server: {' '.join(cmd)}")
        logger.info(f"CUDA_VISIBLE_DEVICES={gpu_id}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                bufsize=1,
            )

            # Wait a moment and check it didn't crash instantly
            time.sleep(3)
            if self._process.poll() is not None:
                output = self._process.stdout.read() if self._process.stdout else ""
                self._process = None
                return False, f"Server exited immediately:\n{output[-1000:]}"

            # Read initial log lines to show download/loading progress
            initial_logs = self._read_available_lines(timeout=2)

            url = self._get_url()
            status = (
                f"Server starting on GPU {gpu_id} (port {port})...\n"
                f"First run downloads model weights (~14GB) from HuggingFace.\n"
                f"Subsequent starts are faster (weights cached).\n"
                f"Click 'Refresh Logs' to monitor progress.\n\n"
                f"URL: {url}"
            )
            if initial_logs:
                status += f"\n\n--- Server Output ---\n{initial_logs}"
            return True, status

        except Exception as e:
            self._process = None
            return False, f"Failed to start server: {e}"

    def stop(self) -> str:
        """Stop the server process."""
        if not self.is_running:
            return "Server is not running."

        logger.info("Stopping PersonaPlex server...")
        self._process.terminate()
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)

        self._process = None
        return "Server stopped."

    def get_status(self) -> str:
        """Get current server status with recent log output."""
        if not self.is_running:
            return "Not running."

        # Read any available output (non-blocking)
        status = f"Running on port {self._port}, GPU {self._gpu_id}"
        url = self._get_url()
        status += f"\nURL: {url}"

        return status

    def _read_available_lines(self, timeout: float = 0.5) -> str:
        """Read available stdout lines without blocking forever."""
        if self._process is None or self._process.stdout is None:
            return ""
        import fcntl
        try:
            fd = self._process.stdout.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    line = self._process.stdout.readline()
                    if not line:
                        time.sleep(0.1)
                        continue
                    self._log_lines.append(line.rstrip())
                    if len(self._log_lines) > 200:
                        self._log_lines = self._log_lines[-200:]
                except (IOError, OSError):
                    time.sleep(0.1)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl)
        except Exception:
            pass
        return "\n".join(self._log_lines[-30:])

    def get_recent_logs(self, max_lines: int = 30) -> str:
        """Read recent log output from the server process."""
        self._read_available_lines(timeout=1.0)
        return "\n".join(self._log_lines[-max_lines:])

    def _get_url(self) -> str:
        """Get the server URL (plain HTTP, no SSL)."""
        return f"http://localhost:{self._port}"

    def _cleanup(self):
        """Cleanup on exit."""
        if self.is_running:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass


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
