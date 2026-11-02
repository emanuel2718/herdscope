"""Render assets/social-preview.png: the 1280x640 image GitHub shows when the repo is linked.

The name and tagline above the real picker, drawn with screenshot.py's demo data: the ranked
agents, and beside them, the blocked one's question. Only that much, so it still reads as a
link-preview thumbnail. Needs what screenshot.py needs.

    python3 tools/social_preview.py [output.png]
"""

import html
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import screenshot
from screenshot import BOLD, CHROME, CYAN, DIM, GREEN, RED, RESET, diff, theme

# Enough rows for the prompt, the tabs and every agent, the terminals below them cut off; enough
# columns for the preview to sit beside the list (100 or more) with its longest line whole.
ROWS, COLUMNS = 14, 130
WIDTH, HEIGHT = 1280, 640
# The README's preview cut to the change and Codex's question: no plan, no files read, two lines of diff.
PREVIEW = "\n".join([
    f"{BOLD}• Proposed change{RESET} apps/web/src/router.ts {GREEN}+38{RESET} {RED}-21{RESET}",
    "",
    diff(12, "-", "const routes = ["),
    diff(12, "+", "const routeTree = rootRoute.addChildren(["),
    "",
    f"{BOLD}  Allow Codex to edit apps/web/src/router.ts?{RESET}",
    "",
    f"{CYAN}› 1. Yes, proceed{RESET}",
    "  2. Yes, and don't ask again for this file",
    f"  3. No, and tell Codex what to do differently {DIM}esc{RESET}",
])  # fmt: skip


def frame(tokens):
    return screenshot.frame(tokens, ROWS, COLUMNS, PREVIEW)


def page(canvas, tokens):
    """The name and tagline, then the picker in Herdr's popup border, on a 1280x640 card."""
    grid = screenshot.page(canvas, tokens)
    style, body = grid.split("</style>", 1)
    return f"""{style}
html, body {{ width: {WIDTH}px; height: {HEIGHT}px; overflow: hidden; }}
body {{ padding: 0; box-sizing: border-box; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 26px; font-size: 14.5px; line-height: 22px;
  background: radial-gradient(ellipse at 50% 0%, #313244 0%, {tokens["panel_bg"]} 55%, #11111b 100%); }}
.grid div, .grid span {{ height: 22px; }}
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
