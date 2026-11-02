"""Render assets/social-preview.png: the 1280x640 image GitHub shows when the repo is linked.

The name and tagline above the real picker's agent list, drawn with screenshot.py's demo data,
large enough to read as a link-preview thumbnail. Needs what screenshot.py needs.

    python3 tools/social_preview.py [output.png]
"""

import html
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import screenshot
from screenshot import CHROME, NOW, Canvas, Style, View, app, commands, render, theme

# Enough rows for the prompt, the tabs and every agent; the terminals below them are cut off.
ROWS, COLUMNS = 14, 86
WIDTH, HEIGHT = 1280, 640


def frame(tokens):
    """The picker without its preview: the prompt, the tabs and the ranked list."""
    local, panes = screenshot.entries()
    with patch.object(commands, "installed", side_effect=lambda name: name in ("claude", "codex", "opencode")):
        listed = panes + commands.catalog(local, {})
    canvas, palette, view = Canvas(ROWS, COLUMNS), render.Palette(tokens), View()
    view.preview = False
    with app.Previewer(Style(tokens["text"], tokens["panel_bg"]), lambda entry, lines: "") as previewer:
        render.draw(canvas, palette, view, listed, view.results(1, listed), 0.35, previewer, wall=NOW)
    return canvas


def page(canvas, tokens):
    """The name and tagline, then the list in Herdr's popup border, on a 1280x640 card."""
    grid = screenshot.page(canvas, tokens)
    style, body = grid.split("</style>", 1)
    return f"""{style}
html, body {{ width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; }}
body {{ padding: 0; box-sizing: border-box; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 26px; font-size: 18px; line-height: 25px;
  background: radial-gradient(ellipse at 50% 0%, #313244 0%, {tokens["panel_bg"]} 55%, #11111b 100%); }}
.grid div, .grid span {{ height: 25px; }}
.popup {{ padding-top: 20px; }}
.window {{ box-shadow: 0 0 0 1px #ffffff1a, 0 30px 70px #00000099; }}
header {{ text-align: center; color: {tokens["text"]};
  font-family: "Inter", -apple-system, "Helvetica Neue", sans-serif; }}
h1 {{ margin: 0; font-size: 58px; line-height: 62px; font-weight: 800; letter-spacing: -1.5px; }}
h1 span {{ color: {tokens["accent"]}; }}
p {{ margin: 6px 0 0; font-size: 25px; line-height: 32px; color: #bac2de; }}
</style><header><h1><span>Herd</span>scope</h1>
<p>{html.escape("A fuzzy finder for everything in Herdr, ranked by what needs you.")}</p></header>
{body}"""


def main():
    output = Path(
        sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets/social-preview.png"
    )
    tokens = theme.load({"theme": {"name": "catppuccin"}})
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "social-preview.html"
        source.write_text(page(frame(tokens), tokens))
        subprocess.run([CHROME, "--headless=new", "--hide-scrollbars", "--force-device-scale-factor=2",
                        f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={output}", source.as_uri()],
                       check=True, capture_output=True, timeout=60)  # fmt: skip
    print(output)


if __name__ == "__main__":
    main()
