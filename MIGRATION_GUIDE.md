# 🔄 Migration Guide: Old → New Version

## Quick Answer

**To use the new improved version:**

```bash
# Instead of:
python app.py

# Use:
python main.py
```

That's it! Everything else works the same, but better.

---

## What's Different?

### Old Way (app.py)
```python
# Hardcoded settings in code
stt = whisper.load_model("base.en")
tts = TextToSpeechService()

# Would crash on errors
audio_np = np.frombuffer(audio_data, dtype=np.int16)
text = transcribe(audio_np)  # No error handling
```

### New Way (main.py)
```python
# Settings in config.yaml
assistant = VoiceAssistant("config.yaml")
assistant.run()

# Handles errors gracefully
result = self.transcribe(audio_np)
if result is None:
    console.print("[yellow]Transcription failed[/yellow]")
    return  # Continues running
```

---

## Side-by-Side Comparison

| Feature | app.py (Old) | main.py (New) |
|---------|--------------|---------------|
| **Configuration** | Hardcoded | config.yaml |
| **Error handling** | Minimal | Comprehensive |
| **Logging** | Console only | Structured + file |
| **Resource cleanup** | Manual | Automatic |
| **Shutdown** | Abrupt | Graceful |
| **Customization** | Edit code | Edit config |
| **Status** | Works | Works + Better |

---

## Benefits of Migrating

### 1. No More Crashes
**Before:** Error crashes entire program
```python
# If Ollama is down, app crashes
chain.predict(input=text)  # Boom! 💥
```

**After:** Friendly error message, continues running
```python
try:
    response = self.chain.predict(input=text)
except Exception as e:
    console.print(f"[red]LLM error: {e}[/red]")
    return None  # Program continues
```

### 2. Easy Customization
**Before:** Edit Python code
```python
# Find this in code and change
stt = whisper.load_model("base.en")
```

**After:** Edit config.yaml
```yaml
# Easy to find and change
whisper:
  model: "tiny.en"
```

### 3. Better Debugging
**Before:** Hard to find issues
```python
print(f"You: {text}")  # Mixed with other output
```

**After:** Structured logs
```python
self.logger.info(f"Transcribed: {text}")
# Can filter by level, save to file
```

---

## Migration Steps

### Option 1: Simple Switch (Recommended)

```bash
# Just start using the new version
python main.py
```

**No changes needed!** Your existing setup will work.

### Option 2: Full Migration

```bash
# 1. Create config.yaml (already exists)
# 2. Customize settings
nano config.yaml

# 3. Run setup checker
python setup.py

# 4. Test new version
python main.py

# 5. If all good, can delete old files (optional)
# Keep them as reference for now
```

---

## Configuration Migration

If you had custom settings in old code, here's how to move them:

### Whisper Model
**Old (in app.py):**
```python
stt = whisper.load_model("small.en")
```

**New (in config.yaml):**
```yaml
whisper:
  model: "small.en"
```

### Ollama Model
**Old (in assistant.py):**
```python
llm=Ollama(model="llama3.1")
```

**New (in config.yaml):**
```yaml
ollama:
  model: "llama3.1"
```

### TTS Voice
**Old (in app.py):**
```python
tts.long_form_synthesize(response, voice_preset="v2/en_speaker_6")
```

**New (in config.yaml):**
```yaml
tts:
  voice_preset: "v2/en_speaker_6"
```

### Audio Settings
**Old (in app.py):**
```python
with sd.RawInputStream(samplerate=16000, dtype="int16", channels=1):
```

**New (in config.yaml):**
```yaml
audio:
  sample_rate: 16000
  dtype: "int16"
  channels: 1
```

---

## Common Questions

### Q: Do I need to reinstall anything?
**A:** No! Same dependencies, just better organized.

### Q: Will my old files stop working?
**A:** No! `app.py` and `assistant.py` still work fine. New version is additional, not replacement.

### Q: What if new version has issues?
**A:** Just switch back: `python app.py`

### Q: Do I need to change my workflow?
**A:** No! Same interaction: Record → Transcribe → Respond → Play

### Q: Can I use both versions?
**A:** Yes! They're independent. Use whichever you prefer.

---

## Testing Your Migration

### Test Checklist

```bash
# 1. Old version still works
python app.py
[Test basic interaction]
Ctrl+C to exit

# 2. New version works
python main.py
[Test basic interaction]
Ctrl+C to exit

# 3. Configuration changes work
# Edit config.yaml, change a setting
python main.py
[Verify change took effect]

# 4. Error handling works
# Stop Ollama: pkill ollama
python main.py
[Should show error, not crash]

# 5. Setup checker passes
python setup.py
[All checks should pass]
```

---

## Rollback Plan

If you need to go back to old version:

```bash
# Simply use the old file
python app.py

# Or restore original if you modified it
git checkout app.py assistant.py tts.py
```

**Note:** New files don't affect old ones, so rollback is just switching which file you run.

---

## Feature Parity

Everything from old version works in new version:

| Feature | Old | New | Status |
|---------|-----|-----|--------|
| Microphone recording | ✅ | ✅ | ✅ Same |
| Whisper STT | ✅ | ✅ | ✅ Same |
| Ollama LLM | ✅ | ✅ | ✅ Same |
| Conversation memory | ✅ | ✅ | ✅ Same |
| Bark TTS | ✅ | ✅ | ✅ Same |
| Audio playback | ✅ | ✅ | ✅ Same |
| Rich console | ✅ | ✅ | ✅ Same |
| Error handling | ⚠️ Minimal | ✅ Comprehensive | ⭐ Better |
| Configuration | ❌ Hardcoded | ✅ config.yaml | ⭐ Better |
| Logging | ⚠️ Console | ✅ Structured | ⭐ Better |
| Shutdown | ⚠️ Abrupt | ✅ Graceful | ⭐ Better |

---

## What Stays the Same

- ✅ Same dependencies (no new packages required)
- ✅ Same models (Whisper, Ollama, Bark)
- ✅ Same workflow (record → process → respond)
- ✅ Same quality (transcription, responses, speech)
- ✅ Same privacy (all local, no cloud)

---

## What Improves

- ⭐ Doesn't crash on errors
- ⭐ Easy to customize (config file)
- ⭐ Better error messages
- ⭐ Structured logging
- ⭐ Graceful shutdown
- ⭐ Resource cleanup
- ⭐ Setup automation
- ⭐ Better documentation

---

## Code Comparison

### Recording Audio

**Old:**
```python
def record_audio(stop_event, data_queue):
    def callback(indata, frames, time, status):
        if status:
            console.print(status)
        data_queue.put(bytes(indata))
    
    with sd.RawInputStream(..., callback=callback):
        while not stop_event.is_set():
            time.sleep(0.1)
```

**New:**
```python
def record_audio(self, stop_event, data_queue):
    def callback(indata, frames, time_info, status):
        if status:
            self.logger.warning(f"Audio callback status: {status}")
        if not stop_event.is_set():
            data_queue.put(bytes(indata))
    
    try:
        sample_rate = self.config.get("audio.sample_rate", 16000)
        # ... more config-driven setup
        with sd.RawInputStream(..., callback=callback):
            while not stop_event.is_set() and not shutdown_event.is_set():
                time.sleep(0.1)
    except Exception as e:
        self.logger.error(f"Error in audio recording: {e}")
        console.print(f"[red]Recording error: {e}[/red]")
```

**Changes:**
- ✅ Uses configuration
- ✅ Error handling
- ✅ Structured logging
- ✅ Shutdown awareness

---

## Recommendation

### For New Users:
**Use `main.py`** - It's the current version with all improvements.

### For Existing Users:
**Try `main.py`** - Same functionality, better stability. Keep `app.py` as backup.

### For Customizers:
**Definitely use `main.py`** - Much easier to customize via config.yaml than editing code.

### For Developers:
**Use `main.py`** - Better structure, logging, and error handling for debugging.

---

## Getting Help

If migration has issues:

1. **Check setup:** `python setup.py`
2. **Compare configs:** Make sure config.yaml has your settings
3. **Test old version:** Verify `python app.py` still works
4. **Check logs:** Enable DEBUG in config.yaml
5. **Read docs:** See README.md troubleshooting section

---

## Summary

### TL;DR

- ✅ **Old version (`app.py`)**: Still works, no changes
- ✅ **New version (`main.py`)**: Same functionality + improvements
- ✅ **To migrate**: Just run `python main.py` instead
- ✅ **Benefits**: Better stability, easier customization
- ✅ **Risk**: None - can always go back

### Final Recommendation

**Start using `main.py`** - You'll appreciate:
- Not having to restart after errors
- Easy customization via config.yaml
- Better error messages when things go wrong
- Cleaner logs for debugging

**Keep `app.py`** as backup until you're comfortable with new version.

---

**Happy migrating! 🚀**
