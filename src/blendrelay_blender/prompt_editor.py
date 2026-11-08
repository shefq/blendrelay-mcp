# SPDX-License-Identifier: GPL-3.0-or-later
"""Text editing and visual-line navigation independent of Blender's event API."""
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class Line:
    start: int
    end: int


def wrap_lines(text, width, measure):
    lines = []
    start = 0
    for paragraph in text.split('\n'):
        end = start + len(paragraph)
        position = start
        if position == end:
            lines.append(Line(position, position))
        while position < end:
            low, high = position + 1, end
            fit = position + 1
            while low <= high:
                middle = (low + high) // 2
                if measure(text[position:middle]) <= max(1, width):
                    fit, low = middle, middle + 1
                else:
                    high = middle - 1
            if fit < end:
                boundary = max(text.rfind(' ', position, fit), text.rfind('\t', position, fit))
                if boundary >= position:
                    fit = boundary + 1
            lines.append(Line(position, fit))
            position = fit
        start = end + 1
    return lines


class Editor:
    def __init__(self):
        self.text = ''
        self.cursor = self.anchor = self.scroll = 0
        self.visible = 3
        self.lines = [Line(0, 0)]
        self.preferred_x = None
        self.reveal = True
        self._layout_key = None
        self._undo, self._redo = [], []
        self._last_edit = (None, 0)
        self.identity = None
        self.row_hint = None

    @property
    def selection(self):
        return min(self.cursor, self.anchor), max(self.cursor, self.anchor)

    @property
    def selected_text(self):
        a, b = self.selection
        return self.text[a:b]

    def sync(self, text, identity=None):
        if text != self.text or identity != self.identity:
            self.text, self.identity = text, identity
            self.cursor = self.anchor = len(text)
            self.scroll = 0
            self._undo.clear()
            self._redo.clear()
            self._last_edit = (None, 0)
            self.preferred_x = None
            self.row_hint = None
            self.reveal = True

    def layout(self, width, measure, visible, font_key=None):
        key = (self.text, width, font_key)
        if key != self._layout_key:
            self.lines = wrap_lines(self.text, width, measure)
            self._layout_key = key
        self.visible = max(1, visible)
        self.scroll = min(self.scroll, max(0, len(self.lines) - self.visible))
        if self.reveal:
            row = self.row()
            self.scroll = max(0, min(self.scroll, row))
            if row >= self.scroll + self.visible:
                self.scroll = row - self.visible + 1
            self.reveal = False

    def row(self):
        if self.row_hint is not None and 0 <= self.row_hint < len(self.lines):
            line = self.lines[self.row_hint]
            if line.start <= self.cursor <= line.end:
                return self.row_hint
        # At a soft-wrap boundary the caret belongs to the following row.
        for index, line in enumerate(self.lines):
            if self.cursor < line.end or (self.cursor == line.end and
                (index == len(self.lines) - 1 or self.lines[index + 1].start != self.cursor)):
                return index
        return len(self.lines) - 1

    def hit(self, row, x, measure):
        line = self.lines[max(0, min(len(self.lines) - 1, row))]
        previous = 0
        for index in range(line.start, line.end):
            current = measure(self.text[line.start:index + 1])
            if x < (previous + current) / 2:
                return index
            previous = current
        return line.end

    def place(self, position, extend=False, vertical=False):
        self.row_hint = None
        self.cursor = max(0, min(len(self.text), position))
        if not extend:
            self.anchor = self.cursor
        if not vertical:
            self.preferred_x = None
        self.reveal = True
        self._last_edit = (None, 0)

    def word(self, position, direction):
        if direction < 0:
            while position and self.text[position - 1].isspace():
                position -= 1
            while position and not self.text[position - 1].isspace():
                position -= 1
        else:
            while position < len(self.text) and not self.text[position].isspace():
                position += 1
            while position < len(self.text) and self.text[position].isspace():
                position += 1
        return position

    def move(self, direction, extend=False, word=False, measure=len):
        a, b = self.selection
        row = self.row()
        if direction in ('LEFT', 'RIGHT'):
            delta = -1 if direction == 'LEFT' else 1
            position = (a if delta < 0 else b) if a != b and not extend else (
                self.word(self.cursor, delta) if word else self.cursor + delta)
        elif direction in ('HOME', 'END'):
            position = (0 if direction == 'HOME' else len(self.text)) if word else (
                self.lines[row].start if direction == 'HOME' else self.lines[row].end)
        else:
            if self.preferred_x is None:
                self.preferred_x = measure(self.text[self.lines[row].start:self.cursor])
            delta = {'UP': -1, 'DOWN': 1, 'PAGEUP': -self.visible, 'PAGEDOWN': self.visible}[direction]
            target_row = max(0, min(len(self.lines) - 1, row + delta))
            position = self.hit(target_row, self.preferred_x, measure)
        self.place(position, extend, vertical=direction in ('UP', 'DOWN', 'PAGEUP', 'PAGEDOWN'))
        if direction in ('HOME', 'END') and not word:
            self.row_hint = row
        elif direction in ('UP', 'DOWN', 'PAGEUP', 'PAGEDOWN'):
            self.row_hint = target_row

    def replace(self, value, kind='edit'):
        value = value.replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
        a, b = self.selection
        if self.text[a:b] == value:
            self.cursor = self.anchor = a + len(value)
            self.reveal = True
            return
        now = time.monotonic()
        if kind != 'typing' or self._last_edit[0] != kind or now - self._last_edit[1] > .7 or a != b:
            self._undo.append((self.text, self.cursor, self.anchor))
            self._undo = self._undo[-100:]
        self._redo.clear()
        self.text = self.text[:a] + value + self.text[b:]
        self.cursor = self.anchor = a + len(value)
        self.preferred_x = None
        self.row_hint = None
        self.reveal = True
        self._last_edit = (kind, now)

    def delete(self, backward=False, word=False):
        if self.cursor == self.anchor:
            step = -1 if backward else 1
            self.anchor = self.word(self.cursor, step) if word else max(0, min(len(self.text), self.cursor + step))
        self.replace('')

    def undo(self, redo=False):
        source, target = (self._redo, self._undo) if redo else (self._undo, self._redo)
        if source:
            target.append((self.text, self.cursor, self.anchor))
            self.text, self.cursor, self.anchor = source.pop()
            self.preferred_x = None
            self.row_hint = None
            self.reveal = True
            self._last_edit = (None, 0)

    def select_all(self):
        self.anchor, self.cursor = 0, len(self.text)
        self.reveal = True
        self._last_edit = (None, 0)

    def select_word(self, position):
        position = max(0, min(len(self.text), position))
        a = b = position
        if a == len(self.text) and a:
            a -= 1
        if a < len(self.text):
            whitespace = self.text[a].isspace()
            b = a + 1
            while a and self.text[a - 1].isspace() == whitespace:
                a -= 1
            while b < len(self.text) and self.text[b].isspace() == whitespace:
                b += 1
        self.anchor, self.cursor = a, b
        self.reveal = True

    def scroll_by(self, amount):
        self.scroll = max(0, min(max(0, len(self.lines) - self.visible), self.scroll + amount))
        self.reveal = False
