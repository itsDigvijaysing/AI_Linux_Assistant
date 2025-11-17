# 🎙️ AI Voice Assistant with Ollama Models

## Overview

This is an **improved, production-ready** AI Voice Assistant that leverages local models for complete privacy. The assistant uses:
- **Whisper** for speech-to-text transcription (local, no API costs)
- **Ollama** for intelligent responses (local, no API costs)
- **Bark TTS** for natural voice synthesis (local, no API costs)

The assistant is designed for seamless voice interaction with **robust error handling**, **configuration management**, and **graceful shutdown**.

## ✨ Features

- **Speech Recognition**: Utilizes Whisper for accurate speech-to-text transcription
- **Conversational AI**: Employs Ollama models (Llama 3.2+) for intelligent, context-aware responses
- **Text-to-Speech**: High-quality voice synthesis using Bark TTS
- **Conversation Memory**: Maintains context across the conversation session
- **Rich Console Interface**: Beautiful feedback and status updates
- **Configuration System**: Easy customization via YAML config file
- **Error Handling**: Comprehensive error handling prevents crashes
- **Resource Management**: Efficient memory usage with cleanup
- **Logging**: Structured logging for debugging and monitoring
- **100% Local**: No API keys needed, completely private

## 📦 Installation

### Prerequisites

- **Python 3.11+** (Python 3.12 not supported due to dependency constraints)
- **Microphone** - Working audio input device
- **Ollama** - Install from [ollama.ai](https://ollama.ai)
- **FFmpeg** - For audio processing

### Step 1: System Dependencies

#### Linux (Ubuntu/Debian)
```bash
sudo apt-get update
sudo apt-get install python3.11 python3-pip portaudio19-dev ffmpeg
```

#### macOS
```bash
brew install python@3.11 portaudio ffmpeg
```

#### Windows
- Install Python 3.11 from [python.org](https://python.org)
- Install FFmpeg from [ffmpeg.org](https://ffmpeg.org)
- PyAudio may require additional setup

### Step 2: Clone Repository
```bash
git clone <repository-url>
cd AI_Voice_Assistant
```

### Step 3: Create Virtual Environment (Recommended)
```bash
# Using Python 3.11
python3.11 -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/Mac
# OR
venv\Scripts\activate  # Windows
```

### Step 4: Install Dependencies

#### Option A: Using pip (Recommended)
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

#### Option B: Using Poetry
```bash
poetry install
```

### Step 5: Install Ollama and Pull Models
```bash
# Install Ollama from https://ollama.ai

# Pull the default model
ollama pull llama3.2

# Optional: Pull other models
ollama pull llama3.1
ollama pull mistral
```

### Step 6: Download NLTK Data
```bash
python punkt_downloader.py
```

### Step 7: Test Microphone
```bash
# Test if your microphone is detected
python -c "import sounddevice as sd; print(sd.query_devices())"
```

## 🚀 Usage

### Quick Start

```bash
# Make sure Ollama is running
ollama serve  # In a separate terminal

# Run the voice assistant
python main.py
```

### Interaction Flow

1. **Start Recording**: Press `Enter` to begin recording your voice
2. **Speak**: Ask your question or make your request
3. **Stop Recording**: Press `Enter` again to stop
4. **Processing**: The assistant will:
   - Transcribe your speech
   - Generate an AI response
   - Speak the response back to you
5. **Repeat**: Continue the conversation or press `Ctrl+C` to exit

### Example Conversation

```
Assistant started! Press Ctrl+C to exit.
Press Enter to start recording, then press Enter again to stop.
[Recording...]
[Press Enter when done]

You: What is the capital of France?
Assistant: The capital of France is Paris.
[Plays audio response]

Press Enter to start recording, then press Enter again to stop.
```

### Configuration

Edit `config.yaml` to customize the assistant:

```yaml
# Change Whisper model (tiny.en, base.en, small.en, medium.en, large)
whisper:
  model: "base.en"

# Change Ollama model
ollama:
  model: "llama3.2"  # or llama3.1, mistral, etc.

# Adjust response length
prompt:
  system_message: "Keep responses under 20 words."

# Change TTS voice
tts:
  voice_preset: "v2/en_speaker_1"  # Different voices available

# Enable logging to file
logging:
  level: "INFO"
  file: "assistant.log"
```

## 🔧 How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Voice Assistant Flow                      │
└─────────────────────────────────────────────────────────────┘

1. User Input (Microphone)
          ↓
2. Audio Recording (sounddevice)
          ↓
3. Speech-to-Text (Whisper - Local)
          ↓
4. LLM Processing (Ollama - Local)
          ↓  
5. Conversation Memory (LangChain)
          ↓
6. Text-to-Speech (Bark TTS - Local)
          ↓
7. Audio Playback (sounddevice)
```

### Component Details

#### 1. Audio Recording
- **Technology**: `sounddevice` library with threaded recording
- **Format**: 16-bit PCM, 16kHz, mono
- **Buffer Management**: Queue-based audio chunk collection
- **Safety**: Timeout protection and graceful shutdown

#### 2. Speech Transcription (Whisper)
- **Model**: OpenAI Whisper (base.en by default)
- **Processing**: Local transcription, no API calls
- **Features**: Multi-language support, high accuracy
- **Optimizations**: Lazy loading, FP16 support for GPU

#### 3. LLM Response (Ollama)
- **Model**: Llama 3.2 (configurable)
- **Context**: Full conversation history maintained
- **Prompt Engineering**: System prompts for concise responses
- **Memory**: LangChain ConversationBufferMemory

#### 4. Text-to-Speech (Bark)
- **Model**: Suno Bark (small variant)
- **Quality**: Natural-sounding speech synthesis
- **Features**: Multiple voice presets, sentence segmentation
- **Performance**: Lazy loading, resource cleanup

#### 5. Error Handling & Logging
- **Comprehensive**: Try-catch blocks on all critical paths
- **Graceful**: Degraded operation on non-critical failures
- **Informative**: Structured logging with configurable levels
- **User-Friendly**: Rich console feedback

## 📊 Performance & Requirements

### System Requirements

**Minimum:**
- CPU: 4 cores, 2.0 GHz
- RAM: 8 GB
- Storage: 5 GB (for models)
- Microphone: Any USB or built-in mic

**Recommended:**
- CPU: 6+ cores, 3.0 GHz or GPU (NVIDIA with CUDA)
- RAM: 16 GB
- Storage: 10 GB SSD
- Microphone: High-quality USB mic

### Performance Metrics

| Component | Processing Time | Notes |
|-----------|----------------|-------|
| Recording | Real-time | User-controlled |
| Transcription | 1-3 seconds | Depends on audio length |
| LLM Response | 2-5 seconds | Depends on model size |
| TTS Synthesis | 2-4 seconds | Depends on text length |
| **Total Latency** | **5-12 seconds** | End-to-end response time |

### Model Sizes

| Model | Size | Quality | Speed |
|-------|------|---------|-------|
| Whisper tiny.en | 75 MB | Good | Very Fast |
| Whisper base.en | 142 MB | Better | Fast |
| Whisper small.en | 466 MB | Great | Medium |
| Bark small | 1.8 GB | Excellent | Medium |
| Llama 3.2 | 2 GB | Excellent | Fast |

## 🛠️ Troubleshooting

### Common Issues

#### 1. Microphone Not Working
```bash
# List audio devices
python -c "import sounddevice as sd; print(sd.query_devices())"

# Test recording
python -c "import sounddevice as sd; import numpy as np; print(sd.rec(16000, samplerate=16000, channels=1))"
```

**Solutions:**
- Check microphone permissions in system settings
- Try a different USB port
- Update audio drivers
- Test with another application first

#### 2. Ollama Connection Error
```
Error: Failed to initialize LLM
```

**Solutions:**
```bash
# Check if Ollama is running
ollama list

# Start Ollama server
ollama serve

# Pull the model
ollama pull llama3.2

# Test Ollama
ollama run llama3.2 "Hello"
```

#### 3. Out of Memory (GPU)
```
RuntimeError: CUDA out of memory
```

**Solutions:**
- Use CPU instead: Edit `config.yaml`
  ```yaml
  tts:
    device: "cpu"
  whisper:
    fp16: false
  ```
- Use smaller models:
  ```yaml
  whisper:
    model: "tiny.en"
  ```

#### 4. TTS Not Working
```
TTS synthesis failed
```

**Solutions:**
```bash
# Reinstall Bark
pip uninstall suno-bark
pip install git+https://github.com/suno-ai/bark.git

# Use CPU mode
# Edit config.yaml, set tts.device to "cpu"
```

#### 5. Slow Performance
- Use smaller Whisper model (`tiny.en` or `base.en`)
- Use faster Ollama model (`llama3.2`)
- Enable GPU if available
- Increase system swap space

#### 6. Import Errors
```bash
# Reinstall all dependencies
pip uninstall -y -r requirements.txt
pip install -r requirements.txt

# Or use Poetry
poetry install
```

### Debug Mode

Enable detailed logging:

```yaml
# config.yaml
logging:
  level: "DEBUG"
  file: "debug.log"
```

Then check the log file for detailed information.

## 🔒 Privacy & Security

### Data Privacy
- **100% Local Processing**: All AI processing happens on your machine
- **No Cloud Services**: No data sent to external servers
- **No API Keys**: No tracking or usage monitoring
- **Conversation Storage**: Only in memory, deleted on exit

### Security Features
- Graceful shutdown handling
- Input validation
- Error boundary protection
- Resource cleanup on errors

## 🎨 Customization

### Change Voice

Edit `config.yaml`:
```yaml
tts:
  voice_preset: "v2/en_speaker_6"  # Try 0-9
```

### Change Personality

Edit `config.yaml`:
```yaml
prompt:
  system_message: |
    You are a friendly assistant named Alex. 
    Be conversational and warm. 
    Keep responses under 30 words.
```

### Use Different Models

```yaml
# Faster, less accurate
whisper:
  model: "tiny.en"

# Better quality, slower
whisper:
  model: "small.en"

# Different LLM
ollama:
  model: "mistral"  # or llama3.1, codellama, etc.
```

## 🧪 Advanced Usage

### Running as a Service

Create a systemd service (Linux):

```bash
# Create service file
sudo nano /etc/systemd/system/voice-assistant.service
```

```ini
[Unit]
Description=AI Voice Assistant
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/AI_Voice_Assistant
ExecStart=/path/to/venv/bin/python main.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start
sudo systemctl enable voice-assistant
sudo systemctl start voice-assistant
```

### Integration with Other Apps

```python
from main import VoiceAssistant

# Create instance
assistant = VoiceAssistant("custom_config.yaml")

# Process single interaction
assistant.process_voice_input()

# Or run full loop
assistant.run()
```

## 📁 Project Structure

```
AI_Voice_Assistant/
├── main.py                 # Main application (use this!)
├── app.py                  # Legacy file (deprecated)
├── assistant.py            # Legacy file (deprecated)
├── tts_improved.py         # Improved TTS with error handling
├── tts.py                  # Legacy TTS (deprecated)
├── utils.py                # Configuration and utilities
├── config.yaml             # Configuration file
├── punkt_downloader.py     # NLTK data downloader
├── requirements.txt        # pip dependencies
├── pyproject.toml          # Poetry configuration
├── Makefile                # Development tasks
├── README.md               # This file
└── .pre-commit-config.yaml # Code quality hooks
```

### File Descriptions

- **`main.py`** - **Use this file!** Improved version with error handling
- **`tts_improved.py`** - Enhanced TTS service with lazy loading
- **`utils.py`** - Configuration management and logging setup
- **`config.yaml`** - Central configuration for all settings
- **`app.py`, `assistant.py`** - Legacy files, kept for reference
- **`punkt_downloader.py`** - Downloads required NLTK data

## 🤝 Contributing

### Development Setup

```bash
# Clone the repo
git clone <repo-url>
cd AI_Voice_Assistant

# Install dev dependencies
pip install -r requirements.txt
pip install pre-commit

# Setup pre-commit hooks
pre-commit install

# Run linting
make lint
```

### Code Style

- **Black** for formatting
- **Ruff** for linting
- **MyPy** for type checking
- Follow PEP 8 guidelines

## 📝 Future Enhancements

### Planned Features
- ✅ Configuration system (Completed)
- ✅ Error handling (Completed)
- ✅ Logging system (Completed)
- ✅ Resource management (Completed)
- ⬜ Web interface
- ⬜ Voice activity detection (auto-stop recording)
- ⬜ Multiple conversation sessions
- ⬜ Export conversation history
- ⬜ Plugin system for custom commands
- ⬜ Wake word detection
- ⬜ Streaming responses
- ⬜ Multi-language UI

### Integration Ideas
- Home automation control
- Calendar and reminders
- Web search integration
- Document Q&A
- Code assistance

## 📜 License

This project is licensed under the MIT License. See the LICENSE file for details.

## 🙏 Acknowledgments

- **OpenAI Whisper** - Speech recognition
- **Ollama** - Local LLM inference
- **Suno Bark** - Text-to-speech synthesis
- **LangChain** - Conversation management
- **Rich** - Beautiful console interface
- All open-source contributors

## 📧 Support

- **Issues**: Report bugs on GitHub Issues
- **Discussions**: Ask questions in GitHub Discussions
- **Documentation**: Check the [Wiki](wiki) for detailed guides

## 🌟 Show Your Support

If this project helped you, please consider:
- ⭐ Starring the repository
- 🐛 Reporting bugs
- 💡 Suggesting features
- 🤝 Contributing code
- 📢 Sharing with others

---

**Built with ❤️ for voice-first AI interactions**