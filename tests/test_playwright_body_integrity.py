from unittest.mock import patch

from ordo_engine.platforms.playwright._common import fill_body_common


class Editor:
    def __init__(self, text):
        self.text = text
        self.filled = []

    def evaluate(self, _script):
        return False

    def inner_text(self):
        return self.text

    def fill(self, value):
        self.filled.append(value)
        self.text = value


class Keyboard:
    def press(self, _keys):
        pass


class Page:
    keyboard = Keyboard()


class Human:
    _modifier = "Meta"

    def human_click(self, _editor):
        pass

    def human_paste_without_select(self, _text):
        # Reproduces Yidian: clipboard path silently leaves stale editor body.
        pass

    def human_type(self, _text, speed="fast"):
        pass


def test_fill_body_replaces_stale_editor_content_before_returning():
    editor = Editor("旧文章正文和旧 frontmatter")
    expected = "这是本次真正要发布的新文章正文。" * 40

    with patch(
        "ordo_engine.platforms.playwright._common.find_editor_element",
        return_value=editor,
    ), patch("ordo_engine.platforms.playwright._common.time.sleep"):
        fill_body_common(Human(), Page(), expected, ".editor", "一点号")

    assert editor.filled == [expected]
    assert editor.text == expected
