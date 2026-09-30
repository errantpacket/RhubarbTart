"""Follow herdr's colour theme when the TUI runs inside herdr (#131).

herdr keeps its theme in ``config.toml`` under ``[theme]``: a built-in ``name`` (default
``catppuccin``), or ``dark_name`` / ``light_name`` with ``auto_switch = true`` to follow the host's
appearance, plus ``[theme.custom]`` colour tokens layered on top (and ``[theme.custom.dark]`` /
``[theme.custom.light]`` when ``auto_switch`` is on). This module turns that into a Textual theme:

* herdr built-ins map to the Textual built-in of the same palette where one exists;
* the rest are defined here from their upstream palettes (``EXTRA_THEMES``);
* herdr's ``terminal`` theme maps to Textual's ANSI themes, which use the terminal's own colours;
* ``[theme.custom]`` tokens override the matching Textual colours (``TOKEN_MAP``).

Everything here is pure (config dict in, theme out) except the two readers at the bottom, and a
missing or malformed config always falls back to herdr's default rather than raising. Only the
TUI's look depends on it; it never touches ``tart``, the keychain or the core API.
"""

import dataclasses
import subprocess
import sys
from pathlib import Path

from textual.color import Color
from textual.theme import BUILTIN_THEMES, Theme

HERDR_DEFAULT = "catppuccin"
HERDR_DEFAULT_LIGHT = "catppuccin-latte"
THEME_NAME = "herdr"   # the name the synced theme is registered under

# herdr built-in -> Textual built-in with the same palette.
BUILTIN_MAP = {
    "catppuccin": "catppuccin-mocha",
    "catppuccin-latte": "catppuccin-latte",
    "tokyo-night": "tokyo-night",
    "dracula": "dracula",
    "nord": "nord",
    "gruvbox": "gruvbox",
    "one-dark": "atom-one-dark",
    "one-light": "atom-one-light",
    "solarized": "solarized-dark",
    "solarized-light": "solarized-light",
    "rose-pine": "rose-pine",
    "rose-pine-dawn": "rose-pine-dawn",
}

# herdr built-ins Textual lacks, from each theme's upstream palette.
EXTRA_THEMES = {
    "tokyo-night-day": Theme(
        name="tokyo-night-day", dark=False, primary="#2e7de9", secondary="#9854f1",
        accent="#007197", warning="#8c6c3e", error="#f52a65", success="#587539",
        foreground="#3760bf", background="#e1e2e7", surface="#d0d5e3", panel="#c4c8da"),
    "gruvbox-light": Theme(
        name="gruvbox-light", dark=False, primary="#076678", secondary="#8f3f71",
        accent="#af3a03", warning="#b57614", error="#9d0006", success="#79740e",
        foreground="#3c3836", background="#fbf1c7", surface="#ebdbb2", panel="#d5c4a1"),
    "kanagawa": Theme(
        name="kanagawa", dark=True, primary="#7e9cd8", secondary="#957fb8",
        accent="#ffa066", warning="#e6c384", error="#e82424", success="#98bb6c",
        foreground="#dcd7ba", background="#1f1f28", surface="#2a2a37", panel="#363646"),
    "kanagawa-lotus": Theme(
        name="kanagawa-lotus", dark=False, primary="#4d699b", secondary="#624c83",
        accent="#cc6d00", warning="#77713f", error="#c84053", success="#6f894e",
        foreground="#545464", background="#f2ecbc", surface="#e5ddb0", panel="#dcd5ac"),
    "vesper": Theme(
        name="vesper", dark=True, primary="#ffc799", secondary="#a0a0a0",
        accent="#99ffe4", warning="#ffc799", error="#ff8080", success="#99ffe4",
        foreground="#ffffff", background="#101010", surface="#1c1c1c", panel="#232323"),
}

# herdr [theme.custom] token -> Textual Theme field.
TOKEN_MAP = {
    "accent": "primary",
    "text": "foreground",
    "panel_bg": "background",
    "surface0": "surface",
    "surface1": "panel",
    "red": "error",
    "green": "success",
    "yellow": "warning",
}


def herdr_theme_name(config: dict, appearance: str = "dark") -> str:
    """The herdr theme in effect for ``appearance`` ("dark" or "light")."""
    theme = config.get("theme") if isinstance(config.get("theme"), dict) else {}
    if theme.get("auto_switch") is True:
        key, default = (("light_name", HERDR_DEFAULT_LIGHT) if appearance == "light"
                        else ("dark_name", HERDR_DEFAULT))
        name = theme.get(key, default)
    else:
        name = theme.get("name", HERDR_DEFAULT)
    return name if isinstance(name, str) and name else HERDR_DEFAULT


def herdr_overrides(config: dict, appearance: str = "dark") -> dict[str, str]:
    """Textual colour overrides from ``[theme.custom]`` (plus the appearance layer when
    ``auto_switch`` is on). Unknown tokens, ``reset`` and colours Textual can't parse are skipped."""
    theme = config.get("theme") if isinstance(config.get("theme"), dict) else {}
    custom = theme.get("custom") if isinstance(theme.get("custom"), dict) else {}
    layers = [custom]
    if theme.get("auto_switch") is True and isinstance(custom.get(appearance), dict):
        layers.append(custom[appearance])
    out: dict[str, str] = {}
    for layer in layers:
        for token, value in layer.items():
            field = TOKEN_MAP.get(token)
            if not field or not isinstance(value, str) or value.strip().lower() == "reset":
                continue
            try:
                Color.parse(value)
            except Exception:
                continue
            out[field] = value
    return out


def textual_theme(config: dict, appearance: str = "dark") -> Theme:
    """The Textual theme matching herdr's, registered as ``THEME_NAME``."""
    name = herdr_theme_name(config, appearance)
    if name == "terminal":
        base = BUILTIN_THEMES["ansi-light" if appearance == "light" else "ansi-dark"]
    elif name in EXTRA_THEMES:
        base = EXTRA_THEMES[name]
    else:
        base = BUILTIN_THEMES[BUILTIN_MAP.get(name, BUILTIN_MAP[HERDR_DEFAULT])]
    return dataclasses.replace(base, name=THEME_NAME, **herdr_overrides(config, appearance))


# -- readers (the only I/O) ------------------------------------------------------------------


def herdr_config_path(env) -> Path:
    """Where herdr reads its config: HERDR_CONFIG_PATH, then $XDG_CONFIG_HOME/herdr, then ~/.config."""
    if env.get("HERDR_CONFIG_PATH"):
        return Path(env["HERDR_CONFIG_PATH"]).expanduser()
    base = Path(env["XDG_CONFIG_HOME"]).expanduser() if env.get("XDG_CONFIG_HOME") \
        else Path.home() / ".config"
    return base / "herdr" / "config.toml"


def load_herdr_config(env) -> dict:
    """herdr's config as a dict; ``{}`` (herdr's defaults) if it is missing or unreadable."""
    import tomllib

    try:
        with open(herdr_config_path(env), "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def host_appearance() -> str:
    """"light" or "dark" from the macOS appearance setting; "dark" anywhere else or on error.
    herdr follows the host terminal's appearance, which normally follows this setting."""
    if sys.platform != "darwin":
        return "dark"
    try:
        res = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return "dark"
    return "dark" if res.stdout.strip().lower() == "dark" else "light"


def choose_theme(argv: list[str], env) -> Theme | str | None:
    """What the TUI should look like: ``--theme NAME`` or ``RHUBARB_TUI_THEME`` (any Textual
    theme name) wins; inside herdr (``HERDR_PANE_ID`` set) the synced herdr theme; else ``None``
    (Textual's default)."""
    for i, arg in enumerate(argv):
        if arg == "--theme" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--theme="):
            return arg.split("=", 1)[1]
    if env.get("RHUBARB_TUI_THEME"):
        return env["RHUBARB_TUI_THEME"]
    if not env.get("HERDR_PANE_ID"):
        return None
    config = load_herdr_config(env)
    theme = config.get("theme") if isinstance(config.get("theme"), dict) else {}
    appearance = host_appearance() if theme.get("auto_switch") is True else "dark"
    if theme.get("name") == "terminal" and theme.get("auto_switch") is not True:
        appearance = host_appearance()   # ANSI themes: match the terminal's light/dark
    return textual_theme(config, appearance)
