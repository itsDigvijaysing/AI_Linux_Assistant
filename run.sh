#!/usr/bin/env bash
# AI Linux Assistant launcher — runs the vendored GLaDOS engine with OUR config
# in the AI_Linux conda env.
#
# Usage:
#   ./run.sh                  # voice + text assistant (start)
#   ./run.sh tui              # Textual text UI
#   ./run.sh download         # pre-fetch the ONNX model weights
#   ./run.sh --allow-actions  # arm gated shell/desktop actions for this run
#   ./run.sh --groq           # use the Groq API brain (needs GROQ_API_KEY); default is local Ollama
#   ./run.sh tui --allow-actions
#
# Gated actions (mcp.shell.*, mcp.computer_use.*) are denied unless --allow-actions
# (i.e. GLADOS_ALLOW_ACTIONS=1) is set. Autonomy never runs them.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$HERE/configs/ai_linux_config.yaml"
ENV_NAME="AI_Linux"

SUB="start"
ARGS=()
for a in "$@"; do
  case "$a" in
    --allow-actions) export GLADOS_ALLOW_ACTIONS=1 ;;
    --groq) CONFIG="$HERE/configs/ai_linux_groq.yaml" ;;
    --local) CONFIG="$HERE/configs/ai_linux_config.yaml" ;;
    start|tui|download|say) SUB="$a" ;;
    *) ARGS+=("$a") ;;
  esac
done

if [ "$SUB" = "download" ]; then
  exec conda run --no-capture-output -n "$ENV_NAME" glados download
fi

exec conda run --no-capture-output -n "$ENV_NAME" glados "$SUB" --config "$CONFIG" ${ARGS[@]+"${ARGS[@]}"}
