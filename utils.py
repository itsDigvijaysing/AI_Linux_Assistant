"""Utility functions for AI Voice Assistant."""

import logging
import yaml
import sys
from pathlib import Path
from typing import Any, Dict, Optional
import torch


class ConfigManager:
    """Manages configuration loading and access."""

    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize configuration manager.

        Args:
            config_path: Path to the configuration file
        """
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self.load_config()

    def load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            if not self.config_path.exists():
                raise FileNotFoundError(f"Config file not found: {self.config_path}")

            with open(self.config_path, "r") as f:
                self.config = yaml.safe_load(f)

            # Resolve auto device setting
            if self.config.get("tts", {}).get("device") == "auto":
                self.config["tts"]["device"] = (
                    "cuda" if torch.cuda.is_available() else "cpu"
                )

        except Exception as e:
            print(f"Error loading config: {e}")
            print("Using default configuration...")
            self._load_defaults()

    def _load_defaults(self) -> None:
        """Load default configuration if config file fails."""
        self.config = {
            "audio": {
                "sample_rate": 16000,
                "dtype": "int16",
                "channels": 1,
                "chunk_duration": 0.1,
                "min_audio_size": 0,
            },
            "whisper": {"model": "base.en", "fp16": False, "language": "en"},
            "ollama": {
                "model": "llama3.2",
                "base_url": None,
                "temperature": 0.7,
                "max_tokens": 100,
                "verbose": False,
            },
            "tts": {
                "device": "cuda" if torch.cuda.is_available() else "cpu",
                "model": "suno/bark-small",
                "voice_preset": "v2/en_speaker_1",
                "sample_rate": 24000,
                "silence_duration": 0.25,
            },
            "prompt": {
                "system_message": "You are an AI assistant. Keep responses under 20 words.",
                "ai_prefix": "Assistant:",
            },
            "logging": {
                "level": "INFO",
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "file": None,
            },
            "performance": {
                "max_queue_size": 100,
                "thread_timeout": 30,
                "enable_gc": True,
            },
            "ui": {
                "use_rich": True,
                "spinner": "earth",
                "show_transcription": True,
                "show_response": True,
                "show_timings": False,
            },
        }

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.

        Args:
            key_path: Dot-separated path to config value (e.g., 'audio.sample_rate')
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        keys = key_path.split(".")
        value = self.config

        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key_path: str, value: Any) -> None:
        """
        Set configuration value using dot notation.

        Args:
            key_path: Dot-separated path to config value
            value: Value to set
        """
        keys = key_path.split(".")
        config = self.config

        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]

        config[keys[-1]] = value

    def save_config(self, path: Optional[str] = None) -> None:
        """
        Save current configuration to file.

        Args:
            path: Optional path to save config, defaults to original path
        """
        save_path = Path(path) if path else self.config_path
        try:
            with open(save_path, "w") as f:
                yaml.dump(self.config, f, default_flow_style=False)
        except Exception as e:
            logging.error(f"Failed to save config: {e}")


def setup_logging(config: ConfigManager) -> logging.Logger:
    """
    Setup logging configuration.

    Args:
        config: Configuration manager instance

    Returns:
        Configured logger instance
    """
    log_level = config.get("logging.level", "INFO")
    log_format = config.get(
        "logging.format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    log_file = config.get("logging.file")

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            *(
                [logging.FileHandler(log_file)]
                if log_file
                else []
            ),
        ],
    )

    # Create logger for this application
    logger = logging.getLogger("voice_assistant")
    logger.setLevel(getattr(logging, log_level))

    return logger


def validate_audio_input(audio_np: Any, logger: logging.Logger) -> bool:
    """
    Validate audio input before processing.

    Args:
        audio_np: Audio numpy array
        logger: Logger instance

    Returns:
        True if valid, False otherwise
    """
    if audio_np is None:
        logger.error("Audio input is None")
        return False

    if not hasattr(audio_np, "size"):
        logger.error("Audio input is not a numpy array")
        return False

    if audio_np.size == 0:
        logger.warning("Audio input is empty")
        return False

    return True


def cleanup_resources(*resources) -> None:
    """
    Clean up resources gracefully.

    Args:
        *resources: Variable number of resources to clean up
    """
    for resource in resources:
        try:
            if hasattr(resource, "close"):
                resource.close()
            elif hasattr(resource, "cleanup"):
                resource.cleanup()
            elif hasattr(resource, "stop"):
                resource.stop()
        except Exception as e:
            logging.warning(f"Error cleaning up resource: {e}")
