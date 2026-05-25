#!/usr/bin/env python3
"""Split a combined 文章N markdown collection into individual publishable files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tiandi_engine.importers.normalize import body_txt_to_markdown_paragraphs, strip_title_marker

ARTICLE_START_RE = re.compile(
    r"^(?:文章(\d+)[：:]\s*(.+)|##\s*文章(\d+)[：:]\s*(.+))\s*$"
)


def _sanitize_filename(title: str) -> str:
    text = strip_title_marker(title.strip())
    for ch in '<>:"/\\|?*':
        text = text.replace(ch, "_")
    return text[:80] or "untitled"


def split_collection(text: str) -> list[tuple[int, str, str]]:
    lines = text.splitlines()
    starts: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = ARTICLE_START_RE.match(line.strip())
        if not match:
            continue
        if match.group(1):
            number = int(match.group(1))
            title = match.group(2).strip()
        else:
            number = int(match.group(3))
            title = match.group(4).strip()
        starts.append((index, number, title))

    if not starts:
        raise ValueError("未找到任何「文章N」分段标记")

    chunks: list[tuple[int, str, str]] = []
    for idx, (line_no, number, title) in enumerate(starts):
        end_line = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        body_lines = lines[line_no + 1 : end_line]
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        if body_lines and body_lines[-1].strip() == "---":
            body_lines.pop()
        body_raw = "\n".join(body_lines).strip()
        if body_raw.endswith("---"):
            body_raw = body_raw[:-3].strip()
        body_md = body_txt_to_markdown_paragraphs(body_raw)
        chunks.append((number, title, body_md))
    return chunks


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: split_collection_articles.py <source.md> <output_dir>")
        return 2

    source = Path(sys.argv[1]).expanduser().resolve()
    output_dir = Path(sys.argv[2]).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    text = source.read_text(encoding="utf-8")
    chunks = split_collection(text)

    written = []
    for number, title, body_md in chunks:
        safe_title = _sanitize_filename(title)
        filename = f"{number:02d}_{safe_title}.md"
        path = output_dir / filename
        content = f"# {strip_title_marker(title)}\n\n{body_md}\n"
        path.write_text(content, encoding="utf-8")
        written.append(path)

    print(f"[OK] 已拆分 {len(written)} 篇文章到 {output_dir}")
    for path in written:
        print(path.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
