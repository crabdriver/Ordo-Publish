from pathlib import Path
from unittest.mock import patch

import pytest

from ordo_engine.platforms.playwright import _common as common_module
from ordo_engine.platforms.playwright._common import find_visible_button, upload_cover_common
from ordo_engine.platforms.playwright.base_publisher import PublishClickNoEffect
from ordo_engine.platforms.playwright_bilibili.publisher import BilibiliPlaywrightPublisher
from ordo_engine.platforms.playwright_bilibili.locators import BilibiliLocators
from ordo_engine.platforms.playwright_jianshu.publisher import JianshuPlaywrightPublisher
from ordo_engine.platforms.playwright_jianshu.locators import JianshuLocators
from ordo_engine.platforms.playwright_toutiao.publisher import ToutiaoPlaywrightPublisher
from ordo_engine.platforms.playwright_toutiao.locators import ToutiaoLocators
from ordo_engine.platforms.playwright_yidian.publisher import YidianPlaywrightPublisher
from ordo_engine.platforms.playwright_yidian.locators import YidianLocators
from ordo_engine.platforms.playwright_zhihu.locators import ZhihuLocators


class Locator:
    def __init__(self, *, count=1, attrs=None, text="", enabled=True, on_set=None, on_click=None):
        self._count = count
        self.attrs = attrs or {}
        self.clicked = 0
        self.files = []
        self.children = {}
        self.text = text
        self.enabled = enabled
        self.on_set = on_set
        self.on_click = on_click
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
        if self.on_click:
            self.on_click()

    def get_attribute(self, name, **_kwargs):
        return self.attrs.get(name)

    def bounding_box(self, **_kwargs):
        return self.attrs.get("bounding_box")

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

    def is_checked(self):
        return bool(self.attrs.get("checked"))

    def inner_text(self, **_kwargs):
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


def test_find_visible_button_uses_bounded_dom_text_timeout():
    candidate = Locator(text="确认发布")
    timeouts = []

    def inner_text(**kwargs):
        timeouts.append(kwargs.get("timeout"))
        return "确认发布"

    candidate.inner_text = inner_text
    page = MappingPage({'button:visible:has-text("确认发布")': candidate})

    found = find_visible_button(page, ["确认发布"])

    assert found is candidate
    assert timeouts and all(timeout is not None and timeout <= 1000 for timeout in timeouts)


def _evidence_click():
    click = getattr(common_module, "click_publish_with_evidence", None)
    assert click is not None, "click_publish_with_evidence must exist"
    return click


def test_evidence_click_rejects_disabled_publish_button():
    publish = Locator(text="发布", enabled=False)
    page = MappingPage({'button:visible:has-text("发布")': publish})

    with pytest.raises(PublishClickNoEffect, match="不可交互"):
        _evidence_click()(
            page,
            ["发布"],
            ["确认发布"],
            "测试平台",
            confirm_scope_selector='[role="dialog"]:visible',
            timeout_seconds=0,
        )


def test_evidence_click_rejects_click_without_page_change():
    publish = Locator(text="发布")
    page = MappingPage({'button:visible:has-text("发布")': publish})

    with pytest.raises(PublishClickNoEffect, match="页面无变化"):
        _evidence_click()(
            page,
            ["发布"],
            ["确认发布"],
            "测试平台",
            confirm_scope_selector='[role="dialog"]:visible',
            timeout_seconds=0,
        )


def test_toutiao_confirmation_does_not_accept_generic_confirm():
    publish = Locator(text="预览并发布")
    generic = Locator(text="确定")
    page = MappingPage({
        'button:visible:has-text("预览并发布")': publish,
        'button:visible:has-text("确定")': generic,
    })

    with pytest.raises(PublishClickNoEffect, match="页面无变化"):
        _evidence_click()(
            page,
            ToutiaoLocators.PUBLISH_BUTTON_TEXTS,
            ["确定并发布", "确认发布"],
            "头条号",
            confirm_scope_selector=(
                '[role="dialog"]:visible, .byte-modal-wrapper:visible'
            ),
            timeout_seconds=0,
        )

    assert generic.clicked == 0


def test_evidence_click_accepts_scoped_confirm_then_transition():
    publish = Locator(text="发布")
    confirm = Locator(text="确认发布")
    dialog = Locator(count=0)
    dialog.children['button:visible:has-text("确认发布")'] = confirm
    page = MappingPage({
        'button:visible:has-text("发布")': publish,
        '[role="dialog"]:visible': dialog,
    })
    publish.on_click = lambda: setattr(dialog, "_count", 1)
    confirm.on_click = lambda: setattr(
        page,
        "url",
        "https://example.test/manage",
    )

    _evidence_click()(
        page,
        ["发布"],
        ["确认发布"],
        "测试平台",
        confirm_scope_selector='[role="dialog"]:visible',
        timeout_seconds=0,
    )

    assert publish.clicked == 1
    assert confirm.clicked == 1


def test_toutiao_accepts_exact_confirmation_on_editor_page():
    publish = Locator(text="预览并发布")
    confirm = Locator(text="确认发布", count=0)
    page = MappingPage({
        'button:visible:has-text("预览并发布")': publish,
        'button:visible:has-text("确认发布")': confirm,
    })
    publish.on_click = lambda: setattr(confirm, "_count", 1)
    confirm.on_click = lambda: setattr(page, "url", "https://example.test/manage")

    _evidence_click()(
        page,
        ToutiaoLocators.PUBLISH_BUTTON_TEXTS,
        ToutiaoLocators.CONFIRM_PUBLISH_TEXTS,
        "头条号",
        confirm_scope_selector=ToutiaoLocators.CONFIRM_DIALOG_SELECTOR,
        allow_unscoped_confirm=True,
        failure_markers=ToutiaoLocators.SUBMIT_FAILURE_MARKERS,
        timeout_seconds=0,
    )

    assert publish.clicked == 1
    assert confirm.clicked == 1


def test_toutiao_save_failure_stops_before_confirmation():
    publish = Locator(text="预览并发布")
    confirm = Locator(text="确认发布", count=0)
    feedback = Locator(text="保存失败", count=0)
    page = MappingPage({
        'button:visible:has-text("预览并发布")': publish,
        'button:visible:has-text("确认发布")': confirm,
        common_module.FEEDBACK_SELECTOR: feedback,
    })

    def fail_save():
        feedback._count = 1
        confirm._count = 1

    publish.on_click = fail_save

    with pytest.raises(PublishClickNoEffect, match="保存失败"):
        _evidence_click()(
            page,
            ToutiaoLocators.PUBLISH_BUTTON_TEXTS,
            ToutiaoLocators.CONFIRM_PUBLISH_TEXTS,
            "头条号",
            confirm_scope_selector=ToutiaoLocators.CONFIRM_DIALOG_SELECTOR,
            allow_unscoped_confirm=True,
            failure_markers=ToutiaoLocators.SUBMIT_FAILURE_MARKERS,
            timeout_seconds=0,
        )

    assert confirm.clicked == 0


def test_no_effect_error_contains_bounded_button_diagnostics():
    publish = Locator(
        text="发文章",
        attrs={"class": "publish disabled-look", "aria-disabled": "false"},
    )
    page = MappingPage({'button:visible:has-text("发文章")': publish})

    with pytest.raises(PublishClickNoEffect) as exc_info:
        _evidence_click()(
            page,
            ["发文章"],
            ["确认发布"],
            "一点号",
            confirm_scope_selector='[role="dialog"]:visible',
            timeout_seconds=0,
        )

    message = str(exc_info.value)
    assert "text='发文章'" in message
    assert "class='publish disabled-look'" in message
    assert "aria-disabled='false'" in message

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


def test_toutiao_publish_uses_evidence_click_path():
    publisher = object.__new__(ToutiaoPlaywrightPublisher)
    publisher.page = MappingPage({
        ".ai-assistant-drawer .byte-drawer-mask:visible": Locator(count=0),
    })
    publisher.human = object()

    with patch(
        "ordo_engine.platforms.playwright_toutiao.publisher.click_publish_with_evidence",
        create=True,
    ) as click:
        publisher.click_publish()

    click.assert_called_once()
    assert ToutiaoLocators.CONFIRM_PUBLISH_TEXTS == ["确定并发布", "确认发布"]
    assert ToutiaoLocators.CONFIRM_DIALOG_SELECTOR


def test_yidian_publish_uses_evidence_click_path():
    publisher = object.__new__(YidianPlaywrightPublisher)
    publisher.page = MappingPage({})
    publisher.human = object()

    with patch(
        "ordo_engine.platforms.playwright_yidian.publisher.click_publish_with_evidence",
        create=True,
    ) as click:
        publisher.click_publish()

    click.assert_called_once()
    assert YidianLocators.CONFIRM_PUBLISH_TEXTS == ["确认发布"]
    assert YidianLocators.CONFIRM_DIALOG_SELECTOR


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


def test_jianshu_clicks_publish_article_instead_of_collection_submit():
    collection_submit = Locator()
    page = MappingPage({JianshuLocators.PUBLISH_BUTTON_SELECTOR: collection_submit})
    publisher = object.__new__(JianshuPlaywrightPublisher)
    publisher.page = page
    publisher.human = Human()

    with patch(
        "ordo_engine.platforms.playwright_jianshu.publisher.click_publish_common"
    ) as click:
        publisher.click_publish()

    assert collection_submit.clicked == 0
    click.assert_called_once()
    assert JianshuLocators.PUBLISH_BUTTON_TEXTS[0] == "发布文章"
    assert "提交" not in JianshuLocators.PUBLISH_BUTTON_TEXTS


def test_jianshu_verifies_published_note_through_author_api():
    class NotePage:
        url = "https://www.jianshu.com/writer#/notebooks/44589321/notes/140679661"

        def evaluate(self, _script, note_id):
            assert note_id == "140679661"
            return {"shared": True, "slug": "published-slug"}

    publisher = object.__new__(JianshuPlaywrightPublisher)
    publisher.page = NotePage()
    publisher._article = type("Article", (), {"title": "目标标题"})()

    result = publisher.verify_result("publish")

    assert result.status == "published"
    assert result.current_url == "https://www.jianshu.com/p/published-slug"


def test_toutiao_selects_no_cover_when_cover_is_disabled():
    drawer_mask = Locator()
    no_cover = Locator(attrs={"class": "byte-radio"})
    radio_input = Locator(attrs={"checked": False})
    no_cover.children['input[type="radio"]'] = radio_input
    no_cover.on_click = lambda: radio_input.attrs.update({"checked": True})
    page = MappingPage({
        ".ai-assistant-drawer .byte-drawer-mask:visible": drawer_mask,
        'label.byte-radio:has-text("无封面")': no_cover,
    })
    publisher = object.__new__(ToutiaoPlaywrightPublisher)
    publisher.page = page
    publisher.human = Human()
    article = type("Article", (), {
        "title": "标题", "body": "正文", "ai_declaration_mode": "force_off",
        "cover_path": None, "cover_mode": "force_off",
    })()

    with patch(
        "ordo_engine.platforms.playwright_toutiao.publisher.should_declare_ai",
        return_value=False,
    ):
        publisher.configure_settings(article)

    assert drawer_mask.clicked == 1
    assert no_cover.clicked == 1
    assert no_cover.click_kwargs == [{"force": True}]
    assert radio_input.is_checked()


def test_toutiao_accepts_navigation_timeout_when_editor_is_already_ready():
    title = Locator()

    class TimedOutPage(MappingPage):
        def __init__(self):
            super().__init__({ToutiaoLocators.TITLE_INPUT: title})
            self.url = "https://example.test/loading"

        def goto(self, url, **_kwargs):
            self.url = url
            raise RuntimeError("domcontentloaded timeout")

    page = TimedOutPage()
    engine = type("Engine", (), {"get_page_for_platform": lambda _self, _platform: page})()
    instance = ToutiaoPlaywrightPublisher(engine)
    instance._wait_for_login_if_needed = lambda *_args, **_kwargs: None

    assert instance.navigate_to_editor() is page


def test_yidian_selects_platform_default_cover_when_custom_cover_is_disabled():
    default = Locator(attrs={"class": "item"})
    default.on_click = lambda: default.attrs.update({"class": "item checked"})
    page = MappingPage({
        '.cover-type .item:has-text("默认")': default,
    })
    publisher = object.__new__(YidianPlaywrightPublisher)
    publisher.page = page
    publisher.human = Human()
    article = type("Article", (), {
        "title": "标题", "body": "正文", "ai_declaration_mode": "force_off",
        "cover_path": None, "cover_mode": "force_off",
    })()

    with patch(
        "ordo_engine.platforms.playwright_yidian.publisher.should_declare_ai",
        return_value=False,
    ):
        publisher.configure_settings(article)

    assert default.clicked == 1
    assert default.click_kwargs == [{"force": True}]
    assert "checked" in default.attrs["class"]


def test_bilibili_reports_daily_limit_before_generic_disabled_button_error():
    disabled = Locator(enabled=False)
    frame = MappingPage({
        'button.vui_button--blue:visible:has-text("发布")': disabled,
    })
    frame.evaluate = lambda _script: "已达到当日投稿上限，只能保存草稿哦~"
    publisher = object.__new__(BilibiliPlaywrightPublisher)
    publisher._editor_frame = frame

    with pytest.raises(RuntimeError, match="达到发布上限"):
        publisher.configure_settings(object())
