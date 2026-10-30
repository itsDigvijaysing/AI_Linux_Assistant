"""Every setting, its default and its allowed values, plus the two power profiles. gi-free: the engine imports it.

Keys in PROFILE_KEYS have one value for "battery" and one for "charger". "profile" picks which applies:
auto follows the charger (a machine without a battery counts as on the charger), or force either one.
The models are one choice for both; a profile only sets the speed and whether the small model is used.
"""

from __future__ import annotations

from pathlib import Path

# Speed: (id, label, what it does). How long the big model stays in GPU memory.
SPEEDS = (
    ("efficient", "Save Battery", "The big model loads when a request needs it and frees the GPU 2 minutes "
                                  "after."),
    ("auto", "Automatic", "It loads when needed and stays 30 minutes, so follow-ups answer at once."),
    ("full", "Max Performance", "It stays loaded on the NVIDIA GPU from the start: the fastest answers, the "
                                "most power."),
)
SPEED_LABELS = {i: label for i, label, _ in SPEEDS}

DEFAULTS: dict[str, object] = {
    # listening
    "listen_mode": "wake",  # wake | hotkey | always | off
    "wake_word": "phoenix",  # also the assistant's name
    "hotkey": "<Super><Alt>space",
    "hold_to_talk": False,
    "barge_in": True,
    "start_ping": True,
    "stt_engine": "parakeet-tdt",  # parakeet-tdt | parakeet-ctc
    "stt_device": "intel-gpu",  # intel-gpu (TDT's encoder via OpenVINO, falls back to cpu) | cpu
    "live_text": True,  # your words in the pill and the window while you speak, and the reply in the pill
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
    "speed": "efficient",  # per profile, see SPEEDS; the engine gets big_model_policy and ollama_keep_alive from it
    "small_on": True,  # per profile: the small model picks the command (off: the big model does)
    "big_model_policy": "on_demand",  # engine value: on_demand | instant (kept loaded), from speed
    "escalate_threshold": 0.85,
    "think": False,
    "context_length": 8192,  # tokens the big model reads per request (history, tools, screenshot)
    "ollama_url": "http://127.0.0.1:11435",  # Phoenix's own server (see ollama.DEFAULT_URL)
    "ollama_keep_alive": "2m",  # engine value, from speed
    # voice output
    "tts_enabled": True,
    "voice": "supertonic:F1",  # Phoenix speaks with a female voice by default
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
    "theme": "light",  # light | dark | system
    "autostart": True,
    "keep_running": True,
    "history_retention_days": 90,
    "mcp_servers": [],  # [{"name", "command", "args": [...], "enabled": bool}]
    "web_access": False,  # weather and quick look-ups reach the internet only when this is on
    "weather_city": "",  # the city for "what's the weather" when none is named
    "todoist_connected": False,  # a Todoist token is in the keyring (the token itself never is a setting)
    "onboarding_done": False,
}

CHOICES: dict[str, tuple] = {
    "listen_mode": ("wake", "hotkey", "always", "off"),
    "stt_engine": ("parakeet-tdt", "parakeet-ctc"),
    "stt_device": ("intel-gpu", "cpu"),
    "big_model_policy": ("on_demand", "instant"),
    "speed": tuple(i for i, _, _ in SPEEDS),
    "profile": ("auto", "battery", "charger"),
    "small_device": ("GPU", "CPU"),
    "big_device": ("gpu", "cpu"),
    "action_feedback": ("sound", "speech"),
    "pill_position": ("bottom", "top"),
    "theme": ("light", "dark", "system"),
}

PROFILES = ("battery", "charger")
PROFILE_KEYS = (
    # models: how fast, and whether the small model picks commands (the models themselves are one choice)
    "speed", "small_on",
    # listening
    "listen_mode", "addressed_check",
    # voice and extras
    "tts_enabled", "voice", "tts_speed", "tts_volume", "action_feedback", "big_cue",
    "show_pill", "pill_always", "pill_position",
)
# State details the engine sends with "thinking" while the big model works; the bars and pill glow for them.
BIG_WAKING, BIG_WORKING = "Waking the big model", "The big model is answering"


def is_big(detail: str) -> bool:
    return detail in (BIG_WAKING, BIG_WORKING)


# Once per profile, now one value for both (config.py moves stored values).
SHARED_NOW = ("small_model", "small_device", "big_model", "big_device", "think", "escalate_threshold", "stt_engine",
              "stt_device")


def loading(speed: str) -> dict[str, str]:
    """What the engine gets for a speed: keep the big model loaded or not, and for how long after a reply."""
    return {"big_model_policy": "instant" if speed == "full" else "on_demand",
            "ollama_keep_alive": "2m" if speed == "efficient" else "30m"}


# Where a profile differs from DEFAULTS. On the charger the big model picks commands too (more accurate).
PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "battery": {},
    "charger": {"speed": "auto", "small_on": False},
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
