"""Persistent JSON configuration for the auto clicker.

Stored at:
  Linux:   $XDG_CONFIG_HOME/autoclicker/config.json (default ~/.config/autoclicker/config.json)
  Windows: %APPDATA%\\AutoClicker\\config.json
  other:   ~/.autoclicker/config.json
"""

import json
import os
import platform
import sys

DEFAULT_HOTKEYS = {
    "start": "f1",
    "stop": "f2",
    "toggle": "f3",
    "add_slot": "f4",
}

DEFAULTS = {
    "cps": 10.0,
    "mode": "infinite",          # "infinite" | "amount" | "duration"
    "amount": 100,               # total clicks when mode == "amount"
    "duration_seconds": 60.0,    # run time when mode == "duration"
    "hotkeys": DEFAULT_HOTKEYS,
    "slots": [],                 # [[x, y], ...] persisted between runs
    "click_backend": "auto",     # "auto" | "pyautogui" | "ydotool"
    "capture_backend": "auto",   # "auto" | "pynput" | "slurp" | "hyprctl"
    "hotkey_backend": "auto",    # "auto" | "evdev" | "pynput"
}


def config_dir() -> str:
    system = platform.system()

    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "AutoClicker")

    if system == "Linux":
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return os.path.join(base, "autoclicker")

    return os.path.expanduser("~/.autoclicker")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def load() -> dict:
    path = config_path()

    data = json.loads(json.dumps(DEFAULTS))  # deep copy

    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            data.update({k: v for k, v in saved.items() if k in DEFAULTS})

            if isinstance(saved.get("hotkeys"), dict):
                merged_hotkeys = dict(DEFAULT_HOTKEYS)
                merged_hotkeys.update(saved["hotkeys"])
                data["hotkeys"] = merged_hotkeys

        except (json.JSONDecodeError, OSError) as e:
            print(f"[config] could not read {path}: {e}", file=sys.stderr)

    return data


def save(data: dict) -> None:
    d = config_dir()
    os.makedirs(d, exist_ok=True)

    path = config_path()
    tmp_path = path + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    os.replace(tmp_path, path)
