#! python3
# JSON preset save/load for pattern parameters (target/attractor references excluded).
#
# Presets live as <name>.json files in the user's roaming folder under
# SurfacePattern/presets/. Three built-ins ship in code; a saved file with the
# same name shadows its built-in.

import json
import os
import sys

# Session-tied or unserializable keys that never belong in a preset.
EXCLUDED_KEYS = {"projection_plane"}

BUILTIN_PRESETS = {
    "Uniform Perforation": {
        "pattern_mode": "grid",
        "shape": "circle",
        "size": 4.0,
        "spacing_x": 6.0,
        "spacing_y": 6.0,
        "grid_type": "square",
        "rotation": 0.0,
        "jitter_position": 0.0,
        "jitter_size": 0.0,
        "jitter_rotation": 0.0,
        "seed": 0,
    },
    "Center Fade Grille": {
        "pattern_mode": "halftone",
        "shape": "circle",
        "spacing_x": 4.0,
        "spacing_y": 4.0,
        "grid_type": "staggered",
        "halftone_profile": "smooth",
        "halftone_invert": False,
        "halftone_radius": 80.0,
        "halftone_size_min": 0.8,
        "halftone_size_max": 5.0,
        "halftone_cull": 0.5,
        "rotation": 0.0,
        "jitter_position": 0.0,
        "jitter_size": 0.0,
        "jitter_rotation": 0.0,
        "seed": 0,
    },
    "Hex Mesh": {
        "pattern_mode": "grid",
        "shape": "hex",
        "size": 8.0,
        "spacing_x": 1.5,
        "spacing_y": 1.5,
        "grid_type": "triangular",
        "rotation": 0.0,
        "jitter_position": 0.0,
        "jitter_size": 0.0,
        "jitter_rotation": 0.0,
        "seed": 0,
    },
}


def preset_dir():
    """Roaming preset folder (created on demand), per platform."""
    if os.name == "nt":
        base = os.getenv("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    path = os.path.join(base, "SurfacePattern", "presets")
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(name):
    """Filesystem-safe preset name (also used as the file stem)."""
    cleaned = "".join(
        ch for ch in name.strip() if ch.isalnum() or ch in (" ", "-", "_")
    ).strip()
    return cleaned or "preset"


def list_presets():
    """Built-in preset names plus saved files, built-ins first, no duplicates."""
    names = list(BUILTIN_PRESETS)
    try:
        files = sorted(os.listdir(preset_dir()))
    except OSError:
        files = []
    for filename in files:
        if not filename.endswith(".json"):
            continue
        stem = filename[:-5]
        if stem not in names:
            names.append(stem)
    return names


def load_preset(name):
    """Parameter dict for the named preset; saved files shadow built-ins. None when unreadable."""
    path = os.path.join(preset_dir(), name + ".json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as stream:
                loaded = json.load(stream)
        except (OSError, ValueError):
            return None
        if not isinstance(loaded, dict):
            return None
        return {key: value for key, value in loaded.items() if key not in EXCLUDED_KEYS}
    preset = BUILTIN_PRESETS.get(name)
    return dict(preset) if preset is not None else None


def save_preset(name, params):
    """Write params (minus excluded/unserializable keys) as <name>.json; returns the path."""
    filtered = {}
    for key, value in params.items():
        if key in EXCLUDED_KEYS:
            continue
        try:
            json.dumps(value)
        except TypeError:
            continue
        filtered[key] = value
    path = os.path.join(preset_dir(), _safe_name(name) + ".json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(filtered, stream, indent=2, sort_keys=True)
    return path
