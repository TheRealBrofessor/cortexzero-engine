"""Live type-ahead prompt: suggestions update under the input line on every keystroke.

Plain termios (no extra dependency). Keys: Enter submits, Tab takes the first suggestion,
Backspace, Ctrl-U clears, Ctrl-C/Ctrl-D quits. Falls back to input() when not on a terminal.
"""
from __future__ import annotations

import os
import sys


def read_line(prompt: str, suggest, max_hints: int = 3) -> str | None:
    """One line with live hints. `suggest(text) -> list[str]`. None = user quit."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        try:
            return input(prompt)
        except EOFError:
            return None
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    buf = ""
    hints: list[str] = []

    def draw():
        out = sys.stdout
        out.write("\r\x1b[J" + prompt + buf)                       # redraw line, clear below
        if hints:
            out.write("\x1b7")                                        # save cursor
            for h in hints:
                out.write("\r\n\x1b[2m  → " + h[: max(10, os.get_terminal_size().columns - 6)] + "\x1b[0m")
            out.write("\x1b8")                                        # restore cursor
        out.flush()

    try:
        tty.setcbreak(fd)
        draw()
        while True:
            ch = os.read(fd, 4).decode("utf-8", "ignore")
            if ch in ("\x03", "\x04"):
                sys.stdout.write("\r\x1b[J\n")
                return None
            if ch in ("\r", "\n"):
                sys.stdout.write("\r\x1b[J" + prompt + buf + "\n")
                sys.stdout.flush()
                return buf
            if ch == "\t" and hints:
                buf = hints[0]
            elif ch in ("\x7f", "\b"):
                buf = buf[:-1]
            elif ch == "\x15":
                buf = ""
            elif ch.startswith("\x1b"):
                continue                                              # arrows etc.: ignored
            elif ch.isprintable():
                buf += ch
            hints = suggest(buf)[:max_hints] if len(buf.strip()) >= 2 else []
            draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
