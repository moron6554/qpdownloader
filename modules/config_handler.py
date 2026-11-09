"""
config_handler.py — Reads and writes config.ini settings.
"""

import configparser
import os
import sys

if getattr(sys, 'frozen', False):
    EXECUTABLE_DIR = os.path.dirname(sys.executable)
else:
    EXECUTABLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(EXECUTABLE_DIR, "config.ini")

_DEFAULTS = {
    "remove_blank":       "N",
    "remove_additional":  "N",
    "remove_formula":     "N",
    "download_directory": os.path.join(os.path.expanduser("~"), "Downloads"),
    "last_download_directory": os.path.join(os.path.expanduser("~"), "Downloads"),
}


def init_config() -> None:
    """Create config.ini with defaults if it does not exist."""
    if not os.path.exists(CONFIG_PATH):
        print("[Config] No config.ini found — creating with defaults.")
        cfg = configparser.ConfigParser()
        cfg["CompileSettings"] = _DEFAULTS
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            cfg.write(f)


def fetch_from_config(item: str) -> str:
    """Return a single setting value; creates the file if missing."""
    init_config()
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    return cfg.get("CompileSettings", item, fallback=_DEFAULTS.get(item, ""))


def save_to_config(item: str, value: str) -> None:
    """Persist a single setting value."""
    init_config()
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    if "CompileSettings" not in cfg:
        cfg["CompileSettings"] = {}
    cfg["CompileSettings"][item] = value
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        cfg.write(f)
