"""Every setting, its default and its allowed values, plus the two power profiles. gi-free: the engine imports it.

Keys in PROFILE_KEYS have one value for "battery" and one for "charger". "profile" picks which applies:
auto follows the charger (a machine without a battery counts as on the charger), or force either one.
"""

from __future__ import annotations

from pathlib import Path

DEFAULTS: dict[str, object] = {
    # listening
    "listen_mode": "wake",  # wake | hotkey | always | off
    "wake_word": "phoenix",  # also the assistant's name
    "hotkey": "<Super><Alt>space",
    "hold_to_talk": False,
    "barge_in": True,
    "start_ping": True,
    "stt_engine": "parakeet-tdt",  # parakeet-tdt | parakeet-ctc
    "addressed_check": False,  # always-listening: drop speech the small model judges isn't for the assistant
    "mic_device": "",  # empty = system default
    "muted": False,  # the tray's Mute: the microphone stays closed whichever profile is in use
    # about you: added to the big model's instructions, never sent anywhere else
    "user_name": "",
    "about_me": "",
    # models
    "profile": "auto",  # auto | battery | charger
    "small_model": "jiwo-0.8b",  # a small-slot model id, or "none" (the big model picks the command)
    "small_device": "GPU",  # GPU (Intel, via OpenVINO) | CPU
    "big_model": "qwen3.5:4b",
    "big_device": "gpu",  # gpu | cpu (Ollama num_gpu 0: slow, but keeps the graphics card free)
    "big_model_policy": "on_demand",  # on_demand | instant (kept loaded)
    "escalate_threshold": 0.85,
    "think": False,
    "context_length": 8192,  # tokens the big model reads per request (history, tools, screenshot)
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_keep_alive": "2m",
    # voice output
    "tts_enabled": True,
    "voice": "supertonic:M1",
    "tts_speed": 1.0,
    "tts_volume": 1.0,
    "action_feedback": "sound",  # sound: a finished action plays a chime, failures are spoken | speech: always spoken
    "big_cue": True,  # a chime when the big model takes over a request
    # safety
    "actions_armed": True,
    "confirm_shell": True,
    "spoken_confirm": True,  # a pending confirmation can also be answered by saying yes or no
    # desktop
    "show_pill": True,
    "pill_always": False,
    "pill_position": "bottom",  # bottom | top
    "theme": "light",  # light | system
    "autostart": True,
    "keep_running": True,
    "history_retention_days": 90,
    "mcp_servers": [],  # [{"name", "command", "args": [...], "enabled": bool}]
    "onboarding_done": False,
}

CHOICES: dict[str, tuple] = {
    "listen_mode": ("wake", "hotkey", "always", "off"),
    "stt_engine": ("parakeet-tdt", "parakeet-ctc"),
    "big_model_policy": ("on_demand", "instant"),
    "profile": ("auto", "battery", "charger"),
    "small_device": ("GPU", "CPU"),
    "big_device": ("gpu", "cpu"),
    "action_feedback": ("sound", "speech"),
    "pill_position": ("bottom", "top"),
    "theme": ("light", "system"),
}

PROFILES = ("battery", "charger")
PROFILE_KEYS = (
    # models and devices
    "small_model", "small_device", "big_model", "big_device", "think",
    # loading
    "big_model_policy", "ollama_keep_alive", "escalate_threshold",
    # listening
    "listen_mode", "stt_engine", "addressed_check",
    # voice and extras
    "tts_enabled", "voice", "tts_speed", "tts_volume", "action_feedback", "big_cue",
    "show_pill", "pill_always", "pill_position",
)
# Where a profile differs from DEFAULTS. On the charger everything runs on the big model.
PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "battery": {},
    "charger": {"small_model": "none", "ollama_keep_alive": "30m"},
}


def default_for(profile: str, key: str):
    return PROFILE_DEFAULTS.get(profile, {}).get(key, DEFAULTS[key])


def on_ac_power(root: Path = Path("/sys/class/power_supply")) -> bool:
    """True on the charger, or on a machine with no battery (a desktop)."""
    batteries, mains = False, False
    for d in root.glob("*"):
        try:
            kind = (d / "type").read_text().strip()
            if kind == "Battery":
                batteries = True
            elif kind == "Mains" and (d / "online").read_text().strip() == "1":
                mains = True
        except OSError:
            continue
    return mains or not batteries
