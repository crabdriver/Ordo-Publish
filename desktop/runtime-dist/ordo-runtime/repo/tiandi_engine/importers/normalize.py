import re
from typing import Tuple


_TITLE_MARKER_RE = re.compile(r"^\s*\d{1,2}(?:[-_.]\d{1,2})?[-_.\s:：、，]+")


def strip_title_marker(title: str) -> str:
    text = str(title or "").strip()
    while True:
        cleaned = _TITLE_MARKER_RE.sub("", text, count=1).strip()
        if cleaned == text:
            return cleaned
        text = cleaned


def normalize_paste_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.lstrip("\ufeff")
    lines = [ln.rstrip() for ln in text.split("\n")]
    text = "\n".join(lines).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def split_txt_title_body(normalized: str) -> Tuple[str, str]:
    if not normalized:
        return "", ""
    lines = normalized.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            title = stripped
            body_raw = "\n".join(lines[i + 1 :])
            return title, body_raw
    return "", normalized


def body_txt_to_markdown_paragraphs(body_raw: str) -> str:
    body_raw = body_raw.strip()
    if not body_raw:
        return ""
    if "\n\n" in body_raw:
        parts = [p.strip() for p in body_raw.split("\n\n") if p.strip()]
    else:
        parts = [ln.strip() for ln in body_raw.split("\n") if ln.strip()]
    return "\n\n".join(parts)


def parse_markdown_h1(line: str):
    stripped = line.strip()
    if stripped == "#":
        return ""
    if stripped.startswith("# ") and not stripped.startswith("##"):
        return stripped[2:].strip()
    return None


def split_markdown_title_body(content: str, fallback_title: str) -> Tuple[str, str]:
    lines = content.splitlines()
    for index, line in enumerate(lines):
        title = parse_markdown_h1(line)
        if title is None:
            continue
        body_lines = lines[:index] + lines[index + 1 :]
        body = "\n".join(body_lines)
        return title or fallback_title, body
    return fallback_title, content


def split_paste_title_body(normalized: str, fallback_title: str = "Untitled") -> Tuple[str, str]:
    if not normalized:
        return fallback_title, ""
    lines = normalized.split("\n")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        markdown_title = parse_markdown_h1(line)
        if markdown_title is not None:
            body_raw = "\n".join(lines[index + 1 :])
            return markdown_title or fallback_title, body_txt_to_markdown_paragraphs(body_raw)
        plain_title, body_raw = split_txt_title_body(normalized)
        return (plain_title or fallback_title), body_txt_to_markdown_paragraphs(body_raw)
    return fallback_title, ""
