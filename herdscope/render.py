"""Drawing the picker: the list panel, the preview, and the prompt, tabs and footer around them.

Everything is drawn into a Canvas every frame; the terminal then writes only what changed.
"""

import time

from herdscope import inventory
from herdscope.terminal import Style, clip, text_width
from herdscope.view import COMMANDS, SCOPE_KINDS, SCOPES

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SYMBOLS = {"blocked": "▲", "working": "◐", "done": "✓", "idle": "○", "unknown": "?"}
STATUS_ROLE = {"blocked": "red", "working": "yellow", "done": "green", "idle": "overlay1", "unknown": "overlay0",
               "connecting": "overlay1", "offline": "red"}
EMPTY = {None: ("No panes yet", ""), "agent": ("No agents running", "Agents you start in Herdr show up here."),
         "terminal": ("No terminals", ""), "command": ("No commands", "")}
HINTS = {"panes": (("↵", "go"), ("⇥", "scope"), (">", "commands"), ("^/", "preview"), ("⇧↑↓", "scroll"),
                   ("^x", "close"), ("esc", "quit")),
         "commands": (("↵", "run"), ("⇥", "scope"), ("⌫", "back to panes"), ("esc", "quit")),
         "input": (("↵", "apply"), ("esc", "back"), ("^u", "clear"))}
STATUS_WIDTH = len("working 11mo")


class Palette:
    """Theme tokens as cached terminal styles."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.cache = {}

    def __call__(self, fg="text", bg="panel_bg", bold=False, italic=False):
        key = (fg, bg, bold, italic)
        if key not in self.cache:
            # "dim" is the terminal's own text, dimmed; as a background it is the default.
            color, ground = self.tokens.get(fg, fg), self.tokens.get(bg, bg)
            self.cache[key] = Style(None if color == "dim" else color, None if ground == "dim" else ground, bold,
                                    color == "dim", italic)
        return self.cache[key]


def status_icon(entry, now, symbols=False):
    """The glyph before a row and its role: status for agents, spinning while working."""
    status = entry.get("status")
    if entry["kind"] == "command":
        return entry["glyph"], "red" if entry.get("confirm") else "overlay1"
    if entry["kind"] == "terminal":
        return "›", "overlay0"
    if entry["kind"] == "error":
        return (SPINNER[int(now * 10) % 10] if status == "connecting" else "!"), STATUS_ROLE[status]
    if symbols:
        return SYMBOLS.get(status, "?"), STATUS_ROLE.get(status, "overlay0")
    if status == "working":
        return SPINNER[int(now * 10) % 10], "yellow"
    return ("○" if status == "unknown" else "●"), STATUS_ROLE.get(status, "overlay0")


def duration(seconds):
    """How long a status has held: now, 4m, 2h, 3d, 5mo."""
    for size, unit in ((100 * 86400, "mo"), (86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // (30 * 86400 if unit == 'mo' else size))}{unit}"
    return "now"


class Painter:
    """Draws text in theme roles onto a canvas."""

    def __init__(self, canvas, palette):
        self.canvas, self.palette = canvas, palette

    def text(self, y, x, text, fg="text", bg="panel_bg", width=None, bold=False, hl=None, italic=False):
        """Draw clipped text with matched characters highlighted; returns the column after it."""
        shown = clip(text, width) if width is not None else text
        style = self.palette(fg, bg, bold, italic)
        if not hl:
            return self.canvas.put(y, x, shown, style)
        accent = self.palette("accent", bg, True)
        keep = len(shown) if shown == text else len(shown) - 1
        for i, char in enumerate(shown):
            x = self.canvas.put(y, x, char, accent if i < keep and i in hl else style)
        return x

    def fill(self, y, x, width, bg="panel_bg", char=" ", fg="text"):
        self.canvas.fill(y, x, width, self.palette(fg, bg), char)


def columns(items, width):
    """Right-hand columns as [(field, width)], dropping the least useful first when space is short.

    Widths come from every pane, not just the matches, so typing never moves a column.
    """
    panes = [item for item in items if item.entry["kind"] in ("agent", "terminal")]
    caps = [("machine", 14 if len({item.entry["machine"] for item in panes}) > 1 else 0), ("agent", 12),
            ("place", 24)]
    chosen = [(name, size) for name, cap in caps
              if (size := min(cap, max((text_width(item.fields.get(name, "")) for item in panes), default=0)))]
    if any(item.entry["kind"] in ("agent", "error") for item in items):
        chosen.append(("status", STATUS_WIDTH))
    return fit(chosen, width, ("machine", "agent", "place", "status"), 22)


def command_columns(items, width):
    """A command's category, target and shortcut columns, sized from every command."""
    found = [item.entry for item in items if item.entry["kind"] == "command"]
    chosen = [(name, min(cap, max((text_width(entry[name]) for entry in found), default=0)))
              for name, cap in (("category", 10), ("target", 24), ("binding", 8))]
    return fit(chosen, width, ("category", "target", "binding"), 20)


def measured_columns(view, width):
    """The tab's right-hand columns, measured once per inventory and width rather than every frame."""
    key = (view.items[0], view.scope == COMMANDS, width)
    if view.cols[0] != key:
        view.cols = (key, (command_columns if view.scope == COMMANDS else columns)(view.items[1], width))
    return view.cols[1]


def fit(chosen, width, order, room):
    """Drop columns in `order` until a row of `width` keeps `room` cells for its label."""
    for drop in order:
        if width - 6 - sum(size + 2 for _, size in chosen) >= room:
            break
        chosen = [c for c in chosen if c[0] != drop]
    return chosen


def place(cols, x, width):
    """Where each right-hand column starts, packed against the row's right edge."""
    edge, starts = x + width - 2, {}
    for name, size in reversed(cols):
        edge -= size
        starts[name] = edge
        edge -= 2
    return starts


def draw_command(p, y, x, width, item, hl, selected, cols, backgrounds):
    """A command row, shaped like a pane row: what it does, what it acts on, and its shortcut."""
    entry, bg = item.entry, backgrounds[selected]
    p.fill(y, x, width, bg)
    if selected:
        p.text(y, x, "▌", "accent", bg)
    glyph, role = status_icon(entry, 0)
    p.text(y, x + 2, glyph, role, bg, bold=True)
    starts = place(cols, x, width)
    for name, size in cols:
        role = ("subtext0" if selected else "overlay0") if name == "binding" else \
            ("subtext0" if selected else "overlay1")
        p.text(y, starts[name], entry[name], role, bg, size, hl=hl.get(name))
    column = min(starts.values(), default=x + width) - 2
    p.text(y, x + 4, entry["title"], "text", bg, column - (x + 4), selected, hl.get("label"))


def draw_row(p, y, x, width, item, hl, selected, cols, now, wall, backgrounds):
    """One result: pointer, status glyph, label and detail, then the fixed right-hand columns."""
    entry, fields = item.entry, item.fields
    if entry["kind"] == "command":
        return draw_command(p, y, x, width, item, hl, selected, cols, backgrounds)
    bg = backgrounds[selected]
    # Secondary text steps up on the selected row, so it stays readable on the selection color.
    muted, faint = ("subtext0", "subtext0") if selected else ("overlay1", "overlay0")
    p.fill(y, x, width, bg)
    if selected:
        p.text(y, x, "▌", "accent", bg)
    icon, role = status_icon(entry, now, p.palette.tokens.get("symbols"))
    p.text(y, x + 2, icon, role, bg, bold=True)
    starts = place(cols, x, width)
    column = x + width - 2  # Where the label must stop: the first column this row fills.
    for name, size in cols:
        at = starts[name]
        if name == "status":
            # Herdr's status word, then how long it has held, right-aligned.
            status = entry.get("status")
            if status not in STATUS_ROLE or status == "unknown":
                continue
            # A machine's panes as last seen, until it answers, have their status dimmed.
            role = faint if entry.get("stale") else STATUS_ROLE[status]
            p.text(y, at, status, role, bg, size, hl=hl.get("status"))
            if entry.get("since"):
                held = duration(max(0, wall - entry["since"]))
                p.text(y, at + size - text_width(held), held, faint, bg)
        elif entry["kind"] == "error" or not fields.get(name):
            continue
        else:
            p.text(y, at, fields[name], muted, bg, size, hl=hl.get(name))
        # Text on the left may run up to the first column this row fills.
        column = min(column, at - 2)
    # Label, then a "current" tag, then the terminal's directory or the error, as far as they fit.
    current = entry.get("current")
    at = p.text(y, x + 4, fields["label"], "text", bg, column - (x + 4) - (9 if current else 0), selected,
                hl.get("label"))
    extras = [("current", faint, None, True)] if current else []
    if fields.get("detail") or entry["kind"] == "error":
        extras.append((fields.get("detail") or fields["path"], "red" if entry["status"] == "offline" else faint,
                       hl.get("detail") or hl.get("path"), False))
    for value, role, marks, italic in extras:
        room = column - at - 2
        if room < 6:
            break
        at = p.text(y, at + 2, value, role, bg, room, hl=marks, italic=italic)


def draw_split(p, y, x, width, height, entry, entries):
    """The pane you came from and, dashed, the one the split adds beside or below it."""
    pane = next((e for e in entries if e["id"] == entry["pane"]), None)
    label = inventory.labels(pane)[0] if pane else entry["target"]
    if entry["split"] == "right":
        half = width // 2
        boxes = [(y, x, height, half, label, False), (y, x + half, height, width - half, "new pane", True)]
    else:
        half = height // 2
        boxes = [(y, x, half, width, label, False), (y + half, x, height - half, width, "new pane", True)]
    for top, left, h, w, text, new in boxes:
        corners, horizontal, vertical = ("┌┐└┘", "╌", "╎") if new else ("╭╮╰╯", "─", "│")
        role = "accent" if new else "surface1"
        for row in (top, top + h - 1):
            p.fill(row, left + 1, w - 2, char=horizontal, fg=role)
        for row in range(top + 1, top + h - 1):
            p.text(row, left, vertical, role)
            p.text(row, left + w - 1, vertical, role)
        for (row, column), corner in zip(((top, left), (top, left + w - 1), (top + h - 1, left),
                                          (top + h - 1, left + w - 1)), corners):
            p.text(row, column, corner, role)
        p.text(top + h // 2, left + max(1, (w - text_width(text)) // 2), text, "accent" if new else "subtext0",
               width=w - 2, bold=new)


def draw_header(p, top, x, width, glyph, role, title, hl, parts):
    """A preview's first two lines: what it is, then where, as (text, role, highlights) parts."""
    p.text(top, x, glyph, role, bold=True)
    p.text(top, x + 2, title, "text", width=width - 2, bold=True, hl=hl)
    at = x + 2
    for i, (text, role, marks) in enumerate(parts):
        if i:
            at = p.text(top + 1, at, "  ·  ", "overlay0", width=max(0, x + width - at))
        at = p.text(top + 1, at, text, role, width=max(0, x + width - at), hl=marks)
    return top + 1


def draw_answer(p, y, x, width, text, entry, bg="panel_bg"):
    """What the typed answer will become: the text itself, what an empty answer means, or a nudge."""
    if text:
        return p.text(y, x, text, "accent", bg, width, bold=True)
    if entry.get("blank"):
        return p.text(y, x, entry["blank"], "overlay1", bg, width, italic=True)
    return p.text(y, x, "type a name", "overlay0", bg, width, italic=True)


def draw_command_preview(p, view, item, hl, area, entries, now, inset):
    """What a command does and to what: the workspace list, the panes it stops, or the new layout."""
    x, top, width, height = area
    x, width, bottom = x + inset, width - inset - 2, top + height
    top += 1 if height >= 8 else 0
    entry, asking = item.entry, view.prompt == item.entry["id"]
    glyph, role = status_icon(entry, now)
    parts = [(part, "overlay1", None) for part in (entry["category"], entry["target"], entry["binding"]) if part]
    y = draw_header(p, top, x, width, glyph, "red" if role == "red" else "accent", entry["title"], hl.get("label"),
                    parts) + 2
    for line in wrap(entry["about"], width, 4):
        p.text(y, x, line, "subtext0")
        y += 1
    typed = view.query.strip() if asking else None
    if entry.get("folder"):
        at = p.text(y + 1, x, "Starts in  ", "overlay1")
        p.text(y + 1, at, inventory.display_path({"cwd": entry["folder"]}), "text", width=max(0, x + width - at))
        y += 2
    if asking and "was" in entry and "sidebar" not in entry:
        at = p.text(y + 1, x, entry["was"], "overlay0", width=width // 2)
        at = p.text(y + 1, at + 1, "→", "overlay0")
        draw_answer(p, y + 1, at + 1, max(0, x + width - at - 1), typed, entry)
        y += 2
    if entry.get("sidebar"):
        y += 1
        for number, label, here in entry["sidebar"]:
            if y >= bottom:
                break
            bg = "selection_bg" if here else "panel_bg"
            p.fill(y, x, min(34, width), bg)
            p.text(y, x + 1, str(number), "overlay1", bg)
            if here and asking:
                at = p.text(y, x + 4, label, "overlay0", bg, width=12)
                at = p.text(y, at + 1, "→", "overlay0", bg)
                draw_answer(p, y, at + 1, max(0, x + 33 - at), typed, entry, bg)
            else:
                p.text(y, x + 4, label, "text" if here else "subtext0", bg, width=29, bold=here)
            if here and width > 56:
                p.text(y, x + 36, "you came from here", "overlay0", italic=True)
            y += 1
    if entry.get("affects"):
        y += 1
        by_id = {e["id"]: e for e in entries}
        for pane in filter(None, map(by_id.get, entry["affects"])):
            if y >= bottom:
                break
            label, detail = inventory.labels(pane)
            glyph, role = status_icon(pane, now)
            p.text(y, x + 2, glyph, role, bold=True)
            at = p.text(y, x + 4, label, "text", width=width - 4)
            if detail and x + width - at > 8:
                p.text(y, at + 2, detail, "overlay0", width=x + width - at - 2)
            y += 1
    if entry.get("split") and bottom - y > 6:
        draw_split(p, y + 1, x, min(width, 64), min(12, bottom - y - 1), entry, entries)


def draw_preview(p, view, item, hl, area, previewer, now, inset):
    """Header (what and where), then the pane's own output, bottom-anchored like a terminal."""
    x, top, width, height = area
    x, width = x + inset, width - inset - 2
    top += 1 if height >= 8 else 0
    entry = item.entry
    if entry["kind"] == "error":
        parts = [(entry["status"], STATUS_ROLE[entry["status"]], None)]
    else:
        parts = [(entry["agent"], "overlay1", hl.get("agent"))] if entry["kind"] == "agent" else []
        parts.append((item.fields["place"], "overlay1", hl.get("place")))
        path = item.fields.get("path") or item.fields.get("detail")
        if path:
            parts.append((path, "overlay1", hl.get("path") or hl.get("detail")))
    icon, role = status_icon(entry, now, p.palette.tokens.get("symbols"))
    meta = draw_header(p, top, x, width, icon, role, item.fields["label"], hl.get("label"), parts)
    body = meta + 2
    rows = area[1] + height - body
    if rows <= 0:
        return
    if entry["kind"] == "error":
        for i, line in enumerate(wrap(entry["error"], width, rows)):
            p.text(body + i, x, line, "overlay1")
        return
    previewer.want(entry, rows + view.preview_scroll + 1)
    cached = previewer.get(entry["id"])
    if cached is None:
        # Local previews land within milliseconds; only a slow one gets a loading line.
        if previewer.waiting() > 0.15:
            p.text(body, x, f"{SPINNER[int(now * 10) % 10]} loading preview…", "overlay0")
        view.hits["animated"] = True
        return
    lines, error, depth = cached
    if error:
        for i, line in enumerate(wrap(f"Preview unavailable: {error}", width, rows)):
            p.text(body + i, x, line, "overlay1")
        return
    if not lines:
        p.text(body, x, "empty", "overlay0", italic=True)
        return
    if depth >= rows + view.preview_scroll + 1:
        # Only a read as deep as the scroll asked for knows how far back the pane goes.
        view.preview_scroll = min(view.preview_scroll, max(0, len(lines) - rows))
    if view.preview_scroll:
        label = f"↑{view.preview_scroll}"
        p.text(meta, x + width - text_width(label), label, "overlay1")
    start = max(0, len(lines) - rows - view.preview_scroll)
    for i, runs in enumerate(lines[start:start + rows]):
        at = x
        for text, style in runs:
            at = p.canvas.put(body + i, at, text, style, limit=x + width - at)
            if at >= x + width:
                break


def wrap(text, width, lines):
    result, words = [], text.split()
    while words and len(result) < lines:
        line = words.pop(0)
        while words and text_width(line + " " + words[0]) <= width:
            line += " " + words.pop(0)
        result.append(clip(line, width))
    return result


def layout(height, width, preview):
    """The list panel and preview as (x, y, width, height), and how they sit: "beside", "below" or None."""
    if preview and width >= 100:
        side = max(56, min(96, width // 2 - 2))
        return (0, 0, side, height), (side, 0, width - side, height), "beside"
    if preview and height >= 24:
        top = height * 11 // 20
        return (0, 0, width, top), (0, top, width, height - top), "below"
    return (0, 0, width, height), None, None


def footer(p, view, y, x, width, bg):
    """The panel's last line: a close confirmation, else a passing notice, else key hints."""
    limit = x + width - 2
    pending = view.find(view.confirm) if view.confirm else None
    if pending:
        entry = pending.entry
        question = entry.get("confirm") or \
            f"Close {entry['agent'] or 'terminal'} in {entry['workspace']}:{entry['tab']}?"
        at = p.text(y, x + 2, question, "peach", bg, limit - x - 2, bold=True)
        if entry["kind"] == "agent":
            at = p.text(y, at + 1, "This stops the agent.", "peach", bg, max(0, limit - at - 1))
        p.text(y, at + 2, "y confirms · other keys cancel", "overlay0", bg, max(0, limit - at - 2))
    elif view.notice and view.notice[2] > time.monotonic():
        p.text(y, x + 2, view.notice[0], view.notice[1], bg, limit - x - 2)
    else:
        at = x + 2
        mode = "input" if view.prompt else "commands" if view.scope == COMMANDS else "panes"
        for key, label in HINTS[mode]:
            if at + text_width(key) + text_width(label) + 1 > limit:
                break
            at = p.text(y, at, key, "subtext0", bg, bold=True)
            at = p.text(y, at + 1, label, "overlay1", bg) + 3


def draw_prompt(p, view, y, x, width, counter):
    """The query, scrolled to keep the cursor in view, and fzf's inline match counter.

    In Commands the symbol is ">", standing in for the ">" typed to get there. While a
    command asks for text, the prompt becomes its input, with the old value on the right.
    """
    asking = view.find(view.prompt) if view.prompt else None
    if asking:
        entry = asking.entry
        p.text(y, x + 2, entry["glyph"], "accent", "sidebar_bg", bold=True)
        start = p.text(y, x + 4, entry["title"].rstrip("…"), "text", "sidebar_bg", max(1, (width - 8) // 2), True)
        start = p.text(y, start + 1, "›", "overlay0", "sidebar_bg") + 1
        counter, placeholder = f"was {entry['was']}" if entry.get("was") else "", ""
        if x + width - 4 - text_width(counter) - start < 12:
            counter = ""  # Typing room matters more than the reminder.
    else:
        in_commands = view.scope == COMMANDS
        p.text(y, x + 2, ">" if in_commands else "❯", "accent", "sidebar_bg", bold=True)
        start, placeholder = x + 4, "run a command" if in_commands else "search"
    room = max(1, x + width - 4 - text_width(counter) - start)
    shown, before = view.query, text_width(view.query[:view.cursor])
    while before >= room and shown:
        before -= text_width(shown[0])
        shown = shown[1:]
    p.text(y, start, shown or placeholder, "text" if view.query else "overlay0", "sidebar_bg", width=room)
    p.text(y, x + width - 2 - text_width(counter), counter, "overlay0" if asking else "overlay1", "sidebar_bg")
    p.canvas.cursor = (y, start + before)


def draw_tabs(p, view, y, x, width, total):
    """Scope tabs with match counts, underlined under the active one.

    Every tab keeps its slot and counts are padded to the widest possible, so typing
    changes digits, never positions.
    """
    digits, at, underline = len(str(total)), x + 2, None
    for i, (name, count) in enumerate(zip(SCOPES, view.counts())):
        active, start = i == view.scope, at
        at = p.text(y, at, name, "text" if active else "overlay1", "sidebar_bg", bold=active)
        at = p.text(y, at + 1, str(count).ljust(digits), "accent" if active else "overlay0", "sidebar_bg",
                    bold=active)
        if active:
            underline = (start, at - digits + len(str(count)))
        view.hits["tabs"].append((start, at, i))
        at += 3
    view.hits["tabs_y"] = y
    p.fill(y + 1, x + 2, width - 4, "sidebar_bg", "─", "surface0")
    p.fill(y + 1, underline[0], underline[1] - underline[0], "sidebar_bg", "━", "accent")


def draw_results(p, view, rows, top, bottom, x, width, now, wall, selected_bg):
    """One line per result, a blank line between kinds while browsing everything, and a scrollbar."""
    lines = view.lines
    visible = max(1, bottom - top)
    view.page = max(1, visible - 1)
    if rows:
        selected_line = lines.index(view.index)
        margin = min(2, max(0, (visible - 1) // 2))
        if selected_line < view.offset + margin:
            view.offset = selected_line - margin
        elif selected_line > view.offset + visible - 1 - margin:
            view.offset = selected_line - visible + 1 + margin
    view.offset = max(0, min(view.offset, max(0, len(lines) - visible)))
    cols = measured_columns(view, width - 1)
    backgrounds = ("sidebar_bg", selected_bg)
    for n, line in enumerate(lines[view.offset:view.offset + visible]):
        if line is not None:
            item, hl = rows[line]
            draw_row(p, top + n, x, width, item, hl, line == view.index, cols, now, wall, backgrounds)
            view.hits["rows"][top + n] = line
    if len(lines) > visible:
        size = max(1, visible * visible // len(lines))
        start = view.offset * (visible - size) // max(1, len(lines) - visible)
        for y in range(top + start, top + start + size):
            p.text(y, x + width - 1, "┃", "overlay0", "sidebar_bg")
    if not rows:
        title, hint = EMPTY[SCOPE_KINDS[view.scope]]
        if view.search:
            title, hint = f"No matches for “{view.search}”", "Try fewer letters, or ⇥ to another tab."
        p.text(top + 1, x + 4, title, "overlay1", "sidebar_bg", width=width - 6, bold=True)
        p.text(top + 2, x + 4, hint, "overlay0", "sidebar_bg", width=width - 6)


def draw(canvas, palette, view, entries, rows, now, previewer, loading=False, wall=None):
    p = Painter(canvas, palette)
    height, width = canvas.getmaxyx()
    canvas.erase(palette())
    canvas.cursor = None
    view.hits = {"rows": {}, "tabs": [], "tabs_y": None, "preview": None}
    if height < 6 or width < 30:
        p.text(0, 1, "Enlarge the popup · esc closes", "overlay1", width=width - 2)
        previewer.idle()
        return

    # The list panel takes the sidebar's tone and the preview the pane's, like Herdr itself.
    # Without two tones a line separates them, and the selected row gets the selection color.
    lifted = palette.tokens.get("lifted", True)
    (lx, ly, lw, lh), preview_area, arrangement = layout(height, width, view.preview)
    for y in range(ly, ly + lh):
        p.fill(y, lx, lw, "sidebar_bg")
    if preview_area and not lifted:
        if arrangement == "beside":
            for y in range(height):
                p.text(y, preview_area[0], "│", "surface1")
        else:
            p.fill(preview_area[1], 0, width, char="─", fg="surface1")

    # A tall panel gets breathing room around the prompt, the tabs and the hints.
    roomy = lh >= 20
    prompt_y = ly + roomy
    tabs_y = prompt_y + 1 + roomy
    hints_y = ly + lh - 1
    counter = f"{len(rows)}/{view.totals[view.scope]}"
    if loading:
        counter = f"{SPINNER[int(now * 10) % 10]} {counter}"
    draw_prompt(p, view, prompt_y, lx, lw, counter)
    draw_tabs(p, view, tabs_y, lx, lw, len(entries))
    draw_results(p, view, rows, tabs_y + 2 + roomy, hints_y - roomy, lx, lw, now,
                 time.time() if wall is None else wall, "panel_bg" if lifted else "selection_bg")
    footer(p, view, hints_y, lx, lw, "sidebar_bg")
    # Spinners are all that moves by itself, so the loop wakes at their pace only while one shows.
    view.hits["animated"] = loading or any(rows[line][0].entry.get("status") in ("working", "connecting")
                                           for line in view.hits["rows"].values())
    previewing = False
    if preview_area:
        view.hits["preview"] = preview_area
        if rows:
            item, hl = rows[view.index]
            inset = 3 if arrangement == "beside" else 2
            if item.entry["kind"] == "command":
                draw_command_preview(p, view, item, hl, preview_area, entries, now, inset)
            else:
                draw_preview(p, view, item, hl, preview_area, previewer, now, inset)
                previewing = item.entry["kind"] != "error"
    if not previewing:
        previewer.idle()  # Nothing on screen shows a pane's output, so stop reading it.


def draw_message(canvas, palette, lines):
    """A centered notice over the picker, for outcomes the user must act on."""
    p = Painter(canvas, palette)
    height, width = canvas.getmaxyx()
    inner = min(width - 4, max(text_width(text) for text, _ in lines) + 4)
    top = max(0, (height - len(lines) - 2) // 2)
    left = max(0, (width - inner) // 2)
    for y in range(top, top + len(lines) + 2):
        p.fill(y, left, inner, "surface0")
    for i, (text, role) in enumerate(lines):
        p.text(top + 1 + i, left + 2, text, role, "surface0", inner - 4, bold=i == 0)
    canvas.cursor = None

