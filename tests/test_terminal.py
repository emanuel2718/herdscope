"""The terminal canvas: input decoding, ANSI previews, and minimal redraws."""

import os
import pty
import termios
import threading
import time
import tty
import unittest

from herdscope import render, theme
from herdscope.terminal import Canvas, Style, Terminal, parse_ansi, parse_input, rgb, sgr


class TerminalTests(unittest.TestCase):
    def test_input_decoding(self):
        cases = {
            "\x1b[A": ("key", "up"),
            "\x1b[1;2B": ("key", "shift-down"),
            "\x1b[1;5D": ("key", "ctrl-left"),
            "\x1bOA": ("key", "up"),
            "\x1b[3~": ("key", "delete"),
            "\x1b[6~": ("key", "pgdn"),
            "\x1b[Z": ("key", "shift-tab"),
            "\x1bb": ("key", "alt-b"),
            "\x1b\x7f": ("key", "alt-backspace"),
            "\r": ("key", "enter"),
            "\x0e": ("key", "ctrl-n"),
            "\x19": ("key", "ctrl-y"),
            "\x1f": ("key", "ctrl-/"),
            "\x7f": ("key", "backspace"),
            "\t": ("key", "tab"),
            "\x1b[99;5u": ("key", "ctrl-c"),
            "héllo": ("text", "héllo"),
            "\x1b[<0;5;3M": ("mouse", (0, 4, 2, True)),
            "\x1b[<65;1;1M": ("mouse", (65, 0, 0, True)),
            "\x1b[200~a\nb\x1b[201~": ("paste", "a\nb"),
        }
        for sequence, expected in cases.items():
            self.assertEqual(parse_input(sequence), (expected, len(sequence)), repr(sequence))
        self.assertEqual(parse_input("\x1b"), (None, 0))
        self.assertEqual(parse_input("\x1b", final=True), (("key", "esc"), 1))
        self.assertEqual(parse_input("\x1b[1;"), (None, 0))
        self.assertEqual(parse_input("\x1b[200~unfinished"), (None, 0))
        self.assertEqual(parse_input("abc\x1b[A"), (("text", "abc"), 3))

    def test_preview_decodes_colors_and_drops_other_controls(self):
        base = Style("#ffffff", "#000000")
        text = (
            "\x1b[38;2;1;2;3mrgb\x1b[0m \x1b[38;5;4midx\x1b[39m \x1b[91mbright\x1b[0m\r\n"
            "\x1b[7mrev\x1b[27m\x1b]8;;http://x\x07link\x1b]8;;\x07\tz\x1b[2K\n\x1b[38:2::9:8:7mcolon\n\n  \n"
        )
        lines = parse_ansi(text, base)
        self.assertEqual(len(lines), 3, "trailing blank lines are trimmed")
        self.assertEqual(
            [(t, s.fg) for t, s in lines[0]],
            [("rgb", "#010203"), (" ", "#ffffff"), ("idx", 4), (" ", "#ffffff"), ("bright", 9)],
        )
        self.assertEqual(lines[1][0], ("rev", Style("#000000", "#ffffff")))
        self.assertEqual("".join(t for t, _ in lines[1]), "revlink z", "tab stops count printed columns")
        self.assertEqual(lines[2][0][1].fg, "#090807")

    def test_color_query_replies_are_read_and_never_typed(self):
        self.assertEqual(rgb("b3b3", "b3b3", "b3b3"), "#b3b3b3")
        self.assertEqual(rgb("f", "0", "80"), "#ff0080")
        reply = "\x1b]11;rgb:0000/0000/0000\x1b\\"
        self.assertEqual(parse_input(reply + "x"), (None, len(reply)))
        self.assertEqual(parse_input("\x1b]11;rgb:00"), (None, 0))
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        tty.setraw(slave)
        os.write(master, b"\x1b]10;rgb:b3b3/b3b3/b3b3\x07\x1b]11;rgb:0000/0000/0000\x1b\\\x1b[?62;22cj")
        screen = Terminal(stream, stream)
        self.assertEqual(screen.colors(), ("#b3b3b3", "#000000"))
        self.assertEqual(screen.pending, "j", "a key typed during the query stays queued")
        self.assertIn(b"\x1b]11;?", os.read(master, 1024))
        started = time.monotonic()
        self.assertIsNone(Terminal(stream, stream).colors(timeout=0.2))
        self.assertLess(time.monotonic() - started, 1)

    def test_refresh_rewrites_only_what_changed_and_wake_ends_the_wait(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        termios.tcsetwinsize(slave, (3, 20))
        screen = Terminal(stream, stream)
        screen.put(0, 0, "⠋ working 你好", Style())
        screen.refresh()
        os.read(master, 4096)
        screen.cells = [list(row) for row in screen.cells]
        screen.put(0, 0, "⠙", Style())
        screen.refresh()
        self.assertEqual(os.read(master, 4096), b"\x1b[?25l\x1b[1;1H\x1b[0;39;49m\xe2\xa0\x99", "one cell, not the row")
        screen.cells = [list(row) for row in screen.cells]
        screen.put(0, 11, "x", Style())
        screen.refresh()
        self.assertIn(b"\x1b[1;11H", os.read(master, 4096), "overwriting half a wide glyph rewrites it whole")
        started = time.monotonic()
        threading.Timer(0.05, screen.wake).start()
        self.assertIsNone(screen.event(5))
        self.assertLess(time.monotonic() - started, 1, "wake() ends a long wait")

    def test_keys_typed_before_the_popup_starts_are_kept(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        termios.tcsetwinsize(slave, (5, 20))
        os.write(master, b"cla")
        time.sleep(0.05)

        def drain():  # Leaving the terminal waits for its output to be read, as a real terminal would.
            try:
                while os.read(master, 65536):
                    pass
            except OSError:
                pass  # The test closed the terminal.

        threading.Thread(target=drain, daemon=True).start()
        with Terminal(stream, stream) as screen:
            self.assertEqual(screen.event(0.5), ("text", "cla"))

    def test_drawing_into_the_last_frame_still_repaints(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        termios.tcsetwinsize(slave, (8, 40))
        screen = Terminal(stream, stream)
        screen.refresh()
        os.read(master, 4096)
        render.draw_message(screen, render.Palette(theme.load({})), [("Select Box in the sidebar", "text")])
        screen.refresh()
        self.assertIn(b"Select Box", os.read(master, 4096), "the remote-focus message must show")

    def test_a_paste_without_its_end_is_delivered(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r+b", buffering=0)
        self.addCleanup(stream.close)
        termios.tcsetwinsize(slave, (5, 20))
        tty.setraw(slave)
        screen = Terminal(stream, stream)
        os.write(master, b"\x1b[200~half a paste")
        deadline, event = time.monotonic() + 3, None
        while not event and time.monotonic() < deadline:
            event = screen.event(0.2)
        self.assertEqual(event, ("paste", "half a paste"))

    def test_style_output_and_wide_glyph_cells(self):
        self.assertEqual(sgr(Style("#89b4fa", None, True)), "\x1b[0;1;38;2;137;180;250;49m")
        self.assertEqual(sgr(Style(4, 9)), "\x1b[0;34;101m")
        self.assertEqual(sgr(Style(200, None)), "\x1b[0;38;5;200;49m")
        frame = Canvas(2, 6)
        frame.put(0, 0, "你a", Style())
        self.assertEqual([char for char, _ in frame.cells[0]][:3], ["你", "", "a"])
        frame.put(0, 1, "b", Style())
        self.assertEqual([char for char, _ in frame.cells[0]][:3], [" ", "b", "a"], "no orphaned half glyphs")
        frame.put(1, 0, "你\u0301❤\ufe0fx", Style())
        self.assertEqual(
            [char for char, _ in frame.cells[1]][:4],
            ["你\u0301", "", "❤\ufe0f", "x"],
            "marks and emoji selectors stay with their glyph",
        )
