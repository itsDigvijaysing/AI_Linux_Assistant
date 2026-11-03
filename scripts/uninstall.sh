#!/usr/bin/env bash
# Removes Phoenix for the current user. Keeps settings, history, knowledge, logs, models and voices unless --purge.
#
#   scripts/uninstall.sh [--dry-run] [--yes] [--purge]
#
# Never touched: system packages, ~/.ollama (shared with any other Ollama), a system ollama.service, and the
# Hugging Face cache in ~/.cache/huggingface.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/uninstall.sh [options]

Removes Phoenix: the launcher, the app-grid entry, the start-at-login entry, the app copy, the speech
engine's environment, Phoenix's own Ollama and the cache. Your settings, history, knowledge, logs and
downloaded models stay, so installing again picks up where you left off.

  --purge      also delete settings, history, knowledge, logs, downloaded models and the voices
  --dry-run    print what would be removed and change nothing
  --yes        don't ask before removing
  -h, --help   show this help
EOF
}

DRY=0 YES=0 PURGE=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --yes | -y) YES=1 ;;
        --purge) PURGE=1 ;;
        -h | --help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
    esac
done

# Same folders as ai_linux.paths.
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/ai-linux"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/ai-linux"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/ai-linux"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/ai-linux"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/io.github.itsdigvijaysing.AILinux.desktop"
ENTRY="$DATA_HOME/applications/io.github.itsdigvijaysing.AILinux.desktop"
ICON_NAME="io.github.itsdigvijaysing.AILinux"
LAUNCHER="$HOME/.local/bin/ai-linux-app"
VOICES="${SUPERTONIC_CACHE_DIR:-$HOME/.cache/supertonic3}"  # the voices, fetched by the supertonic library
UNIT="ai-linux-ollama.service"

note() { printf '   %s\n' "$*"; }
die() { printf '\nError: %s\n' "$*" >&2; exit 1; }

run() {
    if ((DRY)); then
        printf '   would run:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

remove() {
    local path
    for path in "$@"; do
        [[ -e "$path" || -L "$path" ]] || continue
        if ((DRY)); then
            note "would remove $path"
        else
            rm -rf -- "$path"
            note "Removed $path"
        fi
    done
}

size() { du -sh -- "$1" 2>/dev/null | cut -f1; }

# Everything we installed: launchers, code, the engine's environment, our Ollama build and caches.
targets=("$ENTRY" "$AUTOSTART")
shopt -s nullglob
icons="$DATA_HOME/icons/hicolor"
targets+=("$icons"/*/apps/"$ICON_NAME".png "$icons"/*/apps/"$ICON_NAME".svg "$icons"/*/apps/"$ICON_NAME"-symbolic.svg)
targets+=("$DATA/app" "$DATA/app.new" "$DATA/app.old" "$DATA/venv" "$DATA"/venv.old-* "$DATA/ollama" "$CACHE")
shopt -u nullglob
# Only our own link: never a different program of the same name.
if [[ -L "$LAUNCHER" && "$(readlink -- "$LAUNCHER")" == */bin/ai-linux-app ]]; then
    targets+=("$LAUNCHER")
fi
if ((PURGE)); then
    targets+=("$CONFIG" "$DATA" "$STATE" "$VOICES")
fi

present=()
for path in "${targets[@]}"; do
    [[ -e "$path" || -L "$path" ]] && present+=("$path")
done

printf 'Phoenix uninstaller\n'
((DRY)) && printf '  Dry run: nothing is changed.\n'
if ((${#present[@]})); then
    printf '\nThis removes:\n'
    for path in "${present[@]}"; do
        printf '  %s\n' "$path"
    done
else
    printf '\nNothing of Phoenix is installed for this user.\n'
fi
if ((PURGE)); then
    printf '\n--purge: your settings, history, knowledge, logs and downloaded models and voices are deleted too.\n'
else
    printf '\nKept (delete with --purge):\n'
    for path in "$CONFIG" "$DATA/history.db" "$DATA/knowledge.json" "$DATA/models" "$STATE" "$VOICES"; do
        [[ -e "$path" ]] && printf '  %s (%s)\n' "$path" "$(size "$path")"
    done
fi
if [[ -d "$HOME/.ollama" ]]; then
    printf '\nNot touched: %s (%s), the Ollama models, shared with any other Ollama.\n' \
        "$HOME/.ollama" "$(size "$HOME/.ollama")"
fi
printf '\n'

if ((!DRY && ${#present[@]})); then
    if ((!YES)); then
        [[ -t 0 ]] || die "There is no terminal to ask on. Run again with --yes to go ahead."
        prompt="Remove Phoenix? [y/N] "
        ((PURGE)) && prompt="Remove Phoenix and delete your settings, history and models? [y/N] "
        read -r -p "$prompt" answer
        [[ "$answer" == [Yy]* ]] || die "Nothing was changed."
    fi
fi

# Quit the running app first, through the app itself.
if pgrep -u "$(id -u)" -f -- '-m ai_linux( |$)' >/dev/null 2>&1; then
    if [[ -x "$LAUNCHER" ]]; then
        run "$LAUNCHER" --quit || true
    else
        note "Phoenix is running: quit it from the tray before reinstalling."
    fi
fi

# Our Ollama only: the user unit the app starts, or the process the app started without systemd.
if command -v systemctl >/dev/null 2>&1 &&
    systemctl --user is-active --quiet "$UNIT" 2>/dev/null; then
    run systemctl --user stop "$UNIT"
fi
pid_file="$STATE/ollama.pid"
if [[ -r "$pid_file" ]]; then
    pid="$(tr -dc 0-9 <"$pid_file")"
    if [[ -n "$pid" && -r "/proc/$pid/cmdline" ]] && tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q 'ollama.* serve'; then
        run kill -TERM "$pid"
    fi
    remove "$pid_file"
fi

remove "${present[@]}"

if ((DRY)); then
    printf '\nDry run finished: nothing was changed.\n'
else
    printf '\nPhoenix is removed. System packages installed for it stay; remove them with apt if you like.\n'
fi
