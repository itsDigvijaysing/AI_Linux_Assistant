"""
AI Voice Assistant - Web Frontend (Gradio)

Provides a web interface with two pipelines:
1. Classic Pipeline: Whisper STT -> Ollama LLM -> Bark TTS
2. PersonaPlex Pipeline: NVIDIA PersonaPlex end-to-end speech-to-speech

Usage:
    python app_frontend.py
    # Opens at http://localhost:7860
"""

import os
import time
import tempfile
import logging
import gc
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import gradio as gr

from utils import ConfigManager, setup_logging

logger = logging.getLogger("voice_assistant.frontend")

# Load configuration
config = ConfigManager("config.yaml")


# ============================================================
# Classic Pipeline (Whisper + Ollama + Bark)
# ============================================================

class ClassicPipeline:
    """Classic ASR -> LLM -> TTS pipeline."""

    def __init__(self):
        self.stt = None
        self.tts = None
        self.chain = None
        self._initialized = False

    def initialize(self) -> str:
        """Initialize all models. Returns status message."""
        if self._initialized:
            return "Models already loaded."

        status_messages = []

        # Load Whisper
        try:
            import whisper
            whisper_model = config.get("whisper.model", "base.en")
            status_messages.append(f"Loading Whisper ({whisper_model})...")
            self.stt = whisper.load_model(whisper_model)
            status_messages.append(f"Whisper loaded.")
        except Exception as e:
            status_messages.append(f"Whisper failed: {e}")
            return "\n".join(status_messages)

        # Load TTS
        try:
            from tts_improved import TextToSpeechService
            tts_device = config.get("tts.device", "cpu")
            tts_model = config.get("tts.model", "suno/bark-small")
            status_messages.append(f"Loading TTS ({tts_model} on {tts_device})...")
            self.tts = TextToSpeechService(device=tts_device, model_name=tts_model)
            status_messages.append("TTS initialized (lazy loading).")
        except Exception as e:
            status_messages.append(f"TTS failed: {e}")

        # Setup LLM chain
        try:
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

            system_message = config.get("prompt.system_message", "You are an AI assistant.")
            ai_prefix = config.get("prompt.ai_prefix", "Assistant:")

            template = f"""{system_message}

Here is our conversation transcript:
{{history}}

And here is the user's follow-up: {{input}}

Please provide your response:
"""
            prompt = PromptTemplate(input_variables=["history", "input"], template=template)

            ollama_config = {
                "model": config.get("ollama.model", "llama3.2"),
                "temperature": config.get("ollama.temperature", 0.7),
            }
            max_tokens = config.get("ollama.max_tokens")
            if max_tokens:
                ollama_config["num_predict"] = max_tokens

            base_url = config.get("ollama.base_url")
            if base_url:
                ollama_config["base_url"] = base_url

            self.chain = ConversationChain(
                prompt=prompt,
                verbose=config.get("ollama.verbose", False),
                memory=ConversationBufferMemory(ai_prefix=ai_prefix),
                llm=OllamaLLM(**ollama_config),
            )
            status_messages.append(f"LLM loaded ({ollama_config['model']}).")
        except Exception as e:
            status_messages.append(f"LLM failed: {e}")
            return "\n".join(status_messages)

        self._initialized = True
        status_messages.append("All models ready!")
        return "\n".join(status_messages)

    def transcribe(self, audio_path: str) -> Optional[str]:
        """Transcribe audio file to text."""
        if self.stt is None:
            return None
        try:
            import whisper
            fp16 = config.get("whisper.fp16", False)
            language = config.get("whisper.language", "en")
            result = self.stt.transcribe(audio_path, fp16=fp16, language=language)
            return result["text"].strip()
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None

    def get_response(self, text: str) -> Optional[str]:
        """Get LLM response."""
        if self.chain is None or not text:
            return None
        try:
            result = self.chain.invoke({"input": text})
            response = result if isinstance(result, str) else result.get("response", str(result))
            ai_prefix = config.get("prompt.ai_prefix", "Assistant:")
            if response.startswith(ai_prefix):
                response = response[len(ai_prefix):].strip()
            return response
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return None

    def synthesize(self, text: str) -> Optional[Tuple[int, np.ndarray]]:
        """Synthesize speech from text."""
        if self.tts is None or not text:
            return None
        try:
            voice_preset = config.get("tts.voice_preset", "v2/en_speaker_1")
            silence_duration = config.get("tts.silence_duration", 0.25)
            return self.tts.long_form_synthesize(
                text,
                voice_preset=voice_preset,
                silence_duration=silence_duration,
            )
        except Exception as e:
            logger.error(f"TTS error: {e}")
            return None

    def process(self, audio) -> Tuple[str, str, Optional[Tuple[int, np.ndarray]]]:
        """
        Full pipeline: audio -> text -> response -> speech.

        Args:
            audio: Gradio audio input (sample_rate, numpy_array) or filepath

        Returns:
            Tuple of (transcription, response_text, (sample_rate, audio_array))
        """
        if audio is None:
            return "No audio provided.", "", None

        if not self._initialized:
            return "Models not loaded. Click 'Load Models' first.", "", None

        # Handle audio input (could be filepath or tuple)
        if isinstance(audio, str):
            audio_path = audio
        elif isinstance(audio, tuple):
            sr, audio_data = audio
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            import soundfile as sf
            sf.write(tmp.name, audio_data, sr)
            audio_path = tmp.name
        else:
            return "Invalid audio format.", "", None

        # Step 1: Transcribe
        transcription = self.transcribe(audio_path)
        if not transcription:
            return "Could not transcribe audio.", "", None

        # Step 2: Get LLM response
        response = self.get_response(transcription)
        if not response:
            return transcription, "Could not generate response.", None

        # Step 3: Synthesize speech
        tts_result = self.synthesize(response)

        gc.collect()

        if tts_result:
            sr, audio_array = tts_result
            return transcription, response, (sr, audio_array)
        else:
            return transcription, response, None

    def text_chat(self, text: str) -> str:
        """Text-only chat (no audio)."""
        if not self._initialized:
            return "Models not loaded. Click 'Load Models' first."
        response = self.get_response(text)
        return response if response else "Could not generate response."

    def reset_memory(self) -> str:
        """Reset conversation memory."""
        if self.chain and hasattr(self.chain, "memory"):
            self.chain.memory.clear()
            return "Conversation memory cleared."
        return "No active conversation to reset."


# ============================================================
# PersonaPlex Pipeline
# ============================================================

class PersonaPlexUI:
    """PersonaPlex pipeline UI handler."""

    def __init__(self):
        self._available = None

    def check_available(self) -> str:
        """Check if PersonaPlex is available."""
        try:
            from personaplex_pipeline import is_personaplex_installed, check_hf_token
            installed = is_personaplex_installed()
            has_token = check_hf_token()

            status = []
            if installed:
                status.append("PersonaPlex: Installed")
            else:
                status.append(
                    "PersonaPlex: NOT installed.\n"
                    "Run: git clone https://github.com/NVIDIA/personaplex.git && "
                    "pip install personaplex/moshi/."
                )
            if has_token:
                status.append("HF Token: Set")
            else:
                status.append(
                    "HF Token: NOT set.\n"
                    "Run: export HF_TOKEN=<your_huggingface_token>"
                )

            self._available = installed and has_token
            return "\n".join(status)

        except ImportError as e:
            self._available = False
            return f"PersonaPlex module not found: {e}"

    def process_audio(
        self,
        audio,
        voice_preset: str,
        text_prompt: str,
        gpu_id: int,
    ) -> Tuple[str, Optional[Tuple[int, np.ndarray]]]:
        """
        Process audio through PersonaPlex.

        Returns:
            Tuple of (status_message, (sample_rate, audio_array) or None)
        """
        if audio is None:
            return "No audio provided.", None

        try:
            from personaplex_pipeline import process_audio_with_personaplex, VOICE_PRESETS

            # Save input audio to temp file
            if isinstance(audio, str):
                audio_path = audio
            elif isinstance(audio, tuple):
                sr, audio_data = audio
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                import soundfile as sf
                sf.write(tmp.name, audio_data, sr)
                audio_path = tmp.name
            else:
                return "Invalid audio format.", None

            # Map display name to ID if needed
            voice_id = VOICE_PRESETS.get(voice_preset, voice_preset)

            status = f"Processing with PersonaPlex (voice: {voice_id}, GPU: {gpu_id})..."
            logger.info(status)

            output_wav, transcript = process_audio_with_personaplex(
                input_audio_path=audio_path,
                voice_preset=voice_id,
                text_prompt=text_prompt,
                gpu_id=int(gpu_id),
            )

            if output_wav and os.path.exists(output_wav):
                import soundfile as sf
                audio_data, sr = sf.read(output_wav)
                result_msg = f"PersonaPlex response generated."
                if transcript:
                    result_msg += f"\nTranscript: {transcript}"
                return result_msg, (sr, audio_data)
            else:
                return "PersonaPlex inference failed. Check logs for details.", None

        except Exception as e:
            logger.error(f"PersonaPlex error: {e}")
            return f"Error: {e}", None


# ============================================================
# Build Gradio Interface
# ============================================================

def build_interface() -> gr.Blocks:
    """Build the complete Gradio interface."""

    classic = ClassicPipeline()
    personaplex = PersonaPlexUI()

    with gr.Blocks(
        title="AI Voice Assistant",
        theme=gr.themes.Soft(),
    ) as demo:

        gr.Markdown(
            """
            # AI Voice Assistant
            Choose a pipeline below to interact with the voice assistant.
            """
        )

        with gr.Tabs() as tabs:
            # ========== Tab 1: Classic Pipeline ==========
            with gr.Tab("Classic Pipeline (Whisper + Ollama + Bark)", id="classic"):
                gr.Markdown(
                    """
                    ### Classic Voice Pipeline
                    **Flow:** Record Audio → Whisper (STT) → Ollama LLM → Bark (TTS)

                    This uses separate models for each stage of the pipeline.
                    """
                )

                with gr.Row():
                    classic_load_btn = gr.Button("Load Models", variant="primary")
                    classic_reset_btn = gr.Button("Reset Conversation")

                classic_status = gr.Textbox(
                    label="Status", lines=4, interactive=False,
                    value="Click 'Load Models' to initialize the pipeline."
                )

                gr.Markdown("---")
                gr.Markdown("#### Voice Chat")

                with gr.Row():
                    with gr.Column(scale=1):
                        classic_audio_input = gr.Audio(
                            sources=["microphone", "upload"],
                            type="filepath",
                            label="Record or Upload Audio",
                        )
                        classic_voice_btn = gr.Button(
                            "Process Voice", variant="primary"
                        )

                    with gr.Column(scale=1):
                        classic_transcription = gr.Textbox(
                            label="Your Speech (Transcription)", lines=2
                        )
                        classic_response_text = gr.Textbox(
                            label="Assistant Response", lines=3
                        )
                        classic_audio_output = gr.Audio(
                            label="Assistant Voice Response",
                            type="numpy",
                        )

                gr.Markdown("---")
                gr.Markdown("#### Text Chat")

                with gr.Row():
                    with gr.Column(scale=3):
                        classic_text_input = gr.Textbox(
                            label="Type your message",
                            placeholder="Type here and press Enter or click Send...",
                        )
                    with gr.Column(scale=1):
                        classic_text_btn = gr.Button("Send", variant="primary")

                classic_text_response = gr.Textbox(
                    label="Assistant Response (Text)", lines=3
                )

                # Classic Pipeline Events
                classic_load_btn.click(
                    fn=classic.initialize,
                    outputs=classic_status,
                )

                classic_reset_btn.click(
                    fn=classic.reset_memory,
                    outputs=classic_status,
                )

                classic_voice_btn.click(
                    fn=classic.process,
                    inputs=classic_audio_input,
                    outputs=[
                        classic_transcription,
                        classic_response_text,
                        classic_audio_output,
                    ],
                )

                classic_text_btn.click(
                    fn=classic.text_chat,
                    inputs=classic_text_input,
                    outputs=classic_text_response,
                )

                classic_text_input.submit(
                    fn=classic.text_chat,
                    inputs=classic_text_input,
                    outputs=classic_text_response,
                )

            # ========== Tab 2: PersonaPlex Pipeline ==========
            with gr.Tab("PersonaPlex (NVIDIA Speech-to-Speech)", id="personaplex"):
                gr.Markdown(
                    """
                    ### NVIDIA PersonaPlex Pipeline
                    **Model:** `nvidia/personaplex-7b-v1` (7B params, Moshi architecture)

                    **Flow:** Audio Input → PersonaPlex (end-to-end) → Audio Output

                    PersonaPlex is a real-time, full-duplex speech-to-speech model that
                    replaces the entire ASR + LLM + TTS pipeline with a single model.

                    **Features:**
                    - ~257ms response latency
                    - Persona control (voice + text prompts)
                    - 16 voice presets
                    - Full-duplex (can listen while speaking)
                    """
                )

                with gr.Row():
                    pp_check_btn = gr.Button(
                        "Check PersonaPlex Status", variant="primary"
                    )

                pp_status = gr.Textbox(
                    label="PersonaPlex Status", lines=3, interactive=False,
                    value="Click 'Check PersonaPlex Status' to verify installation."
                )

                gr.Markdown("---")

                with gr.Row():
                    with gr.Column(scale=1):
                        pp_voice_preset = gr.Dropdown(
                            label="Voice Preset",
                            choices=list(_get_voice_choices()),
                            value="Natural Female 2",
                        )
                        pp_text_prompt = gr.Textbox(
                            label="Persona Prompt",
                            lines=3,
                            value=config.get(
                                "personaplex.text_prompt",
                                "You are a helpful, friendly AI assistant."
                            ),
                        )
                        pp_prompt_presets = gr.Dropdown(
                            label="Prompt Presets",
                            choices=[
                                "General Assistant",
                                "Customer Service",
                                "Teacher",
                                "Storyteller",
                                "Custom",
                            ],
                            value="General Assistant",
                        )
                        pp_gpu_id = gr.Number(
                            label="GPU ID",
                            value=config.get("personaplex.gpu_id", 2),
                            precision=0,
                        )

                    with gr.Column(scale=1):
                        pp_audio_input = gr.Audio(
                            sources=["microphone", "upload"],
                            type="filepath",
                            label="Record or Upload Audio",
                        )
                        pp_process_btn = gr.Button(
                            "Process with PersonaPlex",
                            variant="primary",
                        )

                pp_result_text = gr.Textbox(
                    label="Result / Transcript", lines=3
                )
                pp_audio_output = gr.Audio(
                    label="PersonaPlex Response",
                    type="numpy",
                )

                # PersonaPlex Events
                pp_check_btn.click(
                    fn=personaplex.check_available,
                    outputs=pp_status,
                )

                pp_prompt_presets.change(
                    fn=_update_prompt_preset,
                    inputs=pp_prompt_presets,
                    outputs=pp_text_prompt,
                )

                pp_process_btn.click(
                    fn=personaplex.process_audio,
                    inputs=[
                        pp_audio_input,
                        pp_voice_preset,
                        pp_text_prompt,
                        pp_gpu_id,
                    ],
                    outputs=[pp_result_text, pp_audio_output],
                )

            # ========== Tab 3: Settings ==========
            with gr.Tab("Settings", id="settings"):
                gr.Markdown("### Current Configuration")
                gr.Markdown(
                    "Edit `config.yaml` to change settings. "
                    "Restart the app after making changes."
                )

                with gr.Row():
                    with gr.Column():
                        gr.Markdown("#### Whisper (STT)")
                        gr.Textbox(
                            value=config.get("whisper.model", "base.en"),
                            label="Model", interactive=False,
                        )
                        gr.Textbox(
                            value=config.get("whisper.language", "en"),
                            label="Language", interactive=False,
                        )

                    with gr.Column():
                        gr.Markdown("#### Ollama (LLM)")
                        gr.Textbox(
                            value=config.get("ollama.model", "llama3.2"),
                            label="Model", interactive=False,
                        )
                        gr.Textbox(
                            value=str(config.get("ollama.temperature", 0.7)),
                            label="Temperature", interactive=False,
                        )

                    with gr.Column():
                        gr.Markdown("#### Bark (TTS)")
                        gr.Textbox(
                            value=config.get("tts.model", "suno/bark-small"),
                            label="Model", interactive=False,
                        )
                        gr.Textbox(
                            value=config.get("tts.device", "cpu"),
                            label="Device", interactive=False,
                        )

                gr.Markdown("---")
                gr.Markdown("#### GPU Status")
                gpu_status = gr.Textbox(
                    label="GPU Info", lines=6, interactive=False,
                    value=_get_gpu_info(),
                )
                gr.Button("Refresh GPU Status").click(
                    fn=_get_gpu_info,
                    outputs=gpu_status,
                )

    return demo


# ============================================================
# Helper Functions
# ============================================================

def _get_voice_choices():
    """Get PersonaPlex voice preset choices."""
    from personaplex_pipeline import VOICE_PRESETS
    return VOICE_PRESETS.keys()


def _update_prompt_preset(preset_name: str) -> str:
    """Update text prompt based on preset selection."""
    from personaplex_pipeline import DEFAULT_TEXT_PROMPTS
    if preset_name in DEFAULT_TEXT_PROMPTS:
        return DEFAULT_TEXT_PROMPTS[preset_name]
    return ""


def _get_gpu_info() -> str:
    """Get GPU status information."""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            info = ["GPU Status:"]
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 5:
                    info.append(
                        f"  GPU {parts[0]}: {parts[1]} | "
                        f"Memory: {parts[2]}MB / {parts[3]}MB | "
                        f"Utilization: {parts[4]}%"
                    )
            return "\n".join(info)
        return "Could not query GPU status."
    except Exception as e:
        return f"GPU info unavailable: {e}"


# ============================================================
# Main Entry Point
# ============================================================

def main():
    """Launch the web frontend."""
    setup_logging(config)

    port = config.get("frontend.server_port", 7860)
    share = config.get("frontend.share", False)

    print("=" * 60)
    print("  AI Voice Assistant - Web Frontend")
    print("=" * 60)
    print(f"  URL: http://localhost:{port}")
    if share:
        print("  Public link will be generated...")
    print("=" * 60)

    demo = build_interface()
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        share=share,
    )


if __name__ == "__main__":
    main()
