from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ordo_engine.platforms.playwright._common import verify_result_common
from ordo_engine.platforms.playwright.adapters import PlaywrightPlatformAdapter
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload,
    PlaywrightBasePublisher,
    PublishResult,
)
from ordo_engine.platforms.playwright_zhihu.publisher import ZhihuPlaywrightPublisher
from ordo_engine.platforms.playwright_zhihu.locators import ZhihuLocators
from ordo_engine.platforms.playwright_bilibili.locators import BilibiliLocators
from ordo_engine.platforms.playwright_jianshu.locators import JianshuLocators
from ordo_engine.platforms.playwright_toutiao.locators import ToutiaoLocators
from ordo_engine.platforms.playwright_yidian.locators import YidianLocators
from ordo_engine.results.errors import ErrorType, is_retryable_error
from ordo_engine.run_state import article_key, get_record, is_done, record_step, state_file_for


class FakePage:
    def __init__(self, *, url="https://example.test/editor", text="", management_text="", feedback=()):
        self.url = url
        self.text = text
        self.management_text = management_text
        self.feedback = list(feedback)

    def evaluate(self, _script):
        return self.text

    def goto(self, url, **_kwargs):
        self.url = url
        self.text = self.management_text

    def locator(self, _selector):
        return FakeLocator(self.feedback)


class FakeLocator:
    def __init__(self, texts):
        self.texts = list(texts)
        self.index = None

    def count(self):
        return len(self.texts)

    def all_inner_texts(self):
        return list(self.texts)

    def nth(self, index):
        locator = FakeLocator(self.texts)
        locator.index = index
        return locator

    def is_visible(self):
        return True

    def inner_text(self):
        return self.texts[self.index]


def verify_common(page, *, mode="publish", title="目标标题"):
    return verify_result_common(
        page,
        "测试平台",
        mode,
        r"/article/\d+$",
        ["发布成功"],
        ["草稿已保存"],
        ["发布上限"],
        "https://example.test/manage",
        "https://example.test/drafts",
        expected_title=title,
    )


def test_management_navigation_without_exact_title_is_unverified():
    page = FakePage(management_text="目标标题附注\n其他文章")

    with patch("ordo_engine.platforms.playwright._common.time.sleep"):
        result = verify_common(page)

    assert result.status == "submitted_unverified"
    assert result.page_state == "submitted_unverified"


def test_management_exact_normalized_title_is_published():
    page = FakePage(management_text="其他文章\n  目标  标题  \n第三篇")

    with patch("ordo_engine.platforms.playwright._common.time.sleep"):
        result = verify_common(page, title="目标\u3000标题")

    assert result.status == "published"
    assert result.page_state == "published"


def test_management_navigation_error_is_unverified():
    page = FakePage()
    page.goto = MagicMock(side_effect=RuntimeError("network down"))

    with patch("ordo_engine.platforms.playwright._common.time.sleep"):
        result = verify_common(page)

    assert result.status == "submitted_unverified"


def test_direct_published_url_is_terminal_success():
    result = verify_common(FakePage(url="https://example.test/article/42"))

    assert result.status == "published"


@pytest.mark.parametrize(
    ("url", "pattern"),
    [
        ("https://mp.yidianzixun.com/#/ArticleManual/original/review", YidianLocators.PUBLISHED_URL_PATTERN),
        ("https://member.bilibili.com/platform/upload/text/new-edit", BilibiliLocators.PUBLISHED_URL_PATTERN),
        ("https://www.jianshu.com/writer#/notebooks/1/notes/2", JianshuLocators.PUBLISHED_URL_PATTERN),
        ("https://mp.toutiao.com/profile_v4/graphic/manuscript", ToutiaoLocators.PUBLISHED_URL_PATTERN),
        (
            "https://mp.yidianzixun.com/login?next=https://www.yidianzixun.com/article/abc",
            YidianLocators.PUBLISHED_URL_PATTERN,
        ),
    ],
)
def test_editor_or_management_url_is_not_direct_publish_evidence(url, pattern):
    result = verify_result_common(
        FakePage(url=url), "测试平台", "publish", pattern,
        ["发布成功"], ["草稿已保存"], ["发布上限"],
    )

    assert result.status == "submitted_unverified"


def test_navigation_label_containing_success_words_is_not_success_marker():
    page = FakePage(text="首页\n已发布\n草稿箱")

    result = verify_result_common(
        page, "测试平台", "publish", r"/article/\d+$",
        ["已发布"], ["草稿"], ["发布上限"],
    )

    assert result.status == "submitted_unverified"


def test_direct_url_wins_over_limit_words_in_body_and_feedback():
    page = FakePage(
        url="https://example.test/article/42",
        text="正文引用：发布上限",
        feedback=["达到发布上限"],
    )

    result = verify_common(page)

    assert result.status == "published"


def test_explicit_toast_success_is_terminal_success():
    page = FakePage(text="普通页面", feedback=["发布成功"])

    result = verify_common(page)

    assert result.status == "published"


@pytest.mark.parametrize(
    ("mode", "feedback", "success_markers", "draft_markers"),
    [
        ("publish", "未发布成功，请重试", ["发布成功"], ["草稿"]),
        ("draft", "保存草稿失败", ["发布成功"], ["保存草稿"]),
    ],
)
def test_negative_feedback_does_not_match_positive_marker(mode, feedback, success_markers, draft_markers):
    result = verify_result_common(
        FakePage(feedback=[feedback]),
        "测试平台",
        mode,
        r"/article/\d+$",
        success_markers,
        draft_markers,
        ["发布上限"],
    )

    assert result.status == "submitted_unverified"


def test_explicit_alert_limit_is_retryable_nonzero(tmp_path):
    page = FakePage(text="普通页面", feedback=["发布上限"])

    result = verify_common(page)

    assert result.status == "limit_reached"

    article = tmp_path / "article.md"
    article.write_text("# 标题\n正文", encoding="utf-8")

    class ResultPublisher:
        def __init__(self, _engine):
            pass

        def publish(self, _article, _mode):
            return result

    adapter = PlaywrightPlatformAdapter(tmp_path, "stub", ResultPublisher)
    adapter.set_shared_engine(MagicMock())
    process_result = adapter.publish(adapter.prepare(article, "publish"))
    collected = adapter.collect_result(process_result, "publish")

    assert process_result["returncode"] != 0
    assert collected.status == "limit_reached"
    assert collected.retryable


@pytest.mark.parametrize("limit_marker", ["达到发布上限", "发布上限", "请明天再来"])
def test_explicit_limit_feedback_phrase_is_retryable_nonzero(tmp_path, limit_marker):
    result = verify_result_common(
        FakePage(feedback=["今日达到发布上限，请明天再来"]),
        "测试平台",
        "publish",
        r"/article/\d+$",
        ["发布成功"],
        ["草稿已保存"],
        [limit_marker],
    )

    assert result.status == "limit_reached"

    article = tmp_path / "article.md"
    article.write_text("# 标题\n正文", encoding="utf-8")

    class ResultPublisher:
        def __init__(self, _engine):
            pass

        def publish(self, _article, _mode):
            return result

    adapter = PlaywrightPlatformAdapter(tmp_path, "stub", ResultPublisher)
    adapter.set_shared_engine(MagicMock())
    process_result = adapter.publish(adapter.prepare(article, "publish"))
    collected = adapter.collect_result(process_result, "publish")

    assert process_result["returncode"] != 0
    assert collected.status == "limit_reached"
    assert collected.retryable


def test_zhihu_management_navigation_without_title_is_unverified():
    page = FakePage(management_text="没有目标文章")
    publisher = ZhihuPlaywrightPublisher(MagicMock())
    publisher.page = page
    publisher._article = ArticlePayload(
        title="目标标题",
        body="正文",
        markdown_path=Path("article.md"),
    )

    with patch("ordo_engine.platforms.playwright._common.time.sleep"):
        result = publisher.verify_result("publish")

    assert result.status == "submitted_unverified"
    assert page.url == ZhihuLocators.MANAGEMENT_URL


@pytest.mark.parametrize(
    ("status", "expected_returncode"),
    [
        ("published", 0),
        ("scheduled", 0),
        ("draft_only", 0),
        ("draft_saved", 0),
        ("skipped_existing", 0),
        ("limit_reached", 1),
        ("submitted_unverified", 1),
        ("unknown", 1),
        ("failed", 1),
    ],
)
def test_adapter_returncode_only_accepts_terminal_outcomes(tmp_path, status, expected_returncode):
    article = tmp_path / "article.md"
    article.write_text("# 标题\n正文", encoding="utf-8")

    class ResultPublisher:
        def __init__(self, _engine):
            pass

        def publish(self, _article, _mode):
            return PublishResult(platform="stub", status=status, page_state=status)

    adapter = PlaywrightPlatformAdapter(tmp_path, "stub", ResultPublisher)
    adapter.set_shared_engine(MagicMock())
    prepared = adapter.prepare(article, "publish")

    result = adapter.publish(prepared)

    assert result["returncode"] == expected_returncode
    assert adapter.verify(result, "publish") == status


def test_rate_limit_is_retryable_next_run():
    assert is_retryable_error(ErrorType.RATE_LIMITED)


class StatefulPublisher(PlaywrightBasePublisher):
    platform = "stub"

    def __init__(self, engine, *, verification="published", submit_error=None, events=None):
        super().__init__(engine)
        self.verification = verification
        self.submit_error = submit_error
        self.events = events if events is not None else []
        self.navigate_calls = 0
        self.fill_calls = 0
        self.submit_calls = 0

    def _init_human(self, _page):
        human = MagicMock()
        human.human_wait.return_value = None
        return human

    def navigate_to_editor(self):
        self.navigate_calls += 1
        return FakePage()

    def fill_title(self, _title):
        self.fill_calls += 1

    def fill_body(self, _body):
        self.fill_calls += 1

    def upload_cover(self, _cover_path):
        pass

    def configure_settings(self, _article):
        pass

    def click_publish(self):
        self.submit_calls += 1
        self.events.append("click")
        if self.submit_error:
            raise self.submit_error

    def save_draft(self):
        self.submit_calls += 1
        self.events.append("save")

    def verify_result(self, _mode):
        self.events.append("verify")
        return PublishResult(
            platform=self.platform,
            status=self.verification,
            page_state=self.verification,
        )


def payload(path):
    return ArticlePayload(title="目标标题", body="正文", markdown_path=path)


def test_submit_started_is_durable_before_click(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("# 标题", encoding="utf-8")
    events = []
    publisher = StatefulPublisher(MagicMock(base_dir=tmp_path), events=events)
    publisher.engine.screenshot.return_value = None

    def note_step(_identity, _platform, _mode, step, **_kwargs):
        if step in {"submit_started", "submitted"}:
            events.append(step)

    with patch("ordo_engine.platforms.playwright.base_publisher.record_step", side_effect=note_step), patch(
        "ordo_engine.platforms.playwright.base_publisher.mark_done"
    ):
        result = publisher.publish(payload(article), "publish")

    assert result.status == "published"
    assert events.index("submit_started") < events.index("click") < events.index("submitted")


def test_submit_state_write_failure_blocks_click(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("# 标题", encoding="utf-8")
    publisher = StatefulPublisher(MagicMock(base_dir=tmp_path))
    publisher.engine.screenshot.return_value = None

    def fail_submit_started(_identity, _platform, _mode, step, **_kwargs):
        if step == "submit_started":
            raise OSError("disk full")

    with patch("ordo_engine.platforms.playwright.base_publisher.record_step", side_effect=fail_submit_started):
        result = publisher.publish(payload(article), "publish")

    assert result.status == "failed"
    assert result.error == "disk full"
    assert publisher.submit_calls == 0


@pytest.mark.parametrize("verification", ["failed", "unknown"])
def test_post_click_verification_failure_is_unverified(tmp_path, verification):
    article = tmp_path / "article.md"
    article.write_text("# 标题", encoding="utf-8")
    engine = MagicMock(base_dir=tmp_path)
    engine.screenshot.return_value = None
    publisher = StatefulPublisher(engine, verification=verification)

    result = publisher.publish(payload(article), "publish")

    assert result.status == "submitted_unverified"
    assert get_record(
        article_key(article),
        "stub",
        "publish",
        state_file=state_file_for(tmp_path),
    )["last_step"] == "submitted_unverified"


def test_crash_after_click_records_unverified_and_rerun_never_submits(tmp_path):
    article = tmp_path / "article.md"
    article.write_text("# 标题", encoding="utf-8")
    engine = MagicMock(base_dir=tmp_path)
    engine.screenshot.return_value = None
    first = StatefulPublisher(engine, submit_error=RuntimeError("browser crashed"))

    first_result = first.publish(payload(article), "publish")
    record = get_record(
        article_key(article),
        "stub",
        "publish",
        state_file=state_file_for(tmp_path),
    )

    assert first_result.status == "submitted_unverified"
    assert record["last_step"] == "submitted_unverified"

    second = StatefulPublisher(engine, verification="submitted_unverified")
    second_result = second.publish(payload(article), "publish")

    assert second_result.status == "submitted_unverified"
    assert second.submit_calls == 0
    assert second.fill_calls == 0
    assert second.navigate_calls == 0
    assert second.events == ["verify"]


@pytest.mark.parametrize("hazard", ["submit_started", "submitted", "submitted_unverified"])
def test_rerun_reconciles_hazard_and_only_explicit_evidence_marks_done(tmp_path, hazard):
    article = tmp_path / "article.md"
    article.write_text("# 标题", encoding="utf-8")
    identity = article_key(article)
    state_file = state_file_for(tmp_path)
    record_step(identity, "stub", "publish", hazard, state_file=state_file)
    engine = MagicMock(base_dir=tmp_path)
    engine.screenshot.return_value = None
    publisher = StatefulPublisher(engine, verification="published")

    result = publisher.publish(payload(article), "publish")

    assert result.status == "published"
    assert publisher.submit_calls == 0
    assert publisher.fill_calls == 0
    assert publisher.navigate_calls == 0
    assert publisher.events == ["verify"]
    assert is_done(identity, "stub", "publish", state_file=state_file)
