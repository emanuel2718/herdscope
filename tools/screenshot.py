"""Render assets/screenshot.png: the real picker, drawn with demo data, in a terminal window.

The frame comes from Herdscope's own drawing code; only its pixels come from a browser, so the
README always shows the current UI. Needs Google Chrome (or CHROME=/path/to/chromium) and,
for the intended look, the JetBrains Mono font.

    python3 tools/screenshot.py [output.png]
"""

import html
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from herdscope import app, commands, inventory, render, theme
from herdscope.terminal import Canvas, Style
from herdscope.view import View

ROWS, COLUMNS = 38, 160
NOW = 1_800_000_000
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
# The terminal's ANSI palette, for colors the preview names by number: Catppuccin Mocha.
ANSI = ("#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
        "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8")  # fmt: skip

# Each agent: (workspace, tab, agent, status, task, folder, seconds in that status).
AGENTS = (
    ("web", 3, "codex", "blocked", "Migrate router to typed routes", "~/code/web", 130),
    ("api", 1, "opencode", "working", "Review error handling in handlers", "~/code/api", 200),
    ("api", 2, "claude", "working", "Add rate limiting to the public API", "~/code/api", 740),
    ("docs", 1, "claude", "done", "Write release notes for 2.3", "~/code/docs", 1500),
    ("web", 1, "claude", "done", "Fix flaky checkout e2e test", "~/code/web", 380),
    ("web", 2, "claude", "idle", "Where are the fonts loaded?", "~/code/web", 3700),
    ("api", 4, "gemini", "idle", "Explain the billing webhooks", "~/code/api", 11000),
)
TERMINALS = (
    ("api", 3, "lazygit", "~/code/api"),
    ("api", 3, "docker compose up", "~/code/api"),
    ("notes", 1, "nvim inbox.md", "~/notes"),
    ("web", 4, "~/code/web", "~/code/web"),
    ("web", 4, "pnpm dev", "~/code/web"),
)
REMOTE = (
    ("ml", 2, "codex", "blocked", "Pin CUDA images to 12.4", "/home/me/ml"),
    ("ml", 1, "claude", "working", "Profile the training data loader", "/home/me/ml"),
)
# What the selected agent's pane shows: Codex asking to apply a change.
BOLD, DIM, RED, GREEN, CYAN, RESET = "\x1b[1m", "\x1b[2m", "\x1b[31m", "\x1b[32m", "\x1b[36m", "\x1b[0m"
REMOVED, ADDED = "\x1b[48;2;64;38;51m\x1b[38;2;243;139;168m", "\x1b[48;2;40;60;46m\x1b[38;2;166;227;161m"


def diff(number, sign, code):
    return f"  {DIM}{number}{RESET} {REMOVED if sign == '-' else ADDED}{sign} {code}{RESET}"


PREVIEW = "\n".join([
    f"{BOLD}• I'll replace the route array with a typed route tree, so links{RESET}",
    f"{BOLD}  and params are checked when the app builds.{RESET}",
    "",
    f"{BOLD}• Explored{RESET}",
    f"  {DIM}└{RESET} Read router.ts, routes/projects.tsx, routes/settings.tsx",
    "    Search createBrowserRouter in apps/web",
    "",
    f"{BOLD}• Proposed change{RESET} apps/web/src/router.ts {GREEN}+38{RESET} {RED}-21{RESET}",
    "",
    f'  {DIM}10{RESET}   import {{ rootRoute }} from "./routes/root"',
    f"  {DIM}11{RESET}",
    diff(12, "-", "const routes = ["),
    diff(13, "-", '  { path: "/", element: <Home /> },'),
    diff(14, "-", '  { path: "/projects/:id", element: <Project /> },'),
    diff(15, "-", "]"),
    diff(12, "+", "const routeTree = rootRoute.addChildren(["),
    diff(13, "+", "  homeRoute, projectRoute, settingsRoute,"),
    diff(14, "+", "])"),
    diff(15, "+", "export const router = createRouter({ routeTree })"),
    "",
    f"{BOLD}  Allow Codex to edit apps/web/src/router.ts?{RESET}",
    "",
    f"{CYAN}› 1. Yes, proceed{RESET}",
    "  2. Yes, and don't ask again for this file",
    f"  3. No, and tell Codex what to do differently {DIM}esc{RESET}",
])  # fmt: skip


def snapshot(rows, terminals=()):
    """A Herdr session.snapshot holding `rows` of agents and `terminals`."""
    names = sorted({row[0] for row in (*rows, *terminals)}, key=["web", "api", "ml", "docs", "notes"].index)
    workspaces = [{"workspace_id": f"w{i}", "label": name, "number": i} for i, name in enumerate(names, 1)]
    ids = {w["label"]: w["workspace_id"] for w in workspaces}
    tabs, panes, agents = {}, [], []

    def pane(workspace, tab, title, folder):
        tab_id = f"{ids[workspace]}:t{tab}"
        tabs[tab_id] = {"tab_id": tab_id, "label": str(tab), "number": tab}
        pane_id = f"{ids[workspace]}:p{len(panes)}"
        panes.append({"pane_id": pane_id, "terminal_id": f"term-{len(panes)}", "workspace_id": ids[workspace],
                      "tab_id": tab_id, "terminal_title_stripped": title,
                      "cwd": folder.replace("~", str(Path.home()), 1)})  # fmt: skip
        return pane_id

    for workspace, tab, agent, status, task, folder, *_ in rows:
        agents.append({"pane_id": pane(workspace, tab, task, folder), "agent": agent, "agent_status": status})
    for workspace, tab, title, folder in terminals:
        pane(workspace, tab, title, folder)
    focused = panes[-1]["pane_id"] if terminals else None
    return {"focused_pane_id": focused, "workspaces": workspaces, "tabs": list(tabs.values()), "panes": panes,
            "agents": agents}  # fmt: skip


def entries():
    local = snapshot(AGENTS, TERMINALS)
    found = inventory.entries(local)
    for entry, (*_, held) in zip((e for e in found if e["kind"] == "agent"), AGENTS):
        entry["since"] = NOW - held
    found += inventory.entries(snapshot(REMOTE), {"id": "gpu", "label": "gpu-box"})
    return local, sorted(found, key=lambda entry: inventory.rank(entry, {}))


def frame(tokens, rows=ROWS, columns=COLUMNS, preview=PREVIEW):
    """The demo session drawn on a `rows` by `columns` canvas, the selected agent's pane showing `preview`."""
    local, panes = entries()
    with patch.object(commands, "installed", side_effect=lambda name: name in ("claude", "codex", "opencode")):
        listed = panes + commands.catalog(local, {})
    canvas, palette, view = Canvas(rows, columns), render.Palette(tokens), View()
    with app.Previewer(Style(tokens["text"], tokens["panel_bg"]), lambda entry, lines: preview) as previewer:
        for _ in range(50):
            render.draw(canvas, palette, view, listed, view.results(1, listed), 0.35, previewer, wall=NOW)
            if previewer.get(view.selected):
                break
            time.sleep(0.02)
        render.draw(canvas, palette, view, listed, view.results(1, listed), 0.35, previewer, wall=NOW)
    return canvas


def page(canvas, tokens):
    """The frame as HTML: a terminal window, Herdr's popup border, and the picker inside it."""

    def color(value, default):
        return ANSI[value] if isinstance(value, int) and value < 16 else value or default

    text, background = tokens["text"], tokens["sidebar_bg"]
    rows = []
    for row in canvas.cells:
        # Each run: its style, its text, and the cells it must fill, or None when it fills them by itself.
        # A glyph from a fallback font (the working spinner's braille) can be wider than a cell, and the
        # browser would push the rest of its line right, so each non-ASCII glyph gets cells of its own.
        runs = []
        for n, (char, style) in enumerate(row):
            if not char:
                continue  # The second cell of a wide glyph.
            if not char.isascii():
                wide = n + 1 < len(row) and not row[n + 1][0]
                runs.append([style, [char], 1 + wide])
            elif runs and runs[-1][0] == style and runs[-1][2] is None:
                runs[-1][1].append(char)
            else:
                runs.append([style, [char], None])
        spans = []
        for style, chars, cells in runs:
            css = f"color:{color(style.fg, text)};background:{color(style.bg, background)}"
            css += ";font-weight:700" * style.bold + ";opacity:.6" * style.dim + ";font-style:italic" * style.italic
            css += f";width:{cells}ch" * bool(cells)
            spans.append(f'<span style="{css}">{html.escape("".join(chars))}</span>')
        rows.append(f"<div>{''.join(spans)}</div>")
    accent = tokens["accent"]
    return f"""<!doctype html><meta charset="utf-8"><style>
html, body {{ margin: 0; background: transparent; }}
body {{ padding: 40px; font: 14px/20px "JetBrains Mono NL", "JetBrains Mono", Menlo, monospace; }}
.window {{ display: inline-block; border-radius: 12px; overflow: hidden; background: {tokens["panel_bg"]};
  box-shadow: 0 0 0 1px #ffffff14, 0 24px 60px #00000080; }}
.bar {{ height: 36px; display: flex; align-items: center; padding: 0 14px; gap: 8px; background: #181825;
  color: #9399b2; font-size: 13px; }}
.bar i {{ width: 12px; height: 12px; border-radius: 50%; display: inline-block; }}
.bar span {{ flex: 1; text-align: center; margin-right: 52px; }}
.popup {{ margin: 18px 22px 22px; border: 1px solid {accent}; border-radius: 6px; position: relative;
  padding: 10px 2px 8px; background: {background}; }}
.popup b {{ position: absolute; top: -11px; left: 14px; padding: 0 6px; color: {accent};
  background: {tokens["panel_bg"]}; }}
.grid {{ white-space: pre; }}
.grid div {{ height: 20px; }}
.grid span {{ display: inline-block; height: 20px; vertical-align: top; }}
</style><div class="window"><div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i>
<i style="background:#28c840"></i><span>herdr</span></div><div class="popup"><b>Herdscope</b>
<div class="grid">{"".join(rows)}</div></div></div>"""


def main():
    output = Path(
        sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "assets/screenshot.png"
    )
    tokens = theme.load({"theme": {"name": "catppuccin"}})
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "screenshot.html"
        source.write_text(page(frame(tokens), tokens))
        # JetBrains Mono's cells are 0.6em wide; the rest is the window's padding, border and shadow.
        width, height = round(COLUMNS * 8.4) + 130, ROWS * 20 + 176
        subprocess.run([CHROME, "--headless=new", "--hide-scrollbars", "--force-device-scale-factor=2",
                        "--default-background-color=00000000", f"--window-size={width},{height}",
                        f"--screenshot={output}", source.as_uri()],
                       check=True, capture_output=True, timeout=60)  # fmt: skip
    print(output)


if __name__ == "__main__":
    main()
