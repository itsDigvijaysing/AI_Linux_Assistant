# 📁 Project File Index

Complete overview of all files in the AI Voice Assistant project.

---

## 🌟 Main Application Files (USE THESE)

### `main.py` ⭐ **PRIMARY APPLICATION**
**Purpose**: Improved voice assistant with error handling and configuration management  
**Use**: `python main.py`  
**Status**: ✅ Production-ready, use this!  
**Features**:
- Configuration-driven behavior
- Comprehensive error handling
- Structured logging
- Graceful shutdown
- Resource management

### `config.yaml` ⭐ **CONFIGURATION FILE**
**Purpose**: Centralized settings for all components  
**Use**: Edit to customize behavior  
**Status**: ✅ Required  
**Contains**:
- Audio settings (sample rate, channels)
- Model configurations (Whisper, Ollama, Bark)
- TTS settings (voice, device)
- Logging configuration
- Performance tuning
- UI preferences

### `tts_improved.py` ⭐ **ENHANCED TTS SERVICE**
**Purpose**: Text-to-Speech with error handling and lazy loading  
**Use**: Imported by main.py  
**Status**: ✅ Production-ready  
**Features**:
- Lazy model loading
- Error recovery
- Resource cleanup
- GPU/CPU selection
- Memory management

### `utils.py` ⭐ **UTILITY FUNCTIONS**
**Purpose**: Configuration management and helper functions  
**Use**: Imported by main.py  
**Status**: ✅ Required  
**Contains**:
- ConfigManager class
- Logging setup
- Audio validation
- Resource cleanup helpers

---

## 📚 Documentation Files

### `README.md` 📖 **MAIN DOCUMENTATION**
**Purpose**: Complete project documentation  
**Audience**: All users  
**Contains**:
- Project overview
- Installation instructions (detailed)
- Usage guide
- Architecture explanation
- Performance metrics
- Troubleshooting guide
- Customization examples
- Advanced usage

### `QUICKSTART.md` 🚀 **GETTING STARTED GUIDE**
**Purpose**: 5-minute quick start  
**Audience**: New users  
**Contains**:
- Fast setup steps
- First conversation example
- Common issues
- Configuration tips

### `IMPLEMENTATION_SUMMARY.md` 📋 **CHANGES OVERVIEW**
**Purpose**: Complete list of improvements  
**Audience**: Developers, maintainers  
**Contains**:
- What was changed
- Why it was changed
- Before/after comparisons
- Benefits summary
- Testing recommendations

### `MIGRATION_GUIDE.md` 🔄 **OLD TO NEW GUIDE**
**Purpose**: Help existing users migrate  
**Audience**: Users of app.py/assistant.py  
**Contains**:
- Side-by-side comparison
- Migration steps
- Configuration mapping
- Rollback plan
- FAQ

### `CHECKLIST.md` ✅ **PRE-FLIGHT CHECKLIST**
**Purpose**: Verify setup before first run  
**Audience**: All users  
**Contains**:
- System prerequisites
- Dependency checks
- Configuration review
- First run expectations
- Troubleshooting quick reference

### `VOICE_AGENT_COMPLETE_DOCUMENTATION.md` 📚 **REFERENCE DOCUMENTATION**
**Purpose**: Original Twilio voice agent documentation  
**Audience**: Developers (reference only)  
**Note**: We don't use Twilio, but this has useful patterns

---

## 🔧 Setup & Utility Files

### `setup.py` 🛠️ **SETUP CHECKER**
**Purpose**: Automated setup verification  
**Use**: `python setup.py`  
**Status**: ✅ Highly recommended  
**Checks**:
- Python version
- Ollama installation
- FFmpeg availability
- Audio devices
- Python dependencies
- NLTK data
- Whisper model loading
- Configuration file

### `requirements.txt` 📦 **PIP DEPENDENCIES**
**Purpose**: List of all Python packages  
**Use**: `pip install -r requirements.txt`  
**Status**: ✅ Required  
**Contains**: All dependencies with version constraints

### `pyproject.toml` 📦 **POETRY CONFIGURATION**
**Purpose**: Poetry package manager config  
**Use**: `poetry install` (alternative to pip)  
**Status**: ✅ Optional (use pip or poetry)

### `punkt_downloader.py` 📥 **NLTK DATA DOWNLOADER**
**Purpose**: Download NLTK punkt tokenizer  
**Use**: `python punkt_downloader.py`  
**Status**: ✅ Run once during setup  
**Downloads**: punkt and punkt_tab for sentence tokenization

### `Makefile` 🔨 **DEVELOPMENT COMMANDS**
**Purpose**: Convenient command shortcuts  
**Use**: `make <command>`  
**Status**: ✅ Optional but helpful  
**Commands**:
- `make install` - Install dependencies
- `make setup` - Run setup checker
- `make run` - Start assistant
- `make test` - Test components
- `make clean` - Clean cache files
- `make help` - Show all commands

---

## 📦 Legacy Files (REFERENCE ONLY)

### `app.py` 📦 **LEGACY APPLICATION**
**Purpose**: Original voice assistant  
**Status**: ⚠️ Works but deprecated  
**Use**: Reference or fallback  
**Note**: Use `main.py` instead for better stability

### `assistant.py` 📦 **LEGACY APPLICATION (DUPLICATE)**
**Purpose**: Nearly identical to app.py  
**Status**: ⚠️ Works but deprecated  
**Use**: Reference only  
**Note**: This was a duplicate; use `main.py`

### `tts.py` 📦 **LEGACY TTS SERVICE**
**Purpose**: Original TTS implementation  
**Status**: ⚠️ Works but basic  
**Use**: Reference or fallback  
**Note**: Use `tts_improved.py` (via main.py) for better error handling

---

## 🔒 Configuration Files

### `.pre-commit-config.yaml` 🔒 **CODE QUALITY HOOKS**
**Purpose**: Git pre-commit hooks for code quality  
**Status**: ✅ Development tool  
**Tools**:
- Black (formatting)
- Ruff (linting)
- MyPy (type checking)
- Gitleaks (security)

---

## 📂 Directories

### `__pycache__/` 💾 **PYTHON CACHE**
**Purpose**: Compiled Python bytecode  
**Status**: Auto-generated  
**Note**: Can be deleted (`make clean`)

### `.git/` 🔒 **GIT REPOSITORY**
**Purpose**: Version control data  
**Status**: Auto-managed  
**Note**: Don't modify manually

---

## 🗂️ File Usage Matrix

| File | Used By | Required | Purpose |
|------|---------|----------|---------|
| `main.py` | **User** | ✅ Yes | Run assistant |
| `config.yaml` | main.py | ✅ Yes | Configuration |
| `tts_improved.py` | main.py | ✅ Yes | TTS service |
| `utils.py` | main.py | ✅ Yes | Utilities |
| `setup.py` | User | ⭐ Recommended | Verify setup |
| `requirements.txt` | pip | ✅ Yes | Dependencies |
| `punkt_downloader.py` | User (once) | ✅ Yes | NLTK data |
| `README.md` | User | 📖 Read | Documentation |
| `QUICKSTART.md` | User | 📖 Read | Quick start |
| `CHECKLIST.md` | User | 📖 Read | Pre-flight |
| `app.py` | Legacy | ❌ No | Old version |
| `assistant.py` | Legacy | ❌ No | Duplicate |
| `tts.py` | Legacy | ❌ No | Old TTS |
| `Makefile` | Developer | ⭐ Helpful | Commands |
| `pyproject.toml` | Poetry | ⚠️ Optional | Poetry config |

---

## 🎯 Which Files to Use?

### For Normal Use:
1. ✅ **Read**: `README.md` or `QUICKSTART.md`
2. ✅ **Run**: `python setup.py` (first time)
3. ✅ **Edit**: `config.yaml` (customize)
4. ✅ **Run**: `python main.py` (use assistant)

### For Development:
1. ✅ **Edit**: `main.py`, `tts_improved.py`, `utils.py`
2. ✅ **Use**: `Makefile` for commands
3. ✅ **Read**: `IMPLEMENTATION_SUMMARY.md`
4. ✅ **Install**: Pre-commit hooks

### For Troubleshooting:
1. ✅ **Run**: `python setup.py`
2. ✅ **Check**: `CHECKLIST.md`
3. ✅ **Read**: `README.md` troubleshooting section
4. ✅ **Enable**: Debug logging in `config.yaml`

---

## 📊 File Size Reference

| File | Approx Size | Type |
|------|-------------|------|
| `main.py` | ~15 KB | Code |
| `config.yaml` | ~1 KB | Config |
| `tts_improved.py` | ~7 KB | Code |
| `utils.py` | ~7 KB | Code |
| `README.md` | ~25 KB | Docs |
| `setup.py` | ~7 KB | Script |
| `requirements.txt` | ~1 KB | Text |

**Total Project**: ~150 KB (excluding models and dependencies)

---

## 🔄 Lifecycle

### One-Time Setup Files:
- `requirements.txt` → Install once
- `punkt_downloader.py` → Run once
- `setup.py` → Run after changes

### Every-Run Files:
- `main.py` → Run each time
- `config.yaml` → Read each time
- `tts_improved.py` → Loaded each time
- `utils.py` → Loaded each time

### Edit When Needed:
- `config.yaml` → Change settings
- `main.py` → Customize behavior
- `.pre-commit-config.yaml` → Update tools

### Reference Only:
- `README.md` → Read when needed
- `QUICKSTART.md` → Read once
- Documentation files → Reference

---

## 🎯 File Priority

### Priority 1 (Essential):
1. `main.py` - The application
2. `config.yaml` - Configuration
3. `requirements.txt` - Dependencies
4. `README.md` - Instructions

### Priority 2 (Highly Recommended):
5. `setup.py` - Verification
6. `QUICKSTART.md` - Quick start
7. `tts_improved.py` - TTS service
8. `utils.py` - Utilities

### Priority 3 (Helpful):
9. `CHECKLIST.md` - Pre-flight
10. `Makefile` - Commands
11. `MIGRATION_GUIDE.md` - If migrating
12. `punkt_downloader.py` - NLTK data

### Priority 4 (Reference):
13. `IMPLEMENTATION_SUMMARY.md` - What changed
14. Other documentation files
15. Legacy files (app.py, etc.)

---

## 📝 File Relationships

```
main.py
  ├── imports: utils.py (ConfigManager, logging)
  ├── imports: tts_improved.py (TextToSpeechService)
  ├── reads: config.yaml
  ├── uses: whisper (external)
  ├── uses: ollama (external)
  └── documented in: README.md

config.yaml
  └── read by: utils.py → main.py

setup.py
  └── standalone (verifies everything)

requirements.txt
  └── used by: pip install

Makefile
  └── runs: various commands (setup.py, main.py, etc.)
```

---

## 🎓 Learning Path

### Beginner:
1. Read `QUICKSTART.md`
2. Run `python setup.py`
3. Use `python main.py`
4. Edit `config.yaml` for customization

### Intermediate:
1. Read full `README.md`
2. Understand `main.py` structure
3. Check `CHECKLIST.md` for optimization
4. Use `Makefile` commands

### Advanced:
1. Read `IMPLEMENTATION_SUMMARY.md`
2. Study code in `main.py`, `utils.py`, `tts_improved.py`
3. Customize for your needs
4. Contribute improvements

---

## ✅ Quick Reference

**To start using:**
```bash
python main.py
```

**To customize:**
```bash
nano config.yaml
```

**To verify setup:**
```bash
python setup.py
```

**To get help:**
```bash
cat README.md | less
# or
cat QUICKSTART.md
```

**To troubleshoot:**
```bash
# Check checklist
cat CHECKLIST.md

# Enable debug
nano config.yaml  # Set logging.level to DEBUG
python main.py
```

---

**📁 Total Files**: 22 files (excluding __pycache__ and .git)  
**🌟 Files to Use**: 4-8 main files  
**📚 Documentation**: 6 guide files  
**🔧 Utilities**: 4 helper files  
**📦 Legacy**: 3 reference files  

**You're ready to go! 🚀**
