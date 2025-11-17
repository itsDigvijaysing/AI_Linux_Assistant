# ✅ Pre-Flight Checklist

Use this checklist before running the voice assistant for the first time.

## 🔧 System Prerequisites

- [ ] **Operating System**: Linux, macOS, or Windows
- [ ] **Python Version**: 3.11 installed (`python3.11 --version`)
- [ ] **Virtual Environment**: Created and activated
- [ ] **Internet Connection**: Available for initial downloads

## 📦 Dependencies

- [ ] **FFmpeg installed**
  ```bash
  ffmpeg -version
  ```

- [ ] **Ollama installed**
  ```bash
  ollama --version
  ```

- [ ] **Python packages installed**
  ```bash
  pip list | grep -E "(whisper|sounddevice|langchain|torch|transformers)"
  ```

## 🤖 Ollama Setup

- [ ] **Ollama service running**
  ```bash
  # Start if not running
  ollama serve &
  ```

- [ ] **Model downloaded** (llama3.2 or your preferred model)
  ```bash
  ollama list
  # Should show llama3.2 or your chosen model
  ```

- [ ] **Test Ollama**
  ```bash
  ollama run llama3.2 "Say hello"
  # Should get a response
  ```

## 🎤 Audio Setup

- [ ] **Microphone connected**
- [ ] **Microphone permissions granted**
- [ ] **Audio devices detected**
  ```bash
  python -c "import sounddevice as sd; print(sd.query_devices())"
  # Should list your microphone
  ```

## 📁 Project Files

- [ ] **All required files present**
  - [ ] `main.py`
  - [ ] `tts_improved.py`
  - [ ] `utils.py`
  - [ ] `config.yaml`
  - [ ] `requirements.txt`
  - [ ] `punkt_downloader.py`

- [ ] **Configuration file exists**
  ```bash
  ls -la config.yaml
  ```

## 🧪 Quick Tests

- [ ] **NLTK data downloaded**
  ```bash
  python punkt_downloader.py
  ```

- [ ] **Import test passes**
  ```bash
  python -c "import whisper, sounddevice, torch, transformers; print('OK')"
  ```

- [ ] **Setup checker passes**
  ```bash
  python setup.py
  # All checks should pass
  ```

## 🎯 Configuration Review

Open `config.yaml` and verify:

- [ ] **Whisper model**: Set to your preference (tiny/base/small)
- [ ] **Ollama model**: Matches what you downloaded
- [ ] **TTS device**: Set to "cpu" or "cuda" based on your hardware
- [ ] **Logging level**: Set appropriately (INFO for normal use)

## 🚀 First Run Checklist

Before running `python main.py`:

- [ ] **Terminal is ready**: Large enough to see output
- [ ] **Microphone is unmuted**: System settings checked
- [ ] **Quiet environment**: For better speech recognition
- [ ] **Ollama is running**: Check with `ollama list`
- [ ] **Virtual environment activated**: See `(venv)` in prompt

## 📊 Expected First Run

When you run `python main.py`, you should see:

```
[Expected output]
1. ✅ "Assistant started! Press Ctrl+C to exit."
2. ✅ Prompt to press Enter to record
3. ✅ (After recording) "Transcribing..." with spinner
4. ✅ Your transcribed text displayed
5. ✅ "Generating response..." with spinner
6. ✅ Assistant's response displayed
7. ✅ Audio plays through speakers
```

## ⚠️ Common First-Time Issues

Check these if something doesn't work:

- [ ] **"Ollama not found"**
  - Solution: Install Ollama from https://ollama.ai
  
- [ ] **"No audio recorded"**
  - Solution: Check microphone permissions and selection
  
- [ ] **"CUDA out of memory"**
  - Solution: Edit config.yaml, set `tts.device: "cpu"`
  
- [ ] **"Import error"**
  - Solution: `pip install -r requirements.txt`
  
- [ ] **Slow performance**
  - Solution: Use smaller models in config.yaml

## 🎓 Quick Commands Reference

```bash
# Activate environment
source venv/bin/activate

# Check Ollama
ollama list

# Run setup checker
python setup.py

# Start assistant
python main.py

# Stop assistant
Ctrl+C

# View logs (if enabled)
tail -f assistant.log
```

## ✨ Success Indicators

You're ready if:

- [ ] ✅ `python setup.py` shows all green checkmarks
- [ ] ✅ `ollama list` shows your model
- [ ] ✅ Microphone is visible in device list
- [ ] ✅ No import errors when testing
- [ ] ✅ Config file exists and is readable

## 🎯 Final Step

Once all checkboxes are checked:

```bash
python main.py
```

Say: **"Hello, can you hear me?"**

If you see transcription and hear a response: **🎉 SUCCESS!**

---

## 💡 Pro Tips

### Before Each Session

1. **Check Ollama**: `ollama list` (takes 2 seconds)
2. **Check mic**: Quick test in system settings
3. **Activate venv**: `source venv/bin/activate`

### During Use

- Speak clearly, not too fast
- Press Enter when completely done speaking
- Wait for spinners to complete
- Use Ctrl+C for clean exit

### For Best Results

- Use in quiet environment
- Quality USB microphone helps
- Keep responses short (configured in config.yaml)
- Monitor system resources

---

## 📞 Need Help?

If checklist doesn't help:

1. **Enable debug mode**:
   ```yaml
   # config.yaml
   logging:
     level: "DEBUG"
     file: "debug.log"
   ```

2. **Run again and check debug.log**

3. **Check troubleshooting in README.md**

4. **Verify each component individually**:
   ```bash
   # Test imports
   python -c "import whisper; print('Whisper: OK')"
   python -c "import torch; print('PyTorch: OK')"
   python -c "import sounddevice; print('Audio: OK')"
   
   # Test Ollama
   ollama run llama3.2 "test"
   
   # Test audio
   python -c "import sounddevice as sd; sd.rec(16000, samplerate=16000, channels=1)"
   ```

---

## ✅ Ready to Go!

When all boxes are checked, you're ready to enjoy your AI voice assistant!

**First command to try:**
```bash
python main.py
```

**First question to ask:**
*"What can you help me with?"*

**Good luck! 🚀**
