from pathlib import Path
from unittest.mock import patch

import pytest

from ordo_engine.platforms.playwright._common import upload_cover_common
from ordo_engine.platforms.playwright_bilibili.publisher import BilibiliPlaywrightPublisher
from ordo_engine.platforms.playwright_bilibili.locators import BilibiliLocators
from ordo_engine.platforms.playwright_jianshu.publisher import JianshuPlaywrightPublisher
from ordo_engine.platforms.playwright_jianshu.locators import JianshuLocators
from ordo_engine.platforms.playwright_toutiao.publisher import ToutiaoPlaywrightPublisher
from ordo_engine.platforms.playwright_yidian.publisher import YidianPlaywrightPublisher
from ordo_engine.platforms.playwright_zhihu.locators import ZhihuLocators


class Locator:
    def __init__(self, *, count=1, attrs=None, text="", enabled=True, on_set=None):
        self._count = count
        self.attrs = attrs or {}
        self.clicked = 0
        self.files = []
        self.children = {}
        self.text = text
        self.enabled = enabled
        self.on_set = on_set
        self.click_kwargs = []

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def nth(self, _index):
        return self

    def click(self, **_kwargs):
        self.clicked += 1
        self.click_kwargs.append(_kwargs)

    def get_attribute(self, name):
        return self.attrs.get(name)

    def locator(self, selector):
        return self.children.get(selector, Locator(count=0))

    def wait_for(self, **_kwargs):
        return None

    def set_input_files(self, value):
        self.files.append(value)
        if self.on_set:
            self.on_set()

    def is_visible(self):
        return True

    def is_enabled(self):
        return self.enabled

    def inner_text(self):
        return self.text


class MappingPage:
    def __init__(self, mapping):
        self.mapping = mapping
        self.evaluations = []
        self.url = "https://example.test/editor"

    def locator(self, selector):
        return self.mapping.get(selector, Locator(count=0))

    def wait_for_function(self, *_args, **_kwargs):
        return None

    def evaluate(self, script):
        self.evaluations.append(script)
        return ""

    def goto(self, url, **_kwargs):
        self.url = url


class FileChooser:
    def __init__(self):
        self.files = []

    def set_files(self, path):
        self.files.append(path)


class FileChooserInfo:
    def __init__(self, chooser):
        self.value = chooser

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FileChooserPage(MappingPage):
    def __init__(self, mapping, chooser):
        super().__init__(mapping)
        self.chooser = chooser

    def expect_file_chooser(self, **_kwargs):
        return FileChooserInfo(self.chooser)


class Human:
    def human_click(self, locator):
        locator.click()

    def human_wait(self, *_args):
        pass


def cover(tmp_path: Path) -> Path:
    path = tmp_path / "cover.png"
    path.write_bytes(b"png")
    return path


def test_common_cover_upload_fails_closed_when_input_is_missing(tmp_path):
    with pytest.raises(RuntimeError, match="未找到测试平台封面上传 input"):
        upload_cover_common(MappingPage({}), cover(tmp_path), "input[type=file]", "测试平台")


def test_common_cover_upload_requires_visible_completion_evidence(tmp_path):
    file_input = Locator()
    page = MappingPage({
        "input[type=file]": file_input,
        ".uploaded-preview": Locator(count=0),
    })
    with pytest.raises(RuntimeError, match="上传完成证据"):
        upload_cover_common(
            page, cover(tmp_path), "input[type=file]", "测试平台",
            success_selector=".uploaded-preview",
        )


def test_common_cover_upload_requires_new_preview_evidence(tmp_path):
    preview = Locator(attrs={"src": "https://cdn.test/old.jpg"})
    file_input = Locator(on_set=lambda: preview.attrs.update(src="https://cdn.test/new.jpg"))
    page = MappingPage({"input[type=file]": file_input, ".uploaded-preview": preview})

    upload_cover_common(
        page, cover(tmp_path), "input[type=file]", "测试平台",
        success_selector=".uploaded-preview",
    )

    assert file_input.files == [str(cover(tmp_path).resolve())]


def test_common_cover_upload_rejects_local_blob_preview(tmp_path):
    preview = Locator(attrs={"src": "https://cdn.test/old.jpg"})
    file_input = Locator(on_set=lambda: preview.attrs.update(src="blob:local-preview"))
    page = MappingPage({"input[type=file]": file_input, ".uploaded-preview": preview})

    with patch("ordo_engine.platforms.playwright._common.time.time", side_effect=[0, 31]), pytest.raises(
        RuntimeError, match="本次封面上传完成证据"
    ):
        upload_cover_common(
            page, cover(tmp_path), "input[type=file]", "测试平台",
            success_selector=".uploaded-preview",
        )


def test_zhihu_cover_success_locator_matches_confirmed_cover_image():
    assert ZhihuLocators.COVER_UPLOAD_SUCCESS == 'img[alt="封面图"]'


def test_bilibili_cover_success_locator_matches_confirmed_cover_image():
    assert BilibiliLocators.COVER_UPLOAD_SUCCESS == 'img[alt="封面图片"]'


def test_toutiao_opens_cover_picker_before_setting_file(tmp_path):
    radio = Locator(attrs={"class": "byte-radio-inner checked"})
    add = Locator()
    local_upload = Locator()
    file_input = Locator()
    uploaded = Locator()
    confirm = Locator()
    page = MappingPage({
        'label:has-text("单图") .byte-radio-inner': radio,
        ".article-cover-add, .article-cover-img-replace": add,
        'button:visible:has-text("本地上传")': local_upload,
        '#upload-drag-input, .btn-upload-handle input[type="file"], input[type="file"][accept*="image"]': file_input,
        ".pic-select-image-item:has(.success)": uploaded,
        '.byte-drawer-wrapper button:visible:has-text("确定")': confirm,
    })
    publisher = object.__new__(ToutiaoPlaywrightPublisher)
    publisher.page = page
    publisher.human = Human()

    publisher.upload_cover(cover(tmp_path))

    assert add.clicked == 1
    assert local_upload.clicked == 1
    assert file_input.files == [str(cover(tmp_path).resolve())]
    assert uploaded.clicked == 1
    assert confirm.clicked == 1


def test_toutiao_publish_uses_normal_checked_click_path():
    publisher = object.__new__(ToutiaoPlaywrightPublisher)
    publisher.page = object()
    publisher.human = object()

    with patch(
        "ordo_engine.platforms.playwright_toutiao.publisher.click_publish_common"
    ) as click:
        publisher.click_publish()

    click.assert_called_once()


def test_yidian_opens_single_cover_picker_before_setting_file(tmp_path):
    single = Locator()
    cover_item = Locator()
    chooser = FileChooser()
    page = FileChooserPage({
        'text="单图"': single,
        ".article-cover-container .cover-setter:not([style*='display: none']) .cover-item": cover_item,
    }, chooser)
    publisher = object.__new__(YidianPlaywrightPublisher)
    publisher.page = page
    publisher.human = Human()

    publisher.upload_cover(cover(tmp_path))

    assert single.clicked == 1
    assert cover_item.clicked == 1
    assert chooser.files == [str(cover(tmp_path).resolve())]


def test_toutiao_save_failure_blocks_before_submit():
    feedback = Locator(text="保存失败")
    publisher = object.__new__(ToutiaoPlaywrightPublisher)
    publisher.page = MappingPage({
        '[role="alert"], [role="status"], .toast, .Toast': feedback,
    })

    with patch(
        "ordo_engine.platforms.playwright_toutiao.publisher.should_declare_ai",
        return_value=False,
    ), pytest.raises(RuntimeError, match="保存失败"):
        publisher.configure_settings(type("Article", (), {
            "title": "标题", "body": "正文", "ai_declaration_mode": "auto",
        })())


def test_bilibili_enables_custom_cover_then_uploads_in_editor_frame(tmp_path):
    label = Locator()
    parent = Locator()
    switch = Locator(attrs={"class": "vui_switch--switch"})
    parent.children[".vui_switch--switch"] = switch
    label.children["xpath=.."] = parent
    upload = Locator()
    preview = Locator(attrs={"src": "https://cdn.test/old.jpg"})
    file_input = Locator(on_set=lambda: preview.attrs.update(src="https://cdn.test/uploaded.jpg"))
    confirm = Locator()
    frame = MappingPage({
        '.form-item-label:has-text("自定义封面"), label:has-text("自定义封面")': label,
        "div.upload-button": upload,
        'input[type="file"]': file_input,
        'button:visible:has-text("确定")': confirm,
        'img[alt="封面图片"]': preview,
    })
    publisher = object.__new__(BilibiliPlaywrightPublisher)
    publisher._editor_frame = frame

    publisher.upload_cover(cover(tmp_path))

    assert switch.clicked == 1
    assert upload.clicked == 1
    assert file_input.files == [str(cover(tmp_path).resolve())]
    assert confirm.clicked == 1


def test_bilibili_iframe_navigation_text_is_not_publish_evidence():
    frame = MappingPage({})
    frame.evaluate = lambda _script: "导航：已发布\n草稿箱"
    publisher = object.__new__(BilibiliPlaywrightPublisher)
    publisher._editor_frame = frame
    publisher.page = MappingPage({})
    publisher._article = type("Article", (), {"title": "目标标题"})()

    with patch("ordo_engine.platforms.playwright._common.time.sleep"):
        result = publisher.verify_result("publish")

    assert result.status == "submitted_unverified"


def test_bilibili_disabled_publish_button_fails_pre_submit():
    disabled = Locator(enabled=False)
    frame = MappingPage({
        'button.vui_button--blue:visible:has-text("发布")': disabled,
    })
    publisher = object.__new__(BilibiliPlaywrightPublisher)
    publisher._editor_frame = frame

    with pytest.raises(RuntimeError, match="发布按钮仍不可用"):
        publisher.configure_settings(object())


def test_jianshu_clicks_image_tool_then_uploads_cover(tmp_path):
    image_tool = Locator()
    file_input = Locator()
    page = MappingPage({
        "a.fa.fa-picture-o": image_tool,
        "input#kalamu-upload-image": file_input,
    })
    publisher = object.__new__(JianshuPlaywrightPublisher)
    publisher.page = page
    publisher.human = Human()

    publisher.upload_cover(cover(tmp_path))

    assert image_tool.clicked == 1
    assert file_input.files == [str(cover(tmp_path).resolve())]


def test_jianshu_uses_exact_submit_button_with_forced_click():
    submit = Locator()
    page = MappingPage({JianshuLocators.PUBLISH_BUTTON_SELECTOR: submit})
    publisher = object.__new__(JianshuPlaywrightPublisher)
    publisher.page = page
    publisher.human = Human()

    publisher.click_publish()

    assert submit.click_kwargs == [{"force": True}]
