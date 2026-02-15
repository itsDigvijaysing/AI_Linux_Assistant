"""
Improved AI Voice Assistant with robust error handling and configuration management.

This module provides a voice-based AI assistant that:
- Records audio from microphone
- Transcribes speech using Whisper
- Generates responses using Ollama LLM
- Converts responses to speech using Bark TTS
"""

import time
import signal
import sys
import threading
import gc
import logging
from queue import Queue, Empty
from typing import Optional, Tuple
import numpy as np
import torch
import whisper
import sounddevice as sd
from rich.console import Console
try:
    from langchain_classic.memory import ConversationBufferMemory
    from langchain_classic.chains import ConversationChain
except ImportError:
    from langchain.memory import ConversationBufferMemory
    from langchain.chains import ConversationChain
from langchain_core.prompts import PromptTemplate
try:
    from langchain_ollama import OllamaLLM
except ImportError:
    from langchain_community.llms import Ollama as OllamaLLM

from utils import ConfigManager, setup_logging, validate_audio_input, cleanup_resources
from tts_improved import TextToSpeechService

# Global variables for graceful shutdown
shutdown_event = threading.Event()
console = Console()


class VoiceAssistant:
    """Main voice assistant class with improved error handling."""

    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize the voice assistant.

        Args:
            config_path: Path to configuration file
        """
        self.config = ConfigManager(config_path)
        self.logger = setup_logging(self.config)
        self.logger.info("Initializing Voice Assistant...")

        # Initialize components
        self.stt = None
        self.tts = None
        self.chain = None
        self.recording_thread = None

        # Load models
        self._initialize_models()

    def _initialize_models(self) -> None:
        """Initialize STT, TTS, and LLM models with error handling."""
        try:
            # Load Whisper STT
            whisper_model = self.config.get("whisper.model", "base.en")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.logger.info(f"Loading Whisper model: {whisper_model} on {device}")
            self.stt = whisper.load_model(whisper_model, device=device)
            self.logger.info(f"Whisper model loaded on {device}")

        except Exception as e:
            self.logger.error(f"Failed to load Whisper model: {e}")
            console.print("[red]Failed to load speech recognition model. Exiting.[/red]")
            sys.exit(1)

        try:
            # Initialize TTS
            tts_device = self.config.get("tts.device", "cpu")
            tts_model = self.config.get("tts.model", "suno/bark-small")
            self.logger.info(f"Initializing TTS on device: {tts_device}")
            self.tts = TextToSpeechService(
                device=tts_device,
                model_name=tts_model,
            )
            self.logger.info("TTS service initialized")

        except Exception as e:
            self.logger.error(f"Failed to initialize TTS: {e}")
            console.print("[yellow]TTS initialization failed. Continuing without TTS.[/yellow]")

        try:
            # Initialize LLM chain
            self._setup_llm_chain()

        except Exception as e:
            self.logger.error(f"Failed to initialize LLM: {e}")
            console.print("[red]Failed to initialize language model. Exiting.[/red]")
            sys.exit(1)

    def _setup_llm_chain(self) -> None:
        """Setup the LLM conversation chain."""
        system_message = self.config.get("prompt.system_message")
        ai_prefix = self.config.get("prompt.ai_prefix", "Assistant:")

        template = f"""
{system_message}

Here is our conversation transcript:
{{history}}

And here is the user's follow-up: {{input}}

Please provide your response:
"""

        prompt = PromptTemplate(input_variables=["history", "input"], template=template)

        # Initialize Ollama
        ollama_config = {
            "model": self.config.get("ollama.model", "llama3.2"),
            "temperature": self.config.get("ollama.temperature", 0.7),
        }

        base_url = self.config.get("ollama.base_url")
        if base_url:
            ollama_config["base_url"] = base_url

        max_tokens = self.config.get("ollama.max_tokens")
        if max_tokens:
            ollama_config["num_predict"] = max_tokens

        self.logger.info(f"Initializing Ollama with model: {ollama_config['model']}")

        self.chain = ConversationChain(
            prompt=prompt,
            verbose=self.config.get("ollama.verbose", False),
            memory=ConversationBufferMemory(ai_prefix=ai_prefix),
            llm=OllamaLLM(**ollama_config),
        )

        self.logger.info("LLM chain initialized successfully")

    def record_audio(
        self, stop_event: threading.Event, data_queue: Queue
    ) -> None:
        """
        Record audio from microphone.

        Args:
            stop_event: Event to signal recording stop
            data_queue: Queue to store audio data
        """
        def callback(indata, frames, time_info, status):
            if status:
                self.logger.warning(f"Audio callback status: {status}")
            if not stop_event.is_set():
                data_queue.put(bytes(indata))

        try:
            sample_rate = self.config.get("audio.sample_rate", 16000)
            dtype = self.config.get("audio.dtype", "int16")
            channels = self.config.get("audio.channels", 1)

            with sd.RawInputStream(
                samplerate=sample_rate,
                dtype=dtype,
                channels=channels,
                callback=callback,
            ):
                while not stop_event.is_set() and not shutdown_event.is_set():
                    time.sleep(0.1)

        except Exception as e:
            self.logger.error(f"Error in audio recording: {e}")
            console.print(f"[red]Recording error: {e}[/red]")

    def transcribe(self, audio_np: np.ndarray) -> Optional[str]:
        """
        Transcribe audio to text.

        Args:
            audio_np: Audio data as numpy array

        Returns:
            Transcribed text or None on failure
        """
        if not validate_audio_input(audio_np, self.logger):
            return None

        try:
            fp16 = self.config.get("whisper.fp16", False)
            language = self.config.get("whisper.language", "en")

            self.logger.debug(f"Transcribing {len(audio_np)} audio samples")
            result = self.stt.transcribe(audio_np, fp16=fp16, language=language)
            text = result["text"].strip()

            self.logger.info(f"Transcribed: {text}")
            return text

        except Exception as e:
            self.logger.error(f"Transcription error: {e}")
            console.print(f"[red]Transcription failed: {e}[/red]")
            return None

    def get_llm_response(self, text: str) -> Optional[str]:
        """
        Generate LLM response.

        Args:
            text: Input text

        Returns:
            Generated response or None on failure
        """
        if not text or not text.strip():
            self.logger.warning("Empty input text for LLM")
            return None

        try:
            self.logger.debug(f"Generating response for: {text}")
            result = self.chain.invoke({"input": text})
            response = result if isinstance(result, str) else result.get("response", str(result))

            # Clean up response prefix if present
            ai_prefix = self.config.get("prompt.ai_prefix", "Assistant:")
            if response.startswith(ai_prefix):
                response = response[len(ai_prefix):].strip()

            self.logger.info(f"LLM response: {response}")
            return response

        except Exception as e:
            self.logger.error(f"LLM error: {e}")
            console.print(f"[red]Failed to generate response: {e}[/red]")
            return None

    def play_audio(
        self, sample_rate: int, audio_array: np.ndarray
    ) -> None:
        """
        Play audio through speakers.

        Args:
            sample_rate: Audio sample rate
            audio_array: Audio data
        """
        try:
            self.logger.debug(f"Playing audio: {len(audio_array)} samples at {sample_rate}Hz")
            sd.play(audio_array, sample_rate)
            sd.wait()

        except Exception as e:
            self.logger.error(f"Audio playback error: {e}")
            console.print(f"[red]Playback failed: {e}[/red]")

    def process_voice_input(self) -> bool:
        """
        Process one voice input cycle.

        Returns:
            True if successful, False otherwise
        """
        try:
            console.input(
                "[cyan]Press Enter to start recording...[/cyan]"
            )

            # Setup recording
            data_queue: Queue = Queue(
                maxsize=self.config.get("performance.max_queue_size", 100)
            )
            stop_event = threading.Event()

            self.recording_thread = threading.Thread(
                target=self.record_audio,
                args=(stop_event, data_queue),
                daemon=True,
            )
            self.recording_thread.start()
            console.print("[green]Recording... Press Enter to stop.[/green]")

            # Wait for user to stop recording
            input()
            stop_event.set()

            # Wait for recording thread with timeout
            timeout = self.config.get("performance.thread_timeout", 30)
            self.recording_thread.join(timeout=timeout)

            if self.recording_thread.is_alive():
                self.logger.warning("Recording thread did not stop in time")

            # Process recorded audio
            audio_data = b"".join(list(data_queue.queue))

            if not audio_data:
                console.print("[yellow]No audio recorded.[/yellow]")
                return False

            audio_np = (
                np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            )

            min_size = self.config.get("audio.min_audio_size", 0)
            if audio_np.size <= min_size:
                console.print(
                    "[yellow]Audio too short. Please speak for longer.[/yellow]"
                )
                return False

            # Transcribe
            with console.status("Transcribing...", spinner=self.config.get("ui.spinner", "earth")):
                text = self.transcribe(audio_np)

            if not text:
                console.print("[yellow]Could not transcribe audio.[/yellow]")
                return False

            if self.config.get("ui.show_transcription", True):
                console.print(f"[yellow]You: {text}[/yellow]")

            # Generate response
            with console.status(
                "Generating response...", spinner=self.config.get("ui.spinner", "earth")
            ):
                response = self.get_llm_response(text)

            if not response:
                console.print("[yellow]Could not generate response.[/yellow]")
                return False

            if self.config.get("ui.show_response", True):
                console.print(f"[cyan]Assistant: {response}[/cyan]")

            # Text-to-speech
            if self.tts:
                try:
                    with console.status(
                        "Synthesizing speech...",
                        spinner=self.config.get("ui.spinner", "earth"),
                    ):
                        voice_preset = self.config.get("tts.voice_preset", "v2/en_speaker_1")
                        silence_duration = self.config.get("tts.silence_duration", 0.25)

                        result = self.tts.long_form_synthesize(
                            response,
                            voice_preset=voice_preset,
                            silence_duration=silence_duration,
                        )

                    if result:
                        sample_rate, audio_array = result
                        self.play_audio(sample_rate, audio_array)
                    else:
                        console.print("[yellow]TTS synthesis failed.[/yellow]")

                except Exception as e:
                    self.logger.error(f"TTS error: {e}")
                    console.print(f"[yellow]TTS error: {e}[/yellow]")

            # Cleanup
            if self.config.get("performance.enable_gc", True):
                gc.collect()

            return True

        except KeyboardInterrupt:
            raise
        except Exception as e:
            self.logger.error(f"Error processing voice input: {e}")
            console.print(f"[red]Error: {e}[/red]")
            return False

    def run(self) -> None:
        """Run the main voice assistant loop."""
        console.print("[cyan]Assistant started! Press Ctrl+C to exit.[/cyan]")
        self.logger.info("Voice Assistant started")

        try:
            while not shutdown_event.is_set():
                self.process_voice_input()

        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down...[/yellow]")
            self.logger.info("Shutdown requested by user")

        finally:
            self.cleanup()

    def cleanup(self) -> None:
        """Clean up resources."""
        console.print("[blue]Cleaning up resources...[/blue]")
        self.logger.info("Cleaning up resources")

        shutdown_event.set()

        try:
            # Stop any audio playback
            sd.stop()

            # Cleanup TTS
            if self.tts:
                cleanup_resources(self.tts)

            # Force garbage collection
            gc.collect()

            console.print("[blue]Session ended.[/blue]")
            self.logger.info("Cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    console.print("\n[yellow]Received shutdown signal[/yellow]")
    shutdown_event.set()


def main():
    """Main entry point."""
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        assistant = VoiceAssistant()
        assistant.run()

    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/red]")
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
