#!/usr/bin/env bash
# AI Linux Assistant launcher — prepares the runtime and starts the assistant.
# Works from a terminal or a GNOME .desktop click (resolves conda, auto-starts Ollama).
#
# Usage:
#   ./run.sh                  # voice + text (qwen3:4b, local)   [default]
#   ./run.sh tui              # Textual text UI
#   ./run.sh download         # pre-fetch ONNX model weights
#   ./run.sh --groq           # Groq API brain (needs GROQ_API_KEY); default is local Ollama
#   ./run.sh --allow-actions  # arm gated shell/desktop actions for this run
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$HERE/configs/ai_linux_config.yaml"
ENV_NAME="AI_Linux"

# --- resolve conda (works outside an activated shell, e.g. a .desktop launch) ---
CONDA="${CONDA_EXE:-}"
if [ -z "${CONDA}" ] || [ ! -x "${CONDA}" ]; then
  if command -v conda >/dev/null 2>&1; then CONDA="$(command -v conda)"
  elif [ -x "$HOME/miniconda3/bin/conda" ]; then CONDA="$HOME/miniconda3/bin/conda"
  elif [ -x "$HOME/anaconda3/bin/conda" ]; then CONDA="$HOME/anaconda3/bin/conda"
  else echo "ERROR: conda not found — run ./setup.sh first."; exit 1; fi
fi

SUB="start"
ARGS=()
for a in "$@"; do
  case "$a" in
    --allow-actions) export GLADOS_ALLOW_ACTIONS=1 ;;
    --groq)  CONFIG="$HERE/configs/ai_linux_groq.yaml" ;;
    --local) CONFIG="$HERE/configs/ai_linux_config.yaml" ;;
    start|tui|download|say) SUB="$a" ;;
    *) ARGS+=("$a") ;;
  esac
done

# `download` only fetches model files — no brain needed.
if [ "$SUB" = "download" ]; then
  exec "$CONDA" run --no-capture-output -n "$ENV_NAME" glados download
fi

# --- ensure Ollama is serving when using the local brain ---
if [[ "$CONFIG" == *ai_linux_config.yaml ]]; then
  OLLAMA="$(command -v ollama 2>/dev/null || true)"; [ -z "$OLLAMA" ] && OLLAMA="$HOME/.local/bin/ollama"
  if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    if [ -x "$OLLAMA" ]; then
      echo "Starting Ollama..."; "$OLLAMA" serve >/tmp/ai_linux_ollama.log 2>&1 &
      for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
    else
      echo "WARN: ollama not found — the local brain won't respond. Run ./setup.sh."
    fi
  fi
fi

exec "$CONDA" run --no-capture-output -n "$ENV_NAME" glados "$SUB" --config "$CONFIG" ${ARGS[@]+"${ARGS[@]}"}
