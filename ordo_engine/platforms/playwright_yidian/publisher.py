from __future__ import annotations

import time
from pathlib import Path

try:
    from patchright.sync_api import Page
except ImportError:
    from playwright.sync_api import Page

from markdown_utils import should_declare_ai
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload, PlaywrightBasePublisher, PublishResult,
)
from ordo_engine.platforms.playwright._common import (
    fill_title_common, fill_body_common, upload_cover_common,
    click_publish_common, save_draft_common, verify_result_common,
    find_visible_button,
)
from ordo_engine.platforms.playwright_yidian.locators import YidianLocators


class YidianPlaywrightPublisher(PlaywrightBasePublisher):
    """一点号文章 Playwright 人像化发布器"""

    platform = "yidian"

    def navigate_to_editor(self) -> Page:
        page = self.engine.get_page_for_platform("yidian")
        if "articleEditor" not in (page.url or ""):
            page.goto(YidianLocators.EDITOR_URL, wait_until="domcontentloaded", timeout=30000)
        self._wait_for_login_if_needed(page, "articleEditor", YidianLocators.TITLE_INPUT, "一点号", YidianLocators.EDITOR_URL)
        print(f"[INFO] 一点号编辑器已就绪: {page.url}")
        return page

    def fill_title(self, title: str):
        fill_title_common(self.human, self.page, title, YidianLocators.TITLE_INPUT, "一点号")

    def fill_body(self, body: str):
        fill_body_common(
            self.human, self.page, body,
            YidianLocators.EDITOR_AREA, "一点号",
            YidianLocators.EDITOR_AREA_MIN_WIDTH, YidianLocators.EDITOR_AREA_MIN_HEIGHT,
        )

    def upload_cover(self, cover_path: Path):
        # 一点号需要先点击"单图"选择封面模式
        try:
            single_cover_btn = self.page.locator(f'text="{YidianLocators.COVER_SINGLE_TEXT}"')
            if single_cover_btn.count() > 0:
                self.human.human_click(single_cover_btn.first)
                time.sleep(1)
        except Exception:
            pass

        upload_cover_common(self.page, cover_path, YidianLocators.COVER_FILE_INPUT, "一点号")
        self.human.human_wait(0.5, 1.0)

    def configure_settings(self, article: ArticlePayload):
        need_ai = should_declare_ai(article.title, article.body, article.ai_declaration_mode or "auto")
        if need_ai:
            self._set_ai_declaration()

        # 设置个人观点声明
        self._set_personal_opinion()

    def _set_ai_declaration(self):
        print("[INFO] 开始设置一点号 AI 声明...")
        try:
            ai_label = self.page.locator(f'text="{YidianLocators.AI_DECLARATION_TEXT}"')
            if ai_label.count() > 0:
                self.human.human_click(ai_label.first)
                print("[INFO] 一点号 AI 声明已设置")
            else:
                print("[WARN] 未找到一点号 AI 声明选项")
        except Exception as exc:
            print(f"[WARN] 设置一点号 AI 声明失败: {exc}")

    def _set_personal_opinion(self):
        try:
            opinion_label = self.page.locator(f'text="{YidianLocators.PERSONAL_OPINION_TEXT}"')
            if opinion_label.count() > 0:
                self.human.human_click(opinion_label.first)
                time.sleep(0.5)
        except Exception:
            pass

    def click_publish(self):
        click_publish_common(
            self.human, self.page,
            YidianLocators.PUBLISH_BUTTON_TEXTS,
            YidianLocators.CONFIRM_PUBLISH_TEXTS,
            "一点号",
        )

    def save_draft(self):
        save_draft_common(self.human, self.page, YidianLocators.SAVE_DRAFT_TEXTS, "一点号")

    def verify_result(self, mode: str) -> PublishResult:
        return verify_result_common(
            self.page, "一点号", mode,
            YidianLocators.PUBLISHED_URL_PATTERN,
            YidianLocators.PUBLISH_SUCCESS_MARKERS,
            YidianLocators.DRAFT_SUCCESS_MARKERS,
            YidianLocators.LIMIT_MARKERS,
            YidianLocators.MANAGEMENT_URL,
            YidianLocators.DRAFT_MANAGEMENT_URL,
            expected_title=self._article.title,
        )
