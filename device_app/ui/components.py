from __future__ import annotations

from collections.abc import Iterable


def wrap_text(font: object, text: str, max_width: int) -> list[str]:
    """Wrap text using the active Pygame font's measured pixel width."""

    lines: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            proposed = f"{current} {word}"
            if font.size(proposed)[0] <= max_width:
                current = proposed
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def visible_slice(lines: Iterable[str], offset: int, count: int) -> list[str]:
    materialized = list(lines)
    max_offset = max(0, len(materialized) - count)
    start = min(max(0, offset), max_offset)
    return materialized[start : start + count]
