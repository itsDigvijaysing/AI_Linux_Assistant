#!/usr/bin/env bash
# AI Linux Assistant — one-shot setup. Idempotent: safe to re-run.
# Installs: conda env + Python deps, Ollama (rootless), the qwen3:4b brain,
# the ONNX speech weights, optional desktop control, and a GNOME app launcher.
#
#   ./setup.sh        # set everything up, then click "AI Linux Assistant" in Activities
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="AI_Linux"
MODEL="qwen3:4b"
say(){ printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok(){  printf '   \033[0;32m✓ %s\033[0m\n' "$*"; }
warn(){ printf '   \033[0;33m! %s\033[0m\n' "$*"; }

# 1. conda env -------------------------------------------------------------
say "Conda environment ($ENV_NAME)"
CONDA="${CONDA_EXE:-}"
if [ -z "$CONDA" ] || [ ! -x "$CONDA" ]; then
  for c in "$(command -v conda 2>/dev/null || true)" "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda"; do
    [ -n "$c" ] && [ -x "$c" ] && CONDA="$c" && break
  done
fi
[ -z "${CONDA:-}" ] && { echo "ERROR: Miniconda/conda not found. Install it first: https://docs.conda.io/projects/miniconda"; exit 1; }
if "$CONDA" env list | grep -qE "^${ENV_NAME}[[:space:]]"; then ok "env exists"; else
  "$CONDA" create -y -n "$ENV_NAME" python=3.12 >/dev/null && ok "env created (python 3.12)"
fi
"$CONDA" install -y -n "$ENV_NAME" -c conda-forge portaudio >/dev/null 2>&1 && ok "portaudio"

# 2. python deps -----------------------------------------------------------
say "Python dependencies"
( cd "$HERE" && "$CONDA" run -n "$ENV_NAME" pip install -q -e ".[cpu]" ) && ok "glados engine + deps"

# 3. ollama (rootless if absent) ------------------------------------------
say "Ollama"
OLLAMA="$(command -v ollama 2>/dev/null || true)"; [ -z "$OLLAMA" ] && [ -x "$HOME/.local/bin/ollama" ] && OLLAMA="$HOME/.local/bin/ollama"
if [ -n "${OLLAMA:-}" ] && [ -x "$OLLAMA" ]; then ok "present ($("$OLLAMA" --version 2>/dev/null | grep -o '[0-9.]*' | head -1))"; else
  warn "installing rootless into ~/.local ..."
  URL="$(curl -s https://api.github.com/repos/ollama/ollama/releases/latest | grep -oE 'https://[^"]*ollama-linux-amd64\.tar\.zst' | head -1)"
  [ -z "$URL" ] && { echo "ERROR: could not resolve Ollama release URL"; exit 1; }
  curl -fSL "$URL" -o /tmp/ollama.tar.zst
  mkdir -p "$HOME/.local"; tar --zstd -C "$HOME/.local" -xf /tmp/ollama.tar.zst; rm -f /tmp/ollama.tar.zst
  OLLAMA="$HOME/.local/bin/ollama"; ok "installed"
fi

# 4. brain model -----------------------------------------------------------
say "Brain model ($MODEL)"
if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  "$OLLAMA" serve >/tmp/ai_linux_ollama.log 2>&1 &
  for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
fi
if "$OLLAMA" list 2>/dev/null | grep -q "^${MODEL}"; then ok "$MODEL present"; else
  "$OLLAMA" pull "$MODEL" && ok "$MODEL pulled"
fi

# 5. speech weights --------------------------------------------------------
say "Speech model weights (Parakeet / Kokoro / Silero)"
if ls "$HERE"/models/TTS/*.onnx >/dev/null 2>&1; then ok "present"; else
  "$CONDA" run --no-capture-output -n "$ENV_NAME" glados download && ok "downloaded"
fi

# 6. desktop control (optional) -------------------------------------------
say "Desktop control — computer-use-linux (optional)"
if [ -x "$HOME/.local/bin/computer-use-linux" ]; then ok "installed"
elif command -v cargo >/dev/null 2>&1 && [ -d "$HERE/refs/computer-use-linux" ]; then
  warn "building from refs/ (cargo, ~3 min) ..."
  ( cd "$HERE/refs/computer-use-linux" && cargo build --release -q ) \
    && cp "$HERE/refs/computer-use-linux/target/release/computer-use-linux" "$HOME/.local/bin/" && ok "built + installed"
else
  warn "skipped — later: npm install -g @agent-sh/computer-use-linux ; then 'computer-use-linux doctor'"
fi

# 7. GNOME launcher --------------------------------------------------------
say "GNOME app launcher"
chmod +x "$HERE/run.sh"
APPS="$HOME/.local/share/applications"; mkdir -p "$APPS"
cat > "$APPS/ai-linux-assistant.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AI Linux Assistant
Comment=Local voice assistant (qwen3:4b)
Exec=$HERE/run.sh
Icon=audio-input-microphone
Terminal=true
Categories=Utility;
Keywords=voice;assistant;ai;ollama;
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
ok "installed — search 'AI Linux Assistant' in Activities"

say "Setup complete"
echo "   Launch : click 'AI Linux Assistant' in the app grid, or run ./run.sh"
echo "   Groq   : export GROQ_API_KEY=... ; ./run.sh --groq"
echo "   Actions: ./run.sh --allow-actions   (enables shell/desktop tools)"
