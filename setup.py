#!/usr/bin/env python3
"""
Setup script for AI Voice Assistant.
Checks dependencies, downloads models, and verifies configuration.
"""

import sys
import subprocess
import os
from pathlib import Path


def print_header(text):
    """Print a formatted header."""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def check_python_version():
    """Check if Python version is compatible."""
    print_header("Checking Python Version")
    
    version = sys.version_info
    print(f"Python version: {version.major}.{version.minor}.{version.micro}")
    
    if version.major != 3 or version.minor < 11:
        print("❌ Python 3.11+ required")
        print("   Current version is not supported")
        return False
    
    if version.minor >= 12:
        print("⚠️  Python 3.12+ may have compatibility issues")
        print("   Python 3.11 is recommended")
    
    print("✅ Python version compatible")
    return True


def check_ollama():
    """Check if Ollama is installed and running."""
    print_header("Checking Ollama")
    
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            print("✅ Ollama is installed and running")
            print("\nInstalled models:")
            print(result.stdout)
            
            # Check for llama3.2
            if "llama3.2" not in result.stdout:
                print("⚠️  Recommended model 'llama3.2' not found")
                print("   Run: ollama pull llama3.2")
            
            return True
        else:
            print("⚠️  Ollama installed but may not be running")
            print("   Run: ollama serve")
            return False
            
    except FileNotFoundError:
        print("❌ Ollama not found")
        print("   Install from: https://ollama.ai")
        return False
    except subprocess.TimeoutExpired:
        print("❌ Ollama not responding")
        print("   Try running: ollama serve")
        return False


def check_ffmpeg():
    """Check if FFmpeg is installed."""
    print_header("Checking FFmpeg")
    
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            print("✅ FFmpeg is installed")
            return True
        else:
            print("❌ FFmpeg check failed")
            return False
            
    except FileNotFoundError:
        print("❌ FFmpeg not found")
        print("   Linux: sudo apt-get install ffmpeg")
        print("   macOS: brew install ffmpeg")
        print("   Windows: Download from https://ffmpeg.org")
        return False


def check_audio_devices():
    """Check if audio devices are available."""
    print_header("Checking Audio Devices")
    
    try:
        import sounddevice as sd
        
        devices = sd.query_devices()
        print("✅ Audio system accessible")
        print(f"\nFound {len(devices)} audio devices:")
        
        for i, device in enumerate(devices):
            if device['max_input_channels'] > 0:
                print(f"  📥 Input: {device['name']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Audio system error: {e}")
        print("   Check microphone permissions")
        return False


def download_nltk_data():
    """Download required NLTK data."""
    print_header("Downloading NLTK Data")
    
    try:
        import nltk
        
        print("Downloading punkt tokenizer...")
        nltk.download('punkt', quiet=True)
        nltk.download('punkt_tab', quiet=True)
        
        print("✅ NLTK data downloaded")
        return True
        
    except Exception as e:
        print(f"❌ Failed to download NLTK data: {e}")
        return False


def verify_config():
    """Verify configuration file exists."""
    print_header("Checking Configuration")
    
    config_path = Path("config.yaml")
    
    if config_path.exists():
        print(f"✅ Configuration file found: {config_path}")
        return True
    else:
        print(f"❌ Configuration file not found: {config_path}")
        print("   Please ensure config.yaml exists")
        return False


def check_dependencies():
    """Check if required packages are installed."""
    print_header("Checking Python Dependencies")
    
    required = [
        "numpy",
        "whisper",
        "sounddevice",
        "rich",
        "langchain",
        "transformers",
        "torch",
        "yaml"
    ]
    
    missing = []
    
    for package in required:
        try:
            __import__(package)
            print(f"✅ {package}")
        except ImportError:
            print(f"❌ {package} not found")
            missing.append(package)
    
    if missing:
        print(f"\n⚠️  Missing packages: {', '.join(missing)}")
        print("   Run: pip install -r requirements.txt")
        return False
    
    print("\n✅ All dependencies installed")
    return True


def test_whisper():
    """Test Whisper model loading."""
    print_header("Testing Whisper Model")
    
    try:
        import whisper
        
        print("Loading base.en model...")
        model = whisper.load_model("base.en")
        print("✅ Whisper model loaded successfully")
        return True
        
    except Exception as e:
        print(f"❌ Whisper test failed: {e}")
        return False


def main():
    """Run all setup checks."""
    print("""
    ╔══════════════════════════════════════════════════════╗
    ║                                                      ║
    ║     🎙️  AI Voice Assistant Setup Checker  🎙️       ║
    ║                                                      ║
    ╚══════════════════════════════════════════════════════╝
    """)
    
    checks = [
        ("Python Version", check_python_version),
        ("Ollama", check_ollama),
        ("FFmpeg", check_ffmpeg),
        ("Configuration", verify_config),
        ("Dependencies", check_dependencies),
        ("Audio Devices", check_audio_devices),
        ("NLTK Data", download_nltk_data),
        ("Whisper Model", test_whisper),
    ]
    
    results = {}
    
    for name, check_func in checks:
        try:
            results[name] = check_func()
        except Exception as e:
            print(f"❌ {name} check failed with error: {e}")
            results[name] = False
    
    # Summary
    print_header("Setup Summary")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, status in results.items():
        icon = "✅" if status else "❌"
        print(f"{icon} {name}")
    
    print(f"\n{passed}/{total} checks passed")
    
    if passed == total:
        print("\n🎉 All checks passed! You're ready to go!")
        print("\nRun the assistant:")
        print("  python main.py")
    else:
        print("\n⚠️  Some checks failed. Please resolve the issues above.")
        print("\nCommon solutions:")
        print("  1. Install missing dependencies: pip install -r requirements.txt")
        print("  2. Install Ollama: https://ollama.ai")
        print("  3. Pull Ollama model: ollama pull llama3.2")
        print("  4. Start Ollama: ollama serve")
    
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
