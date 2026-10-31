"""Theme tokens: the terminal's own colors by default, or Herdr's theme when one is configured.

Colors are "#rrggbb", an ANSI palette index (named colors, like Herdr's), None for the
terminal default, or "dim" for the terminal's text, dimmed. Overrides use Herdr's
[theme.custom] token names.
"""

import os
import re
import subprocess
import tomllib
from pathlib import Path

# Herdr's token names. The picker draws with a subset; keeping all of them means any
# [theme.custom] override written for Herdr applies here unchanged.
TOKENS = ("panel_bg", "sidebar_bg", "active_row_bg", "selection_bg", "surface0", "surface1", "surface_dim",
          "overlay0", "overlay1", "text", "subtext0", "mauve", "green", "yellow", "red", "blue", "teal", "peach",
          "accent")


def palette(*colors):
    return dict(zip(TOKENS, colors))


THEMES = {
    "catppuccin": palette("#1e1e2e", "#181825", "#1e1e2e", "#313244", "#313244", "#45475a", "#11111b", "#6c7086",
                          "#7f849c", "#cdd6f4", "#a6adc8", "#cba6f7", "#a6e3a1", "#f9e2af", "#f38ba8", "#89b4fa",
                          "#94e2d5", "#fab387", "#89b4fa"),
    "catppuccin-latte": palette("#eff1f5", "#e6e9ef", "#eff1f5", "#ccd0da", "#ccd0da", "#bcc0cc", "#dce0e8",
                                "#9ca0b0", "#8c8fa1", "#4c4f69", "#6c6f85", "#8839ef", "#40a02b", "#df8e1d",
                                "#d20f39", "#1e66f5", "#179299", "#fe640b", "#1e66f5"),
    "tokyo-night": palette("#1a1b26", "#16161e", "#1a1b26", "#283457", "#292e42", "#3b4261", "#16161e", "#565f89",
                           "#737aa2", "#c0caf5", "#a9b1d6", "#bb9af7", "#9ece6a", "#e0af68", "#f7768e", "#7aa2f7",
                           "#73daca", "#ff9e64", "#7aa2f7"),
    "tokyo-night-day": palette("#e1e2e7", "#d0d5e3", "#e1e2e7", "#b7c1e3", "#c4c8da", "#a8aecb", "#d0d5e3",
                               "#848cb5", "#6172b0", "#3760bf", "#6172b0", "#9854f1", "#587539", "#8c6c3e",
                               "#f52a65", "#2e7de9", "#118c74", "#b15c00", "#2e7de9"),
    "dracula": palette("#282a36", "#21222c", "#282a36", "#44475a", "#343746", "#44475a", "#191a21", "#6272a4",
                       "#7b88b8", "#f8f8f2", "#c8c8d8", "#bd93f9", "#50fa7b", "#f1fa8c", "#ff5555", "#8be9fd",
                       "#8be9fd", "#ffb86c", "#bd93f9"),
    "nord": palette("#2e3440", "#272c36", "#2e3440", "#434c5e", "#3b4252", "#434c5e", "#242933", "#616e88",
                    "#7b88a1", "#eceff4", "#d8dee9", "#b48ead", "#a3be8c", "#ebcb8b", "#bf616a", "#81a1c1",
                    "#8fbcbb", "#d08770", "#88c0d0"),
    "gruvbox": palette("#282828", "#1d2021", "#282828", "#3c3836", "#3c3836", "#504945", "#1d2021", "#7c6f64",
                       "#928374", "#ebdbb2", "#d5c4a1", "#d3869b", "#b8bb26", "#fabd2f", "#fb4934", "#83a598",
                       "#8ec07c", "#fe8019", "#83a598"),
    "gruvbox-light": palette("#fbf1c7", "#f2e5bc", "#fbf1c7", "#ebdbb2", "#ebdbb2", "#d5c4a1", "#f2e5bc",
                             "#a89984", "#928374", "#3c3836", "#504945", "#8f3f71", "#79740e", "#b57614",
                             "#9d0006", "#076678", "#427b58", "#af3a03", "#076678"),
    "one-dark": palette("#282c34", "#21252b", "#282c34", "#3e4451", "#2c313a", "#3e4451", "#1b1f27", "#5c6370",
                        "#7f848e", "#abb2bf", "#9da5b4", "#c678dd", "#98c379", "#e5c07b", "#e06c75", "#61afef",
                        "#56b6c2", "#d19a66", "#61afef"),
    "solarized": palette("#002b36", "#00252e", "#002b36", "#073642", "#073642", "#0b4452", "#00212b", "#586e75",
                         "#657b83", "#93a1a1", "#839496", "#6c71c4", "#859900", "#b58900", "#dc322f", "#268bd2",
                         "#2aa198", "#cb4b16", "#268bd2"),
    "kanagawa": palette("#1f1f28", "#16161d", "#1f1f28", "#2d4f67", "#2a2a37", "#363646", "#16161d", "#54546d",
                        "#727169", "#dcd7ba", "#c8c093", "#957fb8", "#98bb6c", "#e6c384", "#e46876", "#7e9cd8",
                        "#7aa89f", "#ffa066", "#7e9cd8"),
    "rose-pine": palette("#191724", "#1f1d2e", "#191724", "#403d52", "#26233a", "#403d52", "#16141f", "#6e6a86",
                         "#817c9c", "#e0def4", "#908caa", "#c4a7e7", "#31748f", "#f6c177", "#eb6f92", "#9ccfd8",
                         "#ebbcba", "#ebbcba", "#c4a7e7"),
    "vesper": palette("#101010", "#0c0c0c", "#101010", "#232323", "#1c1c1c", "#343434", "#0a0a0a", "#505050",
                      "#7e7e7e", "#ffffff", "#a0a0a0", "#ffc799", "#99ffe4", "#ffc799", "#ff8080", "#a0a0a0",
                      "#99ffe4", "#ffc799", "#ffc799"),
    # Herdr's light themes, from each scheme's published colors.
    "one-light": palette("#fafafa", "#f0f0f0", "#fafafa", "#e5e5e6", "#eaeaeb", "#dbdbdc", "#f2f2f2", "#a0a1a7",
                         "#8e8f96", "#383a42", "#696c77", "#a626a4", "#50a14f", "#c18401", "#e45649", "#4078f2",
                         "#0184bc", "#986801", "#4078f2"),
    "solarized-light": palette("#fdf6e3", "#f5efdc", "#fdf6e3", "#eee8d5", "#eee8d5", "#e4ddc8", "#f7f0dd",
                               "#93a1a1", "#839496", "#586e75", "#657b83", "#6c71c4", "#859900", "#b58900",
                               "#dc322f", "#268bd2", "#2aa198", "#cb4b16", "#268bd2"),
    "kanagawa-lotus": palette("#f2ecbc", "#e7dba0", "#f2ecbc", "#c9cbd1", "#e5ddb0", "#dcd5ac", "#e7dba0",
                              "#8a8980", "#716e61", "#545464", "#43436c", "#624c83", "#6f894e", "#77713f",
                              "#c84053", "#4d699b", "#597b75", "#cc6d00", "#4d699b"),
    "rose-pine-dawn": palette("#faf4ed", "#fffaf3", "#faf4ed", "#dfdad9", "#f2e9e1", "#dfdad9", "#f4ede8",
                              "#9893a5", "#797593", "#575279", "#797593", "#907aa9", "#286983", "#ea9d34",
                              "#b4637a", "#56949f", "#d7827e", "#d7827e", "#907aa9"),
}

# Named colors follow ratatui's names, so they render with the terminal's palette as in Herdr.
NAMED = {"black": 0, "red": 1, "green": 2, "yellow": 3, "blue": 4, "magenta": 5, "cyan": 6, "gray": 7,
         "grey": 7, "darkgray": 8, "darkgrey": 8, "lightred": 9, "lightgreen": 10, "lightyellow": 11,
         "lightblue": 12, "lightmagenta": 13, "lightcyan": 14, "white": 15}


def color(value, fallback):
    if not isinstance(value, str):
        return fallback
    value = value.strip().lower()
    if value in ("reset", "default", "none", "transparent"):
        return None
    if value.replace("_", "").replace("-", "") in NAMED:
        return NAMED[value.replace("_", "").replace("-", "")]
    if re.fullmatch(r"#[\da-f]{3}", value):
        return "#" + "".join(c * 2 for c in value[1:])
    if re.fullmatch(r"#[\da-f]{6}", value):
        return value
    rgb = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", value)
    if rgb and all(int(c) < 256 for c in rgb.groups()):
        return "#" + "".join(f"{int(c):02x}" for c in rgb.groups())
    return fallback


def dark_appearance(terminal=None):
    """Whether to use the dark theme: the terminal's own background decides when it was reported."""
    if terminal:
        red, green, blue = (int(terminal[1][i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue < 128
    if os.uname().sysname != "Darwin":
        return True
    try:
        result = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                capture_output=True, text=True, timeout=1)
    except (OSError, subprocess.SubprocessError):
        return True
    return "dark" in result.stdout.lower()


def config():
    """Herdr's config, found the way Herdr finds it."""
    path = Path(os.environ.get("HERDR_CONFIG_PATH") or
                Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "herdr/config.toml")
    try:
        return tomllib.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def blend(base, other, amount):
    """`amount` of `other` mixed into `base`, both "#rrggbb"."""
    a, b = ([int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in (base, other))
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def from_terminal(colors=None):
    """Tokens drawn from the terminal itself, so the picker looks like part of it.

    Hues are the terminal's ANSI palette and text/background its defaults, so they match
    exactly. `colors` is the (foreground, background) the terminal reported; the lifted
    preview panel and the greys are mixed from them. Without them only the palette is
    known, and the panels share the default background.
    """
    tokens = {"text": None, "sidebar_bg": None, "surface_dim": None, "red": 1, "green": 2, "yellow": 3, "blue": 4,
              "mauve": 5, "teal": 6, "peach": 3}
    if colors:
        fg, bg = colors
        tokens.update({name: blend(bg, fg, amount) for name, amount in (
            ("panel_bg", .07), ("active_row_bg", .07), ("selection_bg", .12), ("surface0", .16), ("surface1", .26),
            ("overlay0", .42), ("overlay1", .58), ("subtext0", .8))})
    else:
        # Only the defaults are safe: palette greys can equal the background (Solarized's color 8 does),
        # so secondary text is the terminal's own text, dimmed.
        tokens.update(panel_bg=None, active_row_bg=None, selection_bg=None, surface0="dim", surface1="dim",
                      overlay0="dim", overlay1="dim", subtext0=None)
    return tokens


def load(settings=None, terminal=None):
    """Resolve the token colors plus UI options.

    With a [theme] in Herdr's config the picker follows it, so it matches Herdr's UI. Otherwise,
    or with Herdr's "terminal" theme, it takes the terminal's colors: `terminal` is the
    (foreground, background) it reported, or None. Herdr's accent is kept either way, because
    Herdr draws the popup border in it.
    """
    settings = config() if settings is None else settings
    theme, ui = settings.get("theme", {}), settings.get("ui", {})
    name, mode = theme.get("name", "catppuccin"), None
    if theme.get("auto_switch"):
        mode = "dark" if dark_appearance(terminal) else "light"
        name = theme.get(f"{mode}_name", "catppuccin" if mode == "dark" else "catppuccin-latte")
    tokens = dict(THEMES.get(name, THEMES["catppuccin"]))
    if not theme or name == "terminal":
        tokens.update(from_terminal(terminal))
    custom = theme.get("custom", {})
    layers = [custom] + ([custom[mode]] if mode and isinstance(custom.get(mode), dict) else [])
    for layer in layers:
        tokens.update({key: color(value, tokens[key]) for key, value in layer.items() if key in tokens})
    if "accent" in ui:
        tokens["accent"] = color(ui["accent"], tokens["accent"])
    tokens["symbols"] = ui.get("status_indicators") == "symbols"
    # Without two tones the list and the preview need a line between them.
    tokens["lifted"] = tokens["panel_bg"] != tokens["sidebar_bg"]
    return tokens
