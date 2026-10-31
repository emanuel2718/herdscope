"""A small true-color terminal canvas: styled cells, row diffing, ANSI and input decoding."""

import codecs
import os
import re
import select
import sys
import termios
import threading
import time
import tty
import unicodedata
from collections import namedtuple
from functools import lru_cache

# A cell's look. Colors are "#rrggbb", a palette index, or None for the terminal's default.
# A namedtuple rather than typing.NamedTuple: importing typing adds milliseconds to every start.
Style = namedtuple("Style", "fg bg bold dim italic underline", defaults=(None, None, False, False, False, False))


def color_codes(value, layer):
    if value is None:
        return [str(layer + 1)]
    if isinstance(value, int):
        if value < 8:
            return [str(layer - 8 + value)]
        if value < 16:
            return [str(layer + 44 + value)]
        return [str(layer), "5", str(value)]
    return [str(layer), "2", *(str(int(value[i : i + 2], 16)) for i in (1, 3, 5))]


@lru_cache(maxsize=4096)
def sgr(style):
    codes = ["0"]
    codes += [code for flag, code in zip(style[2:], "1234") if flag]
    return "\x1b[" + ";".join(codes + color_codes(style.fg, 38) + color_codes(style.bg, 48)) + "m"


def char_width(char):
    if char < "̀":
        return 1
    # Combining marks, variation selectors (emoji style) and joiners attach to the previous glyph.
    if unicodedata.combining(char) or "\ufe00" <= char <= "\ufe0f" or char == "\u200d":
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def text_width(text):
    return len(text) if text.isascii() else sum(map(char_width, text))  # ASCII is one cell a char.


def clip(text, width, ellipsis="…"):
    """`text` cut to `width` cells, ending in an ellipsis if it had to be cut."""
    if width <= 0:
        return ""
    if text_width(text) <= width:
        return text
    if text.isascii():
        return text[: width - len(ellipsis)] + ellipsis
    result, used = [], 0
    for char in text:
        size = char_width(char)
        if used + size > width - len(ellipsis):
            break
        result.append(char)
        used += size
    return "".join(result) + ellipsis


def clip_start(text, width, ellipsis="…"):
    """`text` cut to `width` cells from the start, keeping its end, as paths read best: …/web/src."""
    if width <= 0:
        return ""
    if text_width(text) <= width:
        return text
    if text.isascii():
        return ellipsis + text[len(text) - width + len(ellipsis) :]
    return ellipsis + clip(text[::-1], width - len(ellipsis), "")[::-1]


def first_difference(a, b):
    """Where equal-length rows `a` and `b` first differ, given they do: a binary search whose
    steps compare slices in C, so a one-cell change in a long row costs a few steps, not one
    per cell."""
    low, high = 0, len(a) - 1
    while low < high:
        middle = (low + high) // 2
        if a[low : middle + 1] == b[low : middle + 1]:
            low = middle + 1
        else:
            high = middle
    return low


class Canvas:
    def __init__(self, height, width):
        self.height, self.width = height, width
        self.base = Style()
        self.cursor = None
        self.erase()

    def getmaxyx(self):
        return self.height, self.width

    def erase(self, style=None):
        self.base = style or self.base
        blank = (" ", self.base)
        self.cells = [[blank] * self.width for _ in range(self.height)]

    def put(self, y, x, text, style, limit=None):
        """Draw text clipped to `limit` cells; returns the column after it."""
        if not 0 <= y < self.height:
            return x
        right = self.width if limit is None else min(self.width, x + limit)
        row = self.cells[y]
        if x >= 0 and text.isascii():
            # Most text: a cell per character, written as one slice. Only the ends can split
            # a wide glyph, so only they need fixing.
            end = max(x, min(x + len(text), right))
            if end > x:
                if row[x][0] == "" and x > 0:
                    row[x - 1] = (" ", row[x - 1][1])
                row[x:end] = [(char, style) for char in text[: end - x]]
                if end < self.width and row[end][0] == "":
                    row[end] = (" ", row[end][1])
            return end
        for char in text:
            size = char_width(char)
            if size == 0:
                # Attach to the glyph before; past a wide glyph, that is two cells back.
                at = x - 1 if x > 0 and row[x - 1][0] else x - 2
                if 0 <= at < x <= right and row[at][0]:
                    row[at] = (row[at][0] + char, row[at][1])
                continue
            if x + size > right:
                break
            if x >= 0:
                # Never leave half of a wide glyph behind.
                if row[x][0] == "" and x > 0:
                    row[x - 1] = (" ", row[x - 1][1])
                row[x] = (char, style)
                if size == 2:
                    row[x + 1] = ("", style)
                if x + size < self.width and row[x + size][0] == "":
                    row[x + size] = (" ", row[x + size][1])
            x += size
        return x

    def fill(self, y, x, width, style, char=" "):
        if 0 <= y < self.height:
            start, end = max(0, x), min(self.width, x + width)
            if start < end:
                self.cells[y][start:end] = [(char, style)] * (end - start)

    def text(self):
        return "\n".join("".join(char for char, _ in row) for row in self.cells)


# Preview decoding: Herdr's pane.read "ansi" format is SGR-colored lines.
ESCAPE = re.compile(r"\x1b(?:\[([0-?]*)([ -/]*)([@-~])|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[P^_][^\x1b]*(?:\x1b\\)?|.)")


def extended_color(values, colon=False):
    """Read a 38/48 color body (5;n or 2;r;g;b); returns (color, parameters consumed)."""
    if len(values) > 1 and values[0] == "5" and values[1].isdigit():
        return min(255, int(values[1])), 2
    if values[:1] == ["2"]:
        # The colon form may carry a colorspace slot: 38:2::r:g:b.
        rgb = values[2:5] if colon and len(values) > 4 else values[1:4]
        if len(rgb) == 3 and all(v.isdigit() for v in rgb):
            return "#" + "".join(f"{min(255, int(v)):02x}" for v in rgb), 4
    return None, len(values)


def apply_sgr(state, params, base):
    fg, bg, bold, dim, italic, underline, reverse = state
    parts = params.split(";") if params else ["0"]
    i = 0
    while i < len(parts):
        part = parts[i]
        if ":" in part:
            values = part.split(":")
            if values[0] in ("38", "48"):
                value, _ = extended_color(values[1:], colon=True)
                if value is not None:
                    fg, bg = (value, bg) if values[0] == "38" else (fg, value)
            elif values[0] == "4":
                underline = values[1:2] != ["0"]
            i += 1
            continue
        code = int(part) if part.isdigit() else 0
        if code in (38, 48):
            value, used = extended_color(parts[i + 1 :])
            if value is not None:
                fg, bg = (value, bg) if code == 38 else (fg, value)
            i += used
        elif code == 0:
            fg, bg, bold, dim, italic, underline, reverse = base.fg, base.bg, False, False, False, False, False
        elif code in (1, 2, 3, 4, 7):
            bold, dim, italic, underline, reverse = (
                bold or code == 1,
                dim or code == 2,
                italic or code == 3,
                underline or code == 4,
                reverse or code == 7,
            )
        elif code == 22:
            bold = dim = False
        elif code == 23:
            italic = False
        elif code == 24:
            underline = False
        elif code == 27:
            reverse = False
        elif 30 <= code <= 37 or 90 <= code <= 97:
            fg = code - 30 if code < 90 else code - 82
        elif 40 <= code <= 47 or 100 <= code <= 107:
            bg = code - 40 if code < 100 else code - 92
        elif code == 39:
            fg = base.fg
        elif code == 49:
            bg = base.bg
        i += 1
    return fg, bg, bold, dim, italic, underline, reverse


def parse_ansi(text, base):
    """Decode SGR-colored text into lines of (text, Style) runs; drops other controls.

    A pane's output changes a few lines at a time, so each line's runs are remembered, keyed by
    the line and the colors it starts with: re-reading a busy pane re-decodes only what changed.
    The runs are shared, so callers must not change them.
    """
    state = (base.fg, base.bg, False, False, False, False, False)
    lines = []
    for raw in text.split("\n"):
        runs, state = parse_line(raw, state, base)
        lines.append(runs)
    while lines and not "".join(text for text, _ in lines[-1]).strip():
        lines.pop()
    return lines


@lru_cache(maxsize=4096)
def parse_line(raw, state, base):
    """One line's (text, Style) runs, and the SGR state it leaves for the next line."""
    runs, column, position = [], 0, 0
    for match in [*ESCAPE.finditer(raw), None]:
        chunk = raw[position : match.start() if match else len(raw)]
        if chunk:
            if chunk.isascii() and chunk.isprintable():  # Most text: kept as is, a cell a character.
                cleaned, column = chunk, column + len(chunk)
            else:
                parts = []
                for char in chunk:
                    if char == "\t":
                        spaces = 8 - column % 8
                        parts.append(" " * spaces)
                        column += spaces
                    elif char == "\xa0":
                        parts.append(" ")
                        column += 1
                    elif char.isprintable():
                        parts.append(char)
                        column += char_width(char)
                cleaned = "".join(parts)
            fg, bg, bold, dim, italic, underline, reverse = state
            if reverse:
                fg, bg = (bg if bg is not None else base.bg), (fg if fg is not None else base.fg)
            if cleaned:
                runs.append((cleaned, Style(fg, bg, bold, dim, italic, underline)))
        if match is None:
            break
        # SGR, but not a private sequence that happens to end in m, like xterm's \x1b[>4;1m.
        if match.group(3) == "m" and not match.group(2) and not match.group(1).startswith(("<", "=", ">", "?")):
            state = apply_sgr(state, match.group(1), base)
        position = match.end()
    return runs, state


# Input decoding. Events: ("key", name), ("text", str), ("paste", str), ("mouse", (button, x, y, pressed)).
CSI = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")
CURSOR = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
TILDE = {"1": "home", "7": "home", "4": "end", "8": "end", "2": "insert", "3": "delete", "5": "pgup", "6": "pgdn",
         "11": "f1"}  # fmt: skip
MODIFIERS = {
    2: "shift-",
    3: "alt-",
    4: "alt-shift-",
    5: "ctrl-",
    6: "ctrl-shift-",
    7: "ctrl-alt-",
    8: "ctrl-alt-shift-",
}
CONTROL = {
    "\r": "enter",
    "\n": "ctrl-j",
    "\t": "tab",
    "\x7f": "backspace",
    "\x08": "backspace",
    "\x00": "ctrl-space",
    "\x1c": "ctrl-\\",
    "\x1d": "ctrl-]",
    "\x1e": "ctrl-^",
    "\x1f": "ctrl-/",
}
PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"
OSC_END = re.compile(r"\x07|\x1b\\")
# Replies to color queries (OSC 10/11) and to the device-attributes fence (DA1).
COLOR_REPLY = re.compile(r"\x1b\](1[01]);rgb:([\da-fA-F]{1,4})/([\da-fA-F]{1,4})/([\da-fA-F]{1,4})(?:\x07|\x1b\\)")
DA1_REPLY = re.compile(r"\x1b\[\?[\d;]*c")


def rgb(*channels):
    """X11 "rgb:" channels of 1-4 hex digits each as "#rrggbb"."""
    return "#" + "".join(f"{round(int(c, 16) * 255 / (16 ** len(c) - 1)):02x}" for c in channels)


def csi_event(params, final):
    fields = params.split(";")
    modifier = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 1
    prefix = MODIFIERS.get(modifier, "")
    if final in CURSOR:
        return "key", prefix + CURSOR[final]
    if final == "~" and fields[0] in TILDE:
        return "key", prefix + TILDE[fields[0]]
    if final == "Z":
        return "key", "shift-tab"
    if final == "u" and fields[0].isdigit():  # CSI-u / kitty keyboard protocol
        code = int(fields[0])
        named = {13: "enter", 27: "esc", 9: "tab", 127: "backspace"}.get(code)
        if named:
            return "key", (prefix if named != "tab" else "shift-" if modifier == 2 else "") + named
        char = chr(code)
        if modifier in (1, 2):
            return "text", char
        return "key", prefix.replace("shift-", "") + char.lower()
    return None


def parse_input(buffer, final=False):
    """Decode one event from the front of `buffer`: (event or None, consumed).

    Returns (None, 0) when more bytes are needed. `final` means no more bytes
    arrived within the escape timeout, so a lone ESC is the Escape key.
    """
    char = buffer[0]
    if buffer.startswith("\x1b]"):
        # A late reply to a color query: an OSC string, never input.
        end = OSC_END.search(buffer)
        if end:
            return None, end.end()
        return (None, len(buffer)) if final or len(buffer) > 512 else (None, 0)
    if buffer.startswith(PASTE_START):
        end = buffer.find(PASTE_END)
        if end < 0:
            return None, 0
        return ("paste", buffer[len(PASTE_START) : end]), end + len(PASTE_END)
    if char == "\x1b":
        if len(buffer) == 1 or (buffer[1] in "[O" and len(buffer) == 2):
            return (("key", "esc"), 1) if final else (None, 0)
        follower = buffer[1]
        if follower == "[":
            if buffer.startswith("\x1b[<"):
                mouse = re.match(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])", buffer)
                if mouse:
                    button, x, y = (int(v) for v in mouse.groups()[:3])
                    return ("mouse", (button, x - 1, y - 1, mouse.group(4) == "M")), mouse.end()
            sequence = CSI.match(buffer)
            if not sequence:
                if final or len(buffer) > 32:
                    return ("key", "esc"), 1
                return None, 0
            return csi_event(sequence.group(1), sequence.group(3)), sequence.end()
        if follower == "O":
            name = {"P": "f1", "Q": "f2", "R": "f3", "S": "f4"}.get(buffer[2], CURSOR.get(buffer[2]))
            return (("key", name) if name else None), 3
        if follower in "\x7f\x08":
            return ("key", "alt-backspace"), 2
        if follower == "\r":
            return ("key", "alt-enter"), 2
        if follower.isprintable():
            return ("key", "alt-" + follower), 2
        return ("key", "esc"), 1
    if char in CONTROL:
        return ("key", CONTROL[char]), 1
    if char < " ":
        return ("key", "ctrl-" + chr(ord(char) + 96)), 1
    if char.isprintable():
        end = 1
        while end < len(buffer) and buffer[end].isprintable() and buffer[end] != "\x1b":
            end += 1
        return ("text", buffer[:end]), end
    return None, 1


class Terminal(Canvas):
    def __init__(self, stdin=None, stdout=None):
        self.input = (stdin or sys.stdin).fileno()
        self.output = (stdout or sys.stdout).fileno()
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.pending = ""
        self.previous = []
        self.drawn_cursor = None
        self.incomplete = None  # When the input started waiting on an unfinished sequence.
        self.waker = os.pipe()
        self.waking = threading.Lock()
        os.set_blocking(self.waker[1], False)
        size = os.get_terminal_size(self.output)
        super().__init__(size.lines, size.columns)

    def __enter__(self):
        self.saved = termios.tcgetattr(self.input)
        tty.setraw(self.input, termios.TCSANOW)  # Keep keys typed while the popup was starting.
        try:
            # Alternate screen, bracketed paste, no autowrap, SGR mouse, bar cursor.
            self.write("\x1b[?1049h\x1b[?2004h\x1b[?7l\x1b[?1000h\x1b[?1006h\x1b[6 q\x1b[2J")
        except BaseException:
            termios.tcsetattr(self.input, termios.TCSADRAIN, self.saved)
            raise
        return self

    def colors(self, timeout=0.3):
        """The terminal's default (foreground, background) as "#rrggbb", or None.

        Asks with OSC 10/11, the way editors detect a dark background; nothing is shown. A
        device-attributes query follows, which every terminal answers, so one that ignores
        color queries costs a single round trip. Keys typed meanwhile stay queued as input.
        """
        self.write("\x1b]10;?\x1b\\\x1b]11;?\x1b\\\x1b[c")
        deadline = time.monotonic() + timeout
        while not DA1_REPLY.search(self.pending) and time.monotonic() < deadline:
            self.read(max(0, deadline - time.monotonic()), wakeable=False)
        found = {match.group(1): rgb(*match.groups()[1:]) for match in COLOR_REPLY.finditer(self.pending)}
        self.pending = DA1_REPLY.sub("", COLOR_REPLY.sub("", self.pending), count=1)
        return (found["10"], found["11"]) if len(found) == 2 else None

    def __exit__(self, *exc):
        try:
            self.write("\x1b[0m\x1b[?1006l\x1b[?1000l\x1b[?7h\x1b[?2004l\x1b[0 q\x1b[?25h\x1b[?1049l")
        finally:
            termios.tcsetattr(self.input, termios.TCSADRAIN, self.saved)
            with self.waking:
                for fd in self.waker:
                    os.close(fd)
                self.waker = None

    def wake(self):
        """Make a pending event() return now, from any thread, so the caller redraws."""
        with self.waking:
            if self.waker:
                try:
                    os.write(self.waker[1], b"!")
                except BlockingIOError:
                    pass  # The pipe is full: a wake-up is already pending.

    def write(self, text):
        data = text.encode()
        while data:
            data = data[os.write(self.output, data) :]

    def getmaxyx(self):
        size = os.get_terminal_size(self.output)
        if (self.height, self.width) != (size.lines, size.columns):
            self.height, self.width = size.lines, size.columns
            self.previous = []
        return self.height, self.width

    def refresh(self):
        output = []
        for y, row in enumerate(self.cells):
            old = self.previous[y] if y < len(self.previous) else None
            if old == row:
                continue
            start, end = 0, len(row)
            if old and len(old) == len(row):
                # Rewrite only the changed span (a spinner is one cell), never splitting a wide glyph.
                start = first_difference(old, row)
                end = len(row) - first_difference(old[::-1], row[::-1])
                while start and not row[start][0]:
                    start -= 1
                while end < len(row) and not row[end][0]:
                    end += 1
            output.append(f"\x1b[{y + 1};{start + 1}H")
            active = None
            for char, style in row[start:end]:
                if not char:
                    continue
                if style != active:
                    output.append(sgr(style))
                    active = style
                output.append(char)
        self.previous = [row[:] for row in self.cells]  # A copy: a caller may draw into the rows in place.
        if output or self.cursor != self.drawn_cursor:
            if self.cursor:
                y, x = self.cursor
                output.append(f"\x1b[{y + 1};{x + 1}H\x1b[?25h")
            self.write("\x1b[?25l" + "".join(output))
            self.drawn_cursor = self.cursor

    def read(self, timeout, wakeable=True):
        """Wait up to `timeout` for input; True if some arrived. A wake() ends the wait early."""
        ready = select.select([self.input, self.waker[0]] if wakeable else [self.input], [], [], timeout)[0]
        if self.waker[0] in ready:
            os.read(self.waker[0], 4096)
        if self.input in ready:
            data = os.read(self.input, 65536)
            if not data:
                raise EOFError
            self.pending += self.decoder.decode(data)
            return True
        return False

    def event(self, timeout):
        """Next input event, or None after `timeout` seconds without one."""
        if not self.pending:
            self.read(timeout)
        while self.pending:
            event, used = parse_input(self.pending)
            if used == 0:
                # Incomplete escape sequence or paste: wait briefly for the rest.
                if self.read(0.03, wakeable=False):
                    continue
                if not self.pending.startswith(PASTE_START):
                    event, used = parse_input(self.pending, final=True)
                elif self.incomplete and time.monotonic() - self.incomplete > 1:
                    # The paste's end marker never came: deliver what arrived.
                    event, used = ("paste", self.pending[len(PASTE_START) :]), len(self.pending)
                if used == 0:
                    self.incomplete = self.incomplete or time.monotonic()
                    return None
            self.incomplete = None
            self.pending = self.pending[used:]
            if event:
                return event
        return None
