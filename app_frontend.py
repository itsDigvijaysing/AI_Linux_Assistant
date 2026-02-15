"""
AI Voice Assistant - Web Frontend (Gradio)

Provides a web interface with two pipelines:
1. Classic Pipeline: Whisper STT -> Ollama LLM -> Bark TTS (on GPU)
2. PersonaPlex Pipeline: NVIDIA PersonaPlex real-time server (full-duplex)

Usage:
    CUDA_VISIBLE_DEVICES=2,3 python app_frontend.py
    # Opens at http://localhost:7860
"""

import os
import time
import tempfile
import logging
import gc
import atexit
from typing import Optional, Tuple

import numpy as np
import torch

# --- Monkey-patch gradio_client bug (TypeError: argument of type 'bool' is not iterable) ---
import gradio_client.utils as _gc_utils

_orig_get_type = _gc_utils.get_type

def _patched_get_type(schema):
    if isinstance(schema, bool):
        return "Any"
    return _orig_get_type(schema)

_gc_utils.get_type = _patched_get_type

_orig_json_schema = _gc_utils._json_schema_to_python_type

def _patched_json_schema(schema, defs=None):
    if isinstance(schema, bool):
        return "Any"
    return _orig_json_schema(schema, defs)

_gc_utils._json_schema_to_python_type = _patched_json_schema
# --- End monkey-patch ---

import gradio as gr

from utils import ConfigManager, setup_logging

logger = logging.getLogger("voice_assistant.frontend")

# Load configuration
config = ConfigManager("config.yaml")


# ============================================================
# Classic Pipeline (Whisper + Ollama + Bark) - GPU enabled
# ============================================================

class ClassicPipeline:
    """Classic ASR -> LLM -> TTS pipeline with GPU support."""

    def __init__(self):
        self.stt = None
        self.tts = None
        self.chain = None
        self._initialized = False

    def initialize(self) -> str:
        """Initialize all models on GPU. Returns status message."""
        if self._initialized:
            return "Models already loaded."

        status_messages = []
        device = "cuda" if torch.cuda.is_available() else "cpu"

        # Load Whisper on GPU
        try:
            import whisper
            whisper_model = config.get("whisper.model", "base.en")
            status_messages.append(f"Loading Whisper ({whisper_model}) on {device}...")
            self.stt = whisper.load_model(whisper_model, device=device)
            status_messages.append(f"Whisper loaded on {device}.")
        except Exception as e:
            status_messages.append(f"Whisper failed: {e}")
            return "\n".join(status_messages)

        # Load TTS on GPU
        try:
            from tts_improved import TextToSpeechService
            tts_device = config.get("tts.device", "cpu")
            tts_model = config.get("tts.model", "suno/bark-small")
            status_messages.append(f"Loading TTS ({tts_model} on {tts_device})...")
            self.tts = TextToSpeechService(device=tts_device, model_name=tts_model)
            status_messages.append(f"TTS initialized on {tts_device} (lazy loading).")
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
            fp16 = config.get("whisper.fp16", True)
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
        """Full pipeline: audio -> text -> response -> speech."""
        if audio is None:
            return "No audio provided.", "", None

        if not self._initialized:
            return "Models not loaded. Click 'Load Models' first.", "", None

        # Handle audio input
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

        transcription = self.transcribe(audio_path)
        if not transcription:
            return "Could not transcribe audio.", "", None

        response = self.get_response(transcription)
        if not response:
            return transcription, "Could not generate response.", None

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
# PersonaPlex Real-Time Server UI
# ============================================================

class PersonaPlexUI:
    """PersonaPlex real-time server management."""

    def __init__(self):
        self._server_manager = None

    def _get_manager(self):
        if self._server_manager is None:
            from personaplex_pipeline import PersonaPlexServerManager
            self._server_manager = PersonaPlexServerManager.get_instance()
        return self._server_manager

    def check_status(self) -> str:
        """Check PersonaPlex installation and server status."""
        try:
            from personaplex_pipeline import is_personaplex_installed, check_hf_token

            lines = []
            installed = is_personaplex_installed()
            has_token = check_hf_token()

            lines.append(f"PersonaPlex: {'Installed' if installed else 'NOT installed'}")
            lines.append(f"HF Token: {'Set' if has_token else 'NOT set (export HF_TOKEN=<token>)'}")

            mgr = self._get_manager()
            if mgr.is_running:
                lines.append(f"Server: Running on port {mgr._port}, GPU {mgr._gpu_id}")
            else:
                lines.append("Server: Not running")

            if not installed:
                lines.append("\nInstall: pip install personaplex/moshi/.")
            if not has_token:
                lines.append("Set token: export HF_TOKEN=<your_huggingface_token>")

            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    def start_server(self, gpu_id, cpu_offload) -> Tuple[str, str]:
        """Start PersonaPlex server. Returns (status, url_html)."""
        mgr = self._get_manager()
        port = config.get("personaplex.server_port", 8998)
        host = config.get("personaplex.server_host", "0.0.0.0")

        success, msg = mgr.start(
            port=int(port),
            gpu_id=int(gpu_id),
            host=host,
            cpu_offload=bool(cpu_offload),
        )

        if success:
            url = mgr._get_url()
            url_html = (
                f'<div style="padding: 20px; text-align: center;">'
                f'<h3>PersonaPlex Server Starting...</h3>'
                f'<p>Loading 7B model on GPU {int(gpu_id)}. '
                f'First run downloads ~14GB of weights (takes a few minutes).<br>'
                f'Click <b>"Refresh Logs"</b> to monitor download/loading progress.</p>'
                f'<p><a href="{url}" target="_blank" '
                f'style="display: inline-block; padding: 12px 24px; '
                f'background-color: #2563eb; color: white; text-decoration: none; '
                f'border-radius: 8px; font-size: 16px; font-weight: bold;">'
                f'Open PersonaPlex Conversation UI</a></p>'
                f'<p style="color: #666; font-size: 12px;">'
                f'URL: {url} (open after logs show "Access the Web UI")</p>'
                f'</div>'
            )
            return msg, url_html
        else:
            return msg, '<p style="color: red;">Server failed to start. Check status above.</p>'

    def stop_server(self) -> Tuple[str, str]:
        """Stop PersonaPlex server."""
        mgr = self._get_manager()
        msg = mgr.stop()
        return msg, '<p>Server stopped. Click "Start Server" to restart.</p>'

    def get_logs(self) -> str:
        """Get server logs."""
        mgr = self._get_manager()
        if not mgr.is_running:
            return "Server is not running."
        logs = mgr.get_recent_logs(30)
        status = mgr.get_status()
        return f"{status}\n\n--- Recent Logs ---\n{logs}" if logs else status

    def process_offline(
        self,
        audio,
        voice_preset: str,
        text_prompt: str,
        gpu_id: int,
    ) -> Tuple[str, Optional[Tuple[int, np.ndarray]]]:
        """Process audio through PersonaPlex offline mode (single file)."""
        if audio is None:
            return "No audio provided.", None

        try:
            from personaplex_pipeline import process_audio_with_personaplex, VOICE_PRESETS

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

            voice_id = VOICE_PRESETS.get(voice_preset, voice_preset)

            output_wav, transcript = process_audio_with_personaplex(
                input_audio_path=audio_path,
                voice_preset=voice_id,
                text_prompt=text_prompt,
                gpu_id=int(gpu_id),
            )

            if output_wav and os.path.exists(output_wav):
                import soundfile as sf
                audio_data, sr = sf.read(output_wav)
                result_msg = "PersonaPlex response generated."
                if transcript:
                    result_msg += f"\nTranscript: {transcript}"
                return result_msg, (sr, audio_data)
            else:
                return "PersonaPlex inference failed. Check logs.", None

        except Exception as e:
            logger.error(f"PersonaPlex offline error: {e}")
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
                    **Flow:** Record Audio -> Whisper (STT on GPU) -> Ollama LLM -> Bark (TTS on GPU)
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

            # ========== Tab 2: PersonaPlex (Real-Time Server) ==========
            with gr.Tab("PersonaPlex (Real-Time Conversation)", id="personaplex"):
                gr.Markdown(
                    """
                    ### NVIDIA PersonaPlex - Real-Time Full-Duplex Conversation
                    **Model:** `nvidia/personaplex-7b-v1` (7B params, Moshi architecture)

                    PersonaPlex runs its own server with a built-in web client for
                    **real-time, bidirectional voice conversation**. Your mic stays on
                    continuously and you can talk naturally - no button pressing needed.

                    **How to use:**
                    1. Configure GPU and settings below
                    2. Click "Start Server" (model loads in ~60s on first run)
                    3. Click the link to open the conversation UI in a new tab
                    4. Allow microphone access and start talking!
                    """
                )

                with gr.Row():
                    pp_status = gr.Textbox(
                        label="Status", lines=4, interactive=False,
                        value="Click 'Check Status' to verify PersonaPlex installation."
                    )

                with gr.Row():
                    pp_check_btn = gr.Button("Check Status")
                    pp_logs_btn = gr.Button("Refresh Logs")

                gr.Markdown("---")
                gr.Markdown("#### Server Controls")

                with gr.Row():
                    with gr.Column(scale=1):
                        pp_gpu_id = gr.Dropdown(
                            label="GPU ID",
                            choices=["0", "1", "2", "3"],
                            value=str(config.get("personaplex.gpu_id", 2)),
                        )
                        pp_cpu_offload = gr.Checkbox(
                            label="CPU Offload (for low GPU memory)",
                            value=config.get("personaplex.cpu_offload", False),
                        )
                        with gr.Row():
                            pp_start_btn = gr.Button("Start Server", variant="primary")
                            pp_stop_btn = gr.Button("Stop Server", variant="stop")

                    with gr.Column(scale=2):
                        pp_client_html = gr.HTML(
                            value=(
                                '<div style="padding: 40px; text-align: center; '
                                'border: 2px dashed #ccc; border-radius: 12px; margin: 10px;">'
                                '<h3>PersonaPlex Server Not Running</h3>'
                                '<p>Configure settings and click "Start Server" to begin.</p>'
                                '<p style="color: #666;">The server will load the 7B model onto '
                                'your GPU and start a real-time conversation interface.</p>'
                                '</div>'
                            ),
                        )

                gr.Markdown("---")

                # Offline processing as a fallback
                with gr.Accordion("Offline Processing (Single Audio File)", open=False):
                    gr.Markdown(
                        "Process a single audio file through PersonaPlex (non-realtime). "
                        "Use this for testing or when real-time is not needed."
                    )
                    with gr.Row():
                        with gr.Column():
                            pp_voice_preset = gr.Dropdown(
                                label="Voice Preset",
                                choices=list(_get_voice_choices()),
                                value="Natural Female 2",
                            )
                            pp_text_prompt = gr.Textbox(
                                label="Persona Prompt", lines=2,
                                value=config.get(
                                    "personaplex.text_prompt",
                                    "You are a helpful, friendly AI assistant."
                                ),
                            )
                        with gr.Column():
                            pp_audio_input = gr.Audio(
                                sources=["microphone", "upload"],
                                type="filepath",
                                label="Input Audio",
                            )
                            pp_offline_btn = gr.Button("Process Offline")

                    pp_result_text = gr.Textbox(label="Result", lines=2)
                    pp_audio_output = gr.Audio(label="Response Audio", type="numpy")

                # PersonaPlex Events
                pp_check_btn.click(
                    fn=personaplex.check_status,
                    outputs=pp_status,
                )
                pp_logs_btn.click(
                    fn=personaplex.get_logs,
                    outputs=pp_status,
                )
                pp_start_btn.click(
                    fn=personaplex.start_server,
                    inputs=[pp_gpu_id, pp_cpu_offload],
                    outputs=[pp_status, pp_client_html],
                )
                pp_stop_btn.click(
                    fn=personaplex.stop_server,
                    outputs=[pp_status, pp_client_html],
                )
                pp_offline_btn.click(
                    fn=personaplex.process_offline,
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
                            value=f"fp16: {config.get('whisper.fp16', True)}",
                            label="Precision", interactive=False,
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
                            value=config.get("tts.device", "cuda"),
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
    print(f"  GPU available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  CUDA devices: {os.environ.get('CUDA_VISIBLE_DEVICES', 'all')}")
    print("=" * 60)

    demo = build_interface()
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        share=share,
    )


if __name__ == "__main__":
    main()
