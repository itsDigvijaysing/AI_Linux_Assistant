#!/bin/bash
# ============================================================
# AI Voice Assistant - Conda Environment Setup
# Creates conda env with CUDA support for all pipelines
# ============================================================

set -e

CONDA_PATH="/data1/cs24mtech14020/miniconda3"
ENV_NAME="voice_assistant"
PYTHON_VERSION="3.11"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================================"
echo "  AI Voice Assistant - Environment Setup"
echo "============================================================"
echo ""
echo "Project directory: $PROJECT_DIR"
echo "Conda path: $CONDA_PATH"
echo "Environment name: $ENV_NAME"
echo "Python version: $PYTHON_VERSION"
echo ""

# Initialize conda for this shell session
eval "$($CONDA_PATH/bin/conda shell.bash hook)"

# Check if environment already exists
if conda env list | grep -q "^${ENV_NAME} "; then
    echo "Environment '$ENV_NAME' already exists."
    read -p "Do you want to recreate it? (y/N): " choice
    if [[ "$choice" == "y" || "$choice" == "Y" ]]; then
        echo "Removing existing environment..."
        conda env remove -n "$ENV_NAME" -y
    else
        echo "Using existing environment."
        conda activate "$ENV_NAME"
        echo "Environment activated. Run 'python main.py' or 'python app_frontend.py' to start."
        exit 0
    fi
fi

echo ""
echo "[1/6] Creating conda environment with Python $PYTHON_VERSION..."
conda create -n "$ENV_NAME" python=$PYTHON_VERSION -y

echo ""
echo "[2/6] Activating environment..."
conda activate "$ENV_NAME"

echo ""
echo "[3/6] Installing PortAudio (for audio recording)..."
conda install -c conda-forge portaudio -y

echo ""
echo "[4/8] Installing core dependencies..."
# Note: PersonaPlex requires torch<2.5, so we let its install handle torch version
pip install \
    openai-whisper \
    nltk">=3.8.1" \
    langchain langchain-community langchain-ollama \
    "transformers>=4.30.0,<4.46.0" \
    rich">=13.7.1" \
    pyyaml">=6.0.1" \
    scipy">=1.10.0" \
    soundfile">=0.12.0" \
    "gradio==4.36.0" "gradio-client==1.0.1" \
    accelerate requests

echo ""
echo "[5/8] Installing system-level audio codec (libopus for PersonaPlex)..."
# libopus is needed for PersonaPlex's Mimi audio codec
if command -v apt-get &> /dev/null; then
    echo "Note: PersonaPlex needs libopus-dev. Install with: sudo apt install libopus-dev"
elif command -v dnf &> /dev/null; then
    echo "Note: PersonaPlex needs opus-devel. Install with: sudo dnf install opus-devel"
fi

echo ""
echo "[6/8] Setting up PersonaPlex (NVIDIA speech-to-speech)..."
PERSONAPLEX_DIR="$PROJECT_DIR/personaplex"
if [ ! -d "$PERSONAPLEX_DIR" ]; then
    echo "Cloning PersonaPlex repository..."
    git clone https://github.com/NVIDIA/personaplex.git "$PERSONAPLEX_DIR"
fi
echo "Installing PersonaPlex (moshi package)..."
pip install "$PERSONAPLEX_DIR/moshi/."

echo ""
echo "[7/8] Downloading NLTK data..."
python -c "import nltk; nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True)"

echo ""
echo "[8/8] Verifying installation..."
python -c "
import torch; print(f'  PyTorch: {torch.__version__}, CUDA: {torch.cuda.is_available()}')
import whisper; print('  Whisper: OK')
import transformers; print(f'  Transformers: {transformers.__version__}')
import gradio; print(f'  Gradio: {gradio.__version__}')
import langchain_ollama; print('  LangChain-Ollama: OK')
import moshi; print(f'  PersonaPlex (moshi): {moshi.__version__}')
import sounddevice, soundfile, scipy, nltk, rich, yaml
print('  Audio/NLP/UI: OK')
print('  All imports successful!')
"

echo ""
echo "============================================================"
echo "  Setup Complete!"
echo "============================================================"
echo ""
echo "To activate the environment:"
echo "  eval \"\$($CONDA_PATH/bin/conda shell.bash hook)\""
echo "  conda activate $ENV_NAME"
echo ""
echo "To run the voice assistant (CLI):"
echo "  python main.py"
echo ""
echo "To run the web frontend:"
echo "  python app_frontend.py"
echo ""
echo "To use PersonaPlex (set HF_TOKEN first):"
echo "  export HF_TOKEN=<your_huggingface_token>"
echo "  python app_frontend.py  # Select PersonaPlex tab in the UI"
echo ""
echo "Available GPUs:"
nvidia-smi --query-gpu=index,name,memory.free --format=csv,noheader 2>/dev/null || echo "  No NVIDIA GPUs detected"
echo ""
