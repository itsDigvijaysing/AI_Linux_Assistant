#!/bin/bash
# ============================================================
# AI Voice Assistant - Run Script
# ============================================================

CONDA_PATH="/data1/cs24mtech14020/miniconda3"
ENV_NAME="voice_assistant"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Initialize conda
eval "$($CONDA_PATH/bin/conda shell.bash hook)"
conda activate "$ENV_NAME"

cd "$PROJECT_DIR"

# Use GPUs 2 and 3 (typically free)
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-2,3}"

usage() {
    echo "============================================================"
    echo "  AI Voice Assistant - Run Script"
    echo "============================================================"
    echo ""
    echo "Usage: ./run.sh [command]"
    echo ""
    echo "Commands:"
    echo "  frontend    Launch the Gradio web frontend (default)"
    echo "  cli         Run the CLI voice assistant (main.py)"
    echo "  setup       Run environment setup checks"
    echo "  personaplex Start PersonaPlex server (real-time)"
    echo "  help        Show this help message"
    echo ""
    echo "Environment: $ENV_NAME"
    echo "GPUs: $CUDA_VISIBLE_DEVICES"
    echo ""
}

case "${1:-frontend}" in
    frontend|web|ui)
        echo "Starting Gradio web frontend..."
        echo "Open http://localhost:7860 in your browser"
        python app_frontend.py
        ;;
    cli|main)
        echo "Starting CLI voice assistant..."
        python main.py
        ;;
    setup|check)
        echo "Running setup checks..."
        python setup.py
        ;;
    personaplex|pp)
        echo "Starting PersonaPlex real-time server..."
        echo "Requires: export HF_TOKEN=<your_token>"
        SSL_DIR=$(mktemp -d)
        python -m moshi.server --ssl "$SSL_DIR" ${2:+--cpu-offload}
        ;;
    help|--help|-h)
        usage
        ;;
    *)
        echo "Unknown command: $1"
        usage
        exit 1
        ;;
esac
