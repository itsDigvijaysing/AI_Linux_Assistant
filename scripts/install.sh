#!/usr/bin/env bash
# Installs Phoenix for the current user on Ubuntu 26.04 GNOME. Safe to run again: done steps are skipped.
#
#   scripts/install.sh [--dry-run] [--yes] [--link] [--no-ollama] [--no-models] [--skip-apt]
#
# Only the system packages need sudo, in one step listed before asking. Everything else stays in your home:
#   ~/.local/share/ai-linux/app     a copy of the app (or this checkout itself with --link)
#   ~/.local/share/ai-linux/venv    the speech engine's Python 3.14 environment (requirements-engine.txt)
#   ~/.local/share/ai-linux/ollama  Phoenix's own Ollama build, run as the user unit ai-linux-ollama on port 11435
#   ~/.local/share/ai-linux/models  speech recognition and the small router model
#   ~/.local/bin/ai-linux-app       the launcher, plus the app-grid entry and icons under ~/.local/share
# Models download through the app's own code (ai_linux.downloads), with checksums and resume.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/install.sh [options]

Installs Phoenix for your user. Run it again any time: finished steps are skipped and
interrupted downloads resume.

  --dry-run    print what would happen and change nothing
  --yes        don't ask before starting (needed when there is no terminal)
  --link       run the app from this folder instead of a copy (for development)
  --no-ollama  don't install or start Phoenix's own Ollama
  --no-models  don't download speech recognition, the voices or the small router model
  --skip-apt   don't install system packages (you install them yourself)
  -h, --help   show this help

The big model is not downloaded here: pick it on the Models page.
EOF
}

DRY=0 YES=0 LINK=0 OLLAMA=1 MODELS=1 APT=1
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --yes | -y) YES=1 ;;
        --link) LINK=1 ;;
        --no-ollama) OLLAMA=0 ;;
        --no-models) MODELS=0 ;;
        --skip-apt) APT=0 ;;
        -h | --help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
    esac
done

# An empty XDG variable means the default, as in ai_linux.paths; some libraries would read it as the current folder.
for var in XDG_CONFIG_HOME XDG_DATA_HOME XDG_STATE_HOME XDG_CACHE_HOME; do
    [[ -n "${!var:-}" ]] || unset "$var"
done

SRC_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/ai-linux"
COPY_ROOT="$DATA/app"
VENV="$DATA/venv"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/ai-linux-app"
# The app runs on the system Python with python3-gi; the override is for testing on other systems.
SYS_PY="${PHOENIX_SYSTEM_PYTHON:-/usr/bin/python3}"
WANT_PY="3.14"
if ((LINK)); then APP_ROOT="$SRC_ROOT"; else APP_ROOT="$COPY_ROOT"; fi

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '   Warning: %s\n' "$*" >&2; }
die() { printf '\nError: %s\n' "$*" >&2; exit 1; }

# Runs a command, or only prints it in a dry run.
run() {
    if ((DRY)); then
        printf '   would run:'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

confirm() {
    ((YES)) && return 0
    [[ -t 0 ]] || die "There is no terminal to ask on. Run again with --yes to go ahead."
    local answer
    read -r -p "$1 [Y/n] " answer
    [[ -z "$answer" || "$answer" == [Yy]* ]]
}

py_version() { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true; }

# The helper drives the app's own modules, so downloads, Ollama and the launcher work exactly as in the app.
HELPER=$(
    cat <<'PY'
import re, shutil, sys, tempfile, threading, time
from pathlib import Path

MIN_OLLAMA = (0, 40)  # Gemma 4 needs 0.40


def out(text=""):
    print(text, flush=True)


class Bar:
    """One progress line: redrawn in a terminal, every 10 % otherwise."""

    def __init__(self, label):
        self.label, self.tty, self.shown, self.at = label, sys.stdout.isatty(), -1, 0.0

    def __call__(self, done, total, status=None):
        if status and status != self.label:
            self.end()
            self.label, self.shown = status, -1
        if not total:
            return
        pct = min(100, int(done * 100 / total))
        now = time.monotonic()
        if self.tty and pct != self.shown and (now - self.at > 0.2 or pct == 100):
            sys.stdout.write(f"\r   {self.label}: {pct:3d}% of {total / 1e9:.2f} GB")
            sys.stdout.flush()
            self.shown, self.at = pct, now
        elif not self.tty and pct // 10 != self.shown // 10:
            out(f"   {self.label}: {pct}% of {total / 1e9:.2f} GB")
            self.shown = pct

    def end(self):
        if self.tty and self.shown >= 0:
            sys.stdout.write("\n")
            self.shown = -1


def settings():
    """The user's settings, read from a copy so the installer never migrates or writes the real file."""
    try:
        from ai_linux.config import Settings
        from ai_linux.paths import SETTINGS_FILE
    except Exception:  # no python3-gi yet: the defaults are what a new user gets anyway
        return None
    tmp = Path(tempfile.mkdtemp(prefix="phoenix-install-"))
    try:
        if SETTINGS_FILE.is_file():
            shutil.copyfile(SETTINGS_FILE, tmp / "app.json")
        return Settings(tmp / "app.json")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def setting(key):
    s = settings()
    if s is not None:
        return s.get(key)
    from ai_linux.schema import DEFAULTS
    return DEFAULTS[key]


def small_used():
    """As the Health page: the small model counts when either profile uses it."""
    from ai_linux.schema import PROFILES, default_for
    s = settings()
    get = s.get_for if s is not None else default_for
    return any(get(p, "small_on") for p in PROFILES)


def wait(label, start):
    """Runs one of downloads' threaded calls in the foreground; Ctrl+C keeps the .part file to resume."""
    bar, result, cancel = Bar(label), {}, threading.Event()
    thread = start(bar, lambda ok, err: result.update(ok=ok, err=err), cancel, lambda fn, *a: fn(*a))
    try:
        while thread.is_alive():
            thread.join(0.3)
    except KeyboardInterrupt:
        cancel.set()
        thread.join(10)
        bar.end()
        out("   Stopped. Run the installer again to resume the download.")
        sys.exit(130)
    bar.end()
    if not result.get("ok"):
        sys.exit(f"   {label} failed: {result.get('err')}")


def plan():
    """Tab-separated lines: speech|router, model id, name, GB, installed (1/0)."""
    from ai_linux import downloads, models_catalog
    stt = models_catalog.get(str(setting("stt_engine")))
    if stt is not None and stt.runtime == "onnx":
        out(f"speech\t{stt.id}\t{stt.name}\t{stt.download_gb}\t{int(downloads.speech_installed(stt.id))}")
    small = models_catalog.get(str(setting("small_model"))) if small_used() else None
    if small is not None and small.runtime == "openvino" and downloads.openvino_published(small.id):
        done = downloads.openvino_installed(small.id, models_catalog.local_path(small))
        out(f"router\t{small.id}\t{small.name}\t{small.download_gb}\t{int(done)}")


def speech(model_id):
    from ai_linux import downloads
    if downloads.speech_installed(model_id):
        out("   Already downloaded.")
        return
    wait("Downloading", lambda p, d, c, disp: downloads.speech_download(model_id, p, d, c, dispatch=disp))
    out(f"   Saved in {downloads.SPEECH_DIR}")


def router(model_id):
    from ai_linux import downloads, models_catalog
    dest = models_catalog.local_path(models_catalog.get(model_id))
    if downloads.openvino_installed(model_id, dest):
        out("   Already downloaded.")
        return
    wait("Downloading", lambda p, d, c, disp: downloads.openvino_download(
        model_id, p, d, c, dest_dir=dest, dispatch=disp))
    out(f"   Saved in {dest}")


def _version(text):
    return tuple(int(x) for x in re.findall(r"\d+", text or "")[:3])


def ollama_check():
    """'ok|old|missing <TAB> binary <TAB> version', as the app's own search finds it."""
    from ai_linux import ollama
    binary = ollama.find_binary()
    if binary is None:
        out("missing\t\t")
        return
    version = ollama.binary_version(binary) or ""
    out(f"{'ok' if _version(version) >= MIN_OLLAMA else 'old'}\t{binary}\t{version}")


def ollama_install():
    from ai_linux import ollama
    bar, cancel = Bar("Downloading"), threading.Event()
    try:
        ok, err = ollama.install_release(bar, cancel)
    except KeyboardInterrupt:
        bar.end()
        out("   Stopped. Run the installer again to resume the download.")
        sys.exit(130)
    bar.end()
    if not ok:
        sys.exit(f"   Ollama install failed: {err}")
    out(f"   Installed in {ollama.MANAGED_DIR / ollama.OLLAMA_PIN}")


def ollama_start():
    """Starts it the way the app does: a transient user unit ai-linux-ollama on the app's own port."""
    from ai_linux import ollama

    class Ctx:
        app = None
        screenshot_mode = False

    ctx = Ctx()
    ctx.settings = settings()
    mgr = ollama.OllamaManager(ctx)
    if not ollama.is_local_url(mgr.url):
        out(f"   Your Ollama address is {mgr.url}, on another computer, so nothing to start here.")
        return
    if mgr._ping():
        out(f"   Already running at {mgr.url}")
        return
    ok, err = mgr._start_sync()
    if not ok:
        sys.exit(f"   Ollama didn't start: {err} Phoenix tries again when it opens.")
    out(f"   Running at {mgr.url} as the user unit {ollama.UNIT}")


def launcher():
    from ai_linux import autostart, desktop_entry
    for path in desktop_entry.install():
        if path.suffix == ".desktop":
            out(f"   Wrote {path}")
    out("   Installed the icons")
    if autostart.desktop_path().is_file() and autostart.is_enabled():
        autostart.set_enabled(True)  # points the start-at-login entry at the new launcher
        out("   Updated the start-at-login entry")


cmd, *args = sys.argv[1:]
{"plan": plan, "speech": speech, "router": router, "ollama-check": ollama_check, "ollama-install": ollama_install,
 "ollama-start": ollama_start, "launcher": launcher}[cmd](*args)
PY
)

# Runs the helper against the app source in $1 with the system Python.
helper() {
    local root="$1"
    shift
    PYTHONPATH="$root/src" PYTHONDONTWRITEBYTECODE=1 PATH="$BIN_DIR:$PATH" "$SYS_PY" -c "$HELPER" "$@"
}

# ---------------------------------------------------------------- checks before anything changes

# PHOENIX_ALLOW_ROOT=1 is for containers and tests, where root is the only user.
if ((EUID == 0)) && [[ "${PHOENIX_ALLOW_ROOT:-}" != 1 ]]; then
    if ((DRY)); then
        warn "running as root: a real install must run as your own user (it asks for sudo once)."
    else
        die "Run this as your own user, not with sudo. It asks for sudo once, for the system packages."
    fi
fi
[[ -f "$SRC_ROOT/src/ai_linux/__init__.py" && -x "$SRC_ROOT/bin/ai-linux-app" ]] ||
    die "Run this from a Phoenix checkout: $SRC_ROOT/src/ai_linux is missing."
[[ -f "$SRC_ROOT/requirements-engine.txt" ]] || die "$SRC_ROOT/requirements-engine.txt is missing."
[[ -x "$SYS_PY" ]] || die "$SYS_PY wasn't found. Phoenix runs on the system Python 3."

os_name="this system"
os_id="" os_version=""
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    os_name="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-$NAME}")"
    # shellcheck disable=SC1091
    os_id="$(. /etc/os-release && printf '%s' "${ID:-}")"
    # shellcheck disable=SC1091
    os_version="$(. /etc/os-release && printf '%s' "${VERSION_ID:-}")"
fi
case "$(uname -m)" in
    x86_64 | amd64 | aarch64 | arm64) ;;
    *) die "Phoenix needs a 64-bit Intel, AMD or ARM processor, not $(uname -m)." ;;
esac

sys_py_version="$(py_version "$SYS_PY")"
engine_how=""  # how the engine venv gets Python 3.14
if [[ "$sys_py_version" == "$WANT_PY" ]]; then
    engine_how="venv"
elif command -v uv >/dev/null 2>&1; then
    engine_how="uv"
fi

# ---------------------------------------------------------------- system packages

# apt name and why; installed only when missing. Optional ones are skipped when this release lacks them.
REQUIRED_PKGS=(
    "python3-gi:the app (GTK on the system Python)"
    "python3-gi-cairo:drawing the tray icon and the floating indicator"
    "gir1.2-gtk-4.0:the app window"
    "gir1.2-adw-1:the app window"
    "pipewire-bin:hearing and speaking (pw-record, pw-play)"
    "wireplumber:volume and microphone checks (wpctl)"
    "libglib2.0-bin:app actions over D-Bus (gdbus)"
    "brightnessctl:brightness commands"
    "playerctl:media commands"
    "wl-clipboard:copying and pasting"
    "libnotify-bin:timer notifications"
    "gir1.2-atspi-2.0:pasting into terminals"
)
OPTIONAL_PKGS=(
    "gir1.2-gtk4layershell-1.0:the floating indicator without Xwayland"
    "gnome-shell-extension-appindicator:the top-bar icon"
    "libsndfile1:reading sound files in the engine"
)
if [[ "$engine_how" == "venv" ]]; then
    REQUIRED_PKGS+=("python3-venv:the speech engine's Python environment")
fi

pkg_installed() {
    # shellcheck disable=SC2016
    [[ "$(dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null)" == "installed" ]]
}
pkg_available() { apt-cache show --no-all-versions "$1" >/dev/null 2>&1; }

MISSING_PKGS=() MISSING_WHY=() UNAVAILABLE=()
if ((APT)) && command -v dpkg-query >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    for entry in "${REQUIRED_PKGS[@]}" "${OPTIONAL_PKGS[@]}"; do
        name="${entry%%:*}"
        pkg_installed "$name" && continue
        if pkg_available "$name"; then
            MISSING_PKGS+=("$name")
            MISSING_WHY+=("${entry#*:}")
        else
            UNAVAILABLE+=("$entry")
        fi
    done
elif ((APT)); then
    APT=0
    warn "no apt here, so install these yourself: $(printf '%s ' "${REQUIRED_PKGS[@]%%:*}")"
fi

# ---------------------------------------------------------------- the plan

models_plan=""
if ((MODELS)); then
    models_plan="$(helper "$SRC_ROOT" plan 2>/dev/null || true)"
fi

say "Phoenix installer"
say "  From:   $SRC_ROOT"
say "  System: $os_name, $(uname -m), system Python ${sys_py_version:-not found}"
[[ "$os_id" == "ubuntu" && "$os_version" == "26.04" ]] ||
    warn "Phoenix is made for Ubuntu 26.04 GNOME. On $os_name some parts may not work."
((DRY)) && say "  Dry run: nothing is changed."

say ""
say "This will:"
if ((APT)) && ((${#MISSING_PKGS[@]})); then
    say "  1. Install system packages with sudo (one step):"
    for i in "${!MISSING_PKGS[@]}"; do
        printf '       %-36s %s\n' "${MISSING_PKGS[$i]}" "${MISSING_WHY[$i]}"
    done
elif ((APT)); then
    say "  1. System packages: all installed already"
else
    say "  1. System packages: skipped (--skip-apt)"
fi
if ((LINK)); then
    say "  2. Run the app from this folder, so keep it ($SRC_ROOT)"
else
    say "  2. Copy the app to $COPY_ROOT"
fi
say "  3. Set up the speech engine's Python $WANT_PY environment in $VENV"
say "  4. Add Phoenix to the app grid and $LAUNCHER"
if ((OLLAMA)); then
    say "  5. Install Phoenix's own Ollama (about 1.5 GB) unless 0.40 or newer is there, and start it on port 11435"
else
    say "  5. Ollama: skipped (--no-ollama)"
fi
if ((MODELS)); then
    say "  6. Download models:"
    while IFS=$'\t' read -r kind _id name gb have; do
        [[ -n "$kind" ]] || continue
        label="speech recognition"
        [[ "$kind" == "router" ]] && label="small router model"
        if [[ "$have" == "1" ]]; then
            printf '       %-24s %s, already downloaded\n' "$label" "$name"
        else
            printf '       %-24s %s, %s GB\n' "$label" "$name" "$gb"
        fi
    done <<<"$models_plan"
    say "       voices                   SuperTonic 3, through its own library (Hugging Face)"
    say "     The big model is your choice on the Models page."
else
    say "  6. Models: skipped (--no-models)"
fi
say ""

if ((!DRY)); then
    confirm "Go ahead?" || die "Nothing was changed."
fi

# ---------------------------------------------------------------- 1. system packages

if ((APT)); then
    step "1. System packages"
    for entry in "${UNAVAILABLE[@]}"; do
        if [[ " ${REQUIRED_PKGS[*]%%:*} " == *" ${entry%%:*} "* ]]; then
            warn "${entry%%:*} isn't in your package sources, so ${entry#*:} may not work."
        else
            note "Skipping ${entry%%:*}: not in your package sources (optional, ${entry#*:})."
        fi
    done
    if ((${#MISSING_PKGS[@]})); then
        as_root=()
        ((EUID == 0)) || as_root=(sudo)
        # One sudo prompt for both apt calls.
        # shellcheck disable=SC2016
        run "${as_root[@]}" sh -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"' \
            sh "${MISSING_PKGS[@]}"
    else
        note "All installed."
    fi
fi

# ---------------------------------------------------------------- 2. the app

step "2. The app"
if ((LINK)); then
    note "Using this folder: $SRC_ROOT"
    if [[ -d "$COPY_ROOT" ]]; then
        note "Removing the earlier copy, now unused: $COPY_ROOT"
        run rm -rf -- "$COPY_ROOT"
    fi
else
    parts=(src/ai_linux bin)
    for extra in LICENSE LICENSES VERSION requirements-engine.txt scripts/uninstall.sh; do
        [[ -e "$SRC_ROOT/$extra" ]] && parts+=("$extra")
    done
    if ((DRY)); then
        note "would copy ${parts[*]} into $COPY_ROOT (replacing an earlier copy)"
    else
        mkdir -p "$DATA"
        rm -rf -- "$COPY_ROOT.new" "$COPY_ROOT.old"
        mkdir "$COPY_ROOT.new"
        tar -C "$SRC_ROOT" --exclude=__pycache__ --exclude='*.pyc' -cf - "${parts[@]}" | tar -C "$COPY_ROOT.new" -xf -
        # Swap in one rename, so a failed copy never leaves a half-installed app.
        [[ -d "$COPY_ROOT" ]] && mv -- "$COPY_ROOT" "$COPY_ROOT.old"
        mv -- "$COPY_ROOT.new" "$COPY_ROOT"
        rm -rf -- "$COPY_ROOT.old"
        note "Copied to $COPY_ROOT"
    fi
fi
if pgrep -u "$(id -u)" -f -- '-m ai_linux( |$)' >/dev/null 2>&1; then
    note "Phoenix is running: quit and reopen it to use this version."
fi

# ---------------------------------------------------------------- 3. engine environment

step "3. Speech engine environment"
req="$SRC_ROOT/requirements-engine.txt"
req_sum="$(sha256sum "$req" | cut -d' ' -f1)"
stamp="$VENV/.phoenix-requirements"
venv_version="$(py_version "$VENV/bin/python")"
if [[ -n "$venv_version" && "$venv_version" != "$WANT_PY" ]]; then
    old="$VENV.old-$(date +%Y%m%d-%H%M%S)"
    note "The environment has Python $venv_version, not $WANT_PY: moving it to $old (delete it when you like)"
    run mv -- "$VENV" "$old"
    venv_version=""
fi
if [[ -n "$venv_version" && -f "$stamp" && "$(cat "$stamp")" == "$req_sum" ]]; then
    note "Up to date."
else
    pip_how="pip"
    if [[ -z "$venv_version" ]]; then
        case "$engine_how" in
            venv) run "$SYS_PY" -m venv "$VENV" ;;
            uv) run uv venv --python "$WANT_PY" "$VENV"; pip_how="uv" ;;
            *) die "The engine needs Python $WANT_PY. The system Python here is ${sys_py_version:-missing}: install uv
       (https://docs.astral.sh/uv/) and run this again, and it fetches Python $WANT_PY for the engine." ;;
        esac
    elif ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1 && command -v uv >/dev/null 2>&1; then
        pip_how="uv"  # made by uv, which leaves pip out
    fi
    if [[ "$pip_how" == "uv" ]]; then
        run uv pip install --python "$VENV/bin/python" -r "$req"
    else
        run "$VENV/bin/python" -m pip install --disable-pip-version-check -q -r "$req"
    fi
    if ((DRY)); then
        note "would note the installed requirements in $stamp"
    else
        printf '%s\n' "$req_sum" >"$stamp"
        note "Ready: $VENV"
    fi
fi

# ---------------------------------------------------------------- 4. launcher

step "4. Launcher"
if [[ -e "$LAUNCHER" && ! -L "$LAUNCHER" ]]; then
    die "$LAUNCHER exists and isn't Phoenix's link. Move it away and run this again."
fi
run mkdir -p "$BIN_DIR"
run ln -sfn -- "$APP_ROOT/bin/ai-linux-app" "$LAUNCHER"
if ((DRY)); then
    note "would add the app-grid entry and icons (python3 -m ai_linux.desktop_entry install)"
else
    helper "$APP_ROOT" launcher
fi
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) note "$BIN_DIR joins your PATH at your next login, so 'ai-linux-app' works in a terminal then." ;;
esac

# ---------------------------------------------------------------- 5. Ollama

step "5. Ollama"
if ((!OLLAMA)); then
    note "Skipped (--no-ollama)."
else
    check="$(helper "$APP_ROOT" ollama-check 2>/dev/null || true)"
    [[ -n "$check" ]] || check="$(helper "$SRC_ROOT" ollama-check 2>/dev/null || true)"
    IFS=$'\t' read -r o_state o_bin o_version <<<"$check" || true
    case "${o_state:-unknown}" in
        ok) note "Found Ollama $o_version at $o_bin" ;;
        unknown)
            if ((DRY)); then
                note "would check for Ollama 0.40 or newer and install the pinned release if needed"
            else
                warn "couldn't check for Ollama (is python3-gi installed?). Install it on the Models page."
                OLLAMA=0
            fi
            ;;
        old | missing)
            [[ "${o_state:-}" == "old" ]] && note "Ollama $o_version at $o_bin is older than 0.40."
            if ((DRY)); then
                note "would download, check and unpack the pinned Ollama release into $DATA/ollama"
            elif [[ "$sys_py_version" != "$WANT_PY" ]]; then
                warn "unpacking Ollama needs Python $WANT_PY as the system Python. Install Ollama 0.40 or newer"
                warn "yourself (https://ollama.com); Phoenix runs it on port 11435 on its own."
                OLLAMA=0
            else
                helper "$APP_ROOT" ollama-install
            fi
            ;;
    esac
    if ((OLLAMA)); then
        if ((DRY)); then
            note "would start it as the user unit ai-linux-ollama on 127.0.0.1:11435"
            note "(a system ollama.service is never touched)"
        elif systemctl --user show-environment >/dev/null 2>&1; then
            helper "$APP_ROOT" ollama-start || warn "Ollama didn't start now; Phoenix starts it when it opens."
        else
            note "No user session manager here, so Phoenix starts Ollama itself when it opens."
        fi
    fi
fi

# ---------------------------------------------------------------- 6. models

step "6. Models"
if ((!MODELS)); then
    note "Skipped (--no-models)."
else
    models_plan="$(helper "$APP_ROOT" plan 2>/dev/null || helper "$SRC_ROOT" plan)"
    while IFS=$'\t' read -r kind id name gb have; do
        [[ -n "$kind" ]] || continue
        if [[ "$kind" == "speech" ]]; then note "Speech recognition: $name"; else note "Small router model: $name"; fi
        if [[ "$have" == "1" ]]; then
            note "Already downloaded."
        elif ((DRY)); then
            note "would download $gb GB (checked with SHA-256, resumes if interrupted)"
        else
            helper "$APP_ROOT" "$kind" "$id"
        fi
    done <<<"$models_plan"
    note "Voices: SuperTonic 3"
    if ((DRY)); then
        note "would load the voices once in the engine's environment, which downloads them to ~/.cache/supertonic3"
    else
        if (cd "$DATA" && PYTHONPATH="$APP_ROOT/src" "$VENV/bin/python" -c \
            'from ai_linux.engine.tts import SpeechSynthesizer; SpeechSynthesizer()'); then
            note "Ready."
        else
            warn "the voices didn't load now; Phoenix downloads them when it first speaks."
        fi
    fi
fi

# ---------------------------------------------------------------- done

if ((DRY)); then
    printf '\nDry run finished: nothing was changed.\n'
    exit 0
fi
cat <<EOF

Phoenix is installed.
  Open it from the app grid (search "Phoenix"), or run: $LAUNCHER
  The first start walks you through the microphone and the listening mode.
  Pick and download the big model on the Models page. The Health page shows anything still missing.
  To remove Phoenix: $APP_ROOT/scripts/uninstall.sh (keeps your settings and history unless --purge)
EOF
