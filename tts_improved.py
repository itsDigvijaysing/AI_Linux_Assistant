"""Improved Text-to-Speech Service with error handling."""

import warnings
import logging
from typing import Tuple, Optional
import numpy as np

# Suppress specific warnings
warnings.filterwarnings(
    "ignore",
    message="torch.nn.utils.weight_norm is deprecated",
)

logger = logging.getLogger("voice_assistant.tts")


class TextToSpeechService:
    """Text-to-Speech service using Bark model with robust error handling."""

    def __init__(
        self,
        device: str = "cpu",
        model_name: str = "suno/bark-small",
        sample_rate: int = 24000,
    ):
        """
        Initialize the TextToSpeechService.

        Args:
            device: Device to use ('cuda' or 'cpu')
            model_name: Bark model identifier
            sample_rate: Audio sample rate
        """
        self.device = device
        self.model_name = model_name
        self.sample_rate_config = sample_rate
        self.processor = None
        self.model = None
        self._initialized = False

        logger.info(f"TTS Service initializing with device: {device}")

    def _lazy_load(self) -> bool:
        """
        Lazy load the model on first use.

        Returns:
            True if successful, False otherwise
        """
        if self._initialized:
            return True

        try:
            import torch
            from transformers import AutoProcessor, BarkModel

            logger.info(f"Loading TTS model: {self.model_name} on {self.device}")

            self.processor = AutoProcessor.from_pretrained(self.model_name)
            # Use fp16 on CUDA for faster inference and lower memory
            if "cuda" in self.device:
                self.model = BarkModel.from_pretrained(
                    self.model_name, torch_dtype=torch.float16
                )
            else:
                self.model = BarkModel.from_pretrained(self.model_name)
            self.model.to(self.device)

            self._initialized = True
            logger.info("TTS model loaded successfully")
            return True

        except ImportError as e:
            logger.error(f"Missing required library: {e}")
            logger.error("Please install: pip install transformers torch")
            return False
        except Exception as e:
            logger.error(f"Failed to load TTS model: {e}")
            return False

    def synthesize(
        self, text: str, voice_preset: str = "v2/en_speaker_1"
    ) -> Optional[Tuple[int, np.ndarray]]:
        """
        Synthesize audio from text.

        Args:
            text: Input text to synthesize
            voice_preset: Voice preset to use

        Returns:
            Tuple of (sample_rate, audio_array) or None on failure
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for synthesis")
            return None

        if not self._lazy_load():
            logger.error("TTS model not available")
            return None

        try:
            import torch

            # Prepare inputs
            inputs = self.processor(text, voice_preset=voice_preset, return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Generate audio
            with torch.no_grad():
                audio_array = self.model.generate(**inputs, pad_token_id=10000)

            audio_array = audio_array.cpu().numpy().squeeze()
            sample_rate = self.model.generation_config.sample_rate

            logger.debug(f"Synthesized {len(audio_array)} audio samples")
            return sample_rate, audio_array

        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                logger.error("GPU out of memory. Try using CPU or smaller model.")
            else:
                logger.error(f"Runtime error during synthesis: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during synthesis: {e}")
            return None

    def long_form_synthesize(
        self, text: str, voice_preset: str = "v2/en_speaker_1", silence_duration: float = 0.25
    ) -> Optional[Tuple[int, np.ndarray]]:
        """
        Synthesize long-form text with sentence breaks.

        Args:
            text: Input text to synthesize
            voice_preset: Voice preset to use
            silence_duration: Duration of silence between sentences in seconds

        Returns:
            Tuple of (sample_rate, audio_array) or None on failure
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for long-form synthesis")
            return None

        if not self._lazy_load():
            logger.error("TTS model not available")
            return None

        try:
            import nltk

            # Ensure nltk data is available
            try:
                nltk.data.find("tokenizers/punkt")
            except LookupError:
                logger.info("Downloading NLTK punkt tokenizer...")
                nltk.download("punkt", quiet=True)
                nltk.download("punkt_tab", quiet=True)

            # Split into sentences
            sentences = nltk.sent_tokenize(text)
            logger.debug(f"Processing {len(sentences)} sentences")

            pieces = []
            sample_rate = None

            # Generate silence
            if self.model and hasattr(self.model, "generation_config"):
                sample_rate = self.model.generation_config.sample_rate
            else:
                sample_rate = self.sample_rate_config

            silence = np.zeros(int(silence_duration * sample_rate))

            # Synthesize each sentence
            for i, sent in enumerate(sentences):
                result = self.synthesize(sent.strip(), voice_preset)

                if result is None:
                    logger.warning(f"Failed to synthesize sentence {i+1}")
                    continue

                sent_sample_rate, audio_array = result
                pieces.append(audio_array)
                pieces.append(silence.copy())

            if not pieces:
                logger.error("No audio pieces were synthesized")
                return None

            # Concatenate all pieces
            final_audio = np.concatenate(pieces)
            logger.debug(f"Total audio length: {len(final_audio)} samples")

            return sample_rate, final_audio

        except ImportError:
            logger.error("NLTK not installed. Install with: pip install nltk")
            return None
        except Exception as e:
            logger.error(f"Error in long-form synthesis: {e}")
            return None

    def cleanup(self) -> None:
        """Clean up resources."""
        try:
            if self.model is not None:
                del self.model
            if self.processor is not None:
                del self.processor

            self._initialized = False
            logger.info("TTS resources cleaned up")

        except Exception as e:
            logger.warning(f"Error during TTS cleanup: {e}")
