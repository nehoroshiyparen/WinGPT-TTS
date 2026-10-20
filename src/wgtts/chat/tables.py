"""A table stays a grid in history and becomes labelled rows for speech."""

import html
import re
from dataclasses import dataclass

SEPARATOR = re.compile(r":?-{3,}:?")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
BREAK = re.compile(r"<br\s*/?>", re.I)


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]

    def markdown(self):
        def cell(value):
            value = html.escape(value, quote=False).replace("\\", "\\\\").replace("|", "\\|")
            return value.replace("\r\n", "\n").replace("\n", "<br>")

        def line(values):
            return "| " + " | ".join(cell(value) for value in values) + " |"

        return "\n".join(
            [line(self.headers), line(["---"] * len(self.headers))]
            + [line(row) for row in self.rows]
        )

    def speech(self):
        def plain(value):
            value = html.unescape(BREAK.sub("; ", value))
            value = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", value)
            value = value.replace("`", "").replace("**", "").replace("__", "")
            return " ".join(value.split())

        headers = [plain(value) or f"Столбец {i}" for i, value in enumerate(self.headers, 1)]
        lines = ["Таблица."]
        if not self.rows:
            lines.append("Столбцы: " + "; ".join(headers) + ".")
        for index, row in enumerate(self.rows, 1):
            values = [f"{header}: {plain(value) or 'пусто'}" for header, value in zip(headers, row)]
            lines.append(f"Строка {index}. " + "; ".join(values) + ".")
        return "\n".join([*lines, "Конец таблицы."])


def _cells(line):
    """Split pipes without splitting escaped pipes; retain empty edge cells."""
    if "|" not in line:
        return None
    line = line.strip()
    cells, current = [], []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line) and line[index + 1] in "\\|":
            index += 1
            current.append(line[index])
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    if line.startswith("|"):
        cells.pop(0)
    # A trailing escaped pipe belongs to the last cell.
    trailing_slashes = len(line[:-1]) - len(line[:-1].rstrip("\\"))
    if line.endswith("|") and trailing_slashes % 2 == 0:
        cells.pop()
    return cells


def tables_for_speech(text):
    """Convert only complete Markdown tables, preserving prose and fenced code."""
    lines = text.splitlines(keepends=True)
    output = []
    index = 0
    fence = None
    while index < len(lines):
        line = lines[index]
        marker = FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif (
                token[0] == fence[0]
                and len(token) >= len(fence)
                and not line[marker.end() :].strip()
            ):
                fence = None
        headers = _cells(line) if fence is None else None
        separator = _cells(lines[index + 1]) if headers and index + 1 < len(lines) else None
        if (
            not separator
            or len(separator) != len(headers)
            or not all(SEPARATOR.fullmatch(value) for value in separator)
        ):
            output.append(line)
            index += 1
            continue
        end = index + 2
        rows = []
        while end < len(lines):
            row = _cells(lines[end])
            if row is None or len(row) != len(headers):
                break
            rows.append(row)
            end += 1
        suffix = "\n" if lines[end - 1].endswith("\n") else ""
        output.append(Table(headers, rows).speech() + suffix)
        index = end
    return "".join(output)
