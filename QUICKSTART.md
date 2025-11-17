# 🚀 Quick Start Guide

## First Time Setup (5 minutes)

### 1. Install Ollama
```bash
# Visit https://ollama.ai and install for your OS
# Or use package managers:

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS
brew install ollama

# Windows
# Download from https://ollama.ai
```

### 2. Start Ollama and Pull Model
```bash
# Start Ollama service
ollama serve &

# Pull the model (2GB download)
ollama pull llama3.2
```

### 3. Clone and Setup Project
```bash
# Clone repository
git clone <your-repo-url>
cd AI_Voice_Assistant

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
# OR
venv\Scripts\activate     # Windows

# Install dependencies (may take 5-10 minutes)
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Run Setup Checker
```bash
python setup.py
```

This will verify:
- ✅ Python version
- ✅ Ollama installation
- ✅ FFmpeg
- ✅ Audio devices
- ✅ All dependencies

### 5. Run the Assistant!
```bash
python main.py
```

## First Conversation

```
Assistant started! Press Ctrl+C to exit.
Press Enter to start recording, then press Enter again to stop.

[Press Enter]
[Speak: "Hello, who are you?"]
[Press Enter]

Transcribing...
You: Hello, who are you?

Generating response...
Assistant: I'm an AI voice assistant here to help you with information and questions.

Synthesizing speech...
[Plays audio response]

[Ready for next question]
```

## Common First-Time Issues

### Issue: "Ollama not found"
**Solution:**
```bash
# Check Ollama installation
which ollama

# If not found, install from https://ollama.ai
# Then start it:
ollama serve
```

### Issue: "No audio recorded"
**Solution:**
- Check microphone permissions
- Test mic: `python -c "import sounddevice; print(sounddevice.query_devices())"`
- Select correct input device in system settings

### Issue: "CUDA out of memory" or slow TTS
**Solution:**
Edit `config.yaml`:
```yaml
tts:
  device: "cpu"  # Force CPU mode
```

### Issue: "Import errors"
**Solution:**
```bash
# Reinstall all dependencies
pip uninstall -r requirements.txt -y
pip install -r requirements.txt
```

## Configuration Tips

### Make it Faster
Edit `config.yaml`:
```yaml
whisper:
  model: "tiny.en"  # Smaller, faster model

ollama:
  model: "llama3.2"  # Already fast

tts:
  device: "cuda"  # If you have NVIDIA GPU
```

### Make Responses Shorter
Edit `config.yaml`:
```yaml
prompt:
  system_message: "Answer in 10 words or less."
```

### Change Voice
Edit `config.yaml`:
```yaml
tts:
  voice_preset: "v2/en_speaker_6"  # Try 0-9
```

## Next Steps

1. **Customize**: Edit `config.yaml` to your preferences
2. **Experiment**: Try different models and voices
3. **Integrate**: Use it in your projects (see README)
4. **Contribute**: Report bugs, suggest features

## Getting Help

- Read full README.md for detailed documentation
- Check Troubleshooting section in README
- Run `python setup.py` to diagnose issues
- Enable debug mode: Set `logging.level: "DEBUG"` in config.yaml

## Useful Commands

```bash
# Check Ollama models
ollama list

# Test Ollama
ollama run llama3.2 "Hello"

# List audio devices
python -c "import sounddevice; print(sounddevice.query_devices())"

# Run with debug logging
python main.py  # Check logs

# Update dependencies
pip install --upgrade -r requirements.txt
```

---

**You're all set! Enjoy your voice assistant! 🎉**
