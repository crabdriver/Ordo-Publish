import re

import markdown
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag


MARKDOWN_EXTENSIONS = ["extra", "sane_lists", "nl2br"]


def normalize_markdown_source(markdown_text):
    content = markdown_text.strip()
    if not content:
        return ""

    lines = content.splitlines()
    normalized_lines = []

    def is_cn_enumeration(line):
        return bool(re.match(r"^\d+[、]\s*", line.strip()))

    for index, line in enumerate(lines):
        stripped = line.strip()
        if (
            stripped
            and is_cn_enumeration(line)
            and normalized_lines
            and normalized_lines[-1].strip()
        ):
            normalized_lines.append("")
        normalized_lines.append(line)

        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if stripped and is_cn_enumeration(line) and next_line and is_cn_enumeration(lines[index + 1]):
            normalized_lines.append("")

    content = "\n".join(normalized_lines).strip()
    return f"{content}\n" if content else ""


def render_markdown_soup(markdown_text, extensions=None):
    active_extensions = extensions or MARKDOWN_EXTENSIONS
    raw_html = markdown.markdown(normalize_markdown_source(markdown_text), extensions=active_extensions)
    return BeautifulSoup(raw_html, "html.parser")


def render_markdown_html(markdown_text, extensions=None):
    soup = render_markdown_soup(markdown_text, extensions=extensions)
    return "".join(str(node) for node in soup.contents if str(node).strip())


def render_markdown_plain_text(markdown_text, extensions=None):
    soup = render_markdown_soup(markdown_text, extensions=extensions)
    blocks = []

    def normalize_inline_text(text):
        text = text.replace("\xa0", " ")
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        return text.strip()

    def collect(node):
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag):
            return ""
        if node.name == "br":
            return "\n"
        if node.name in {"ul", "ol"}:
            items = [collect(child) for child in node.find_all("li", recursive=False)]
            return "\n".join(item for item in items if item.strip())
        if node.name in {"p", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}:
            return normalize_inline_text("".join(collect(child) for child in node.children))
        return "".join(collect(child) for child in node.children)

    for child in soup.contents:
        text = collect(child)
        if text.strip():
            blocks.append(text.strip())

    return "\n\n".join(blocks).strip()
