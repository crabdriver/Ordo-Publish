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
    find_visible_button, _feedback_text,
)
from ordo_engine.platforms.playwright_toutiao.locators import ToutiaoLocators


class ToutiaoPlaywrightPublisher(PlaywrightBasePublisher):
    """头条号文章 Playwright 人像化发布器"""

    platform = "toutiao"

    def navigate_to_editor(self) -> Page:
        page = self.engine.get_page_for_platform("toutiao")
        if "publish" not in (page.url or ""):
            page.goto(ToutiaoLocators.EDITOR_URL, wait_until="domcontentloaded", timeout=30000)
        self._wait_for_login_if_needed(page, "publish", ToutiaoLocators.TITLE_INPUT, "头条号", ToutiaoLocators.EDITOR_URL)
        print(f"[INFO] 头条号编辑器已就绪: {page.url}")
        return page

    def fill_title(self, title: str):
        fill_title_common(self.human, self.page, title, ToutiaoLocators.TITLE_INPUT, "头条号")

    def fill_body(self, body: str):
        fill_body_common(
            self.human, self.page, body,
            ToutiaoLocators.EDITOR_AREA, "头条号",
            ToutiaoLocators.EDITOR_AREA_MIN_WIDTH, ToutiaoLocators.EDITOR_AREA_MIN_HEIGHT,
        )

    def upload_cover(self, cover_path: Path):
        path = Path(cover_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"封面文件不存在: {path}")

        radio = self.page.locator('label:has-text("单图") .byte-radio-inner').first
        if radio.count() == 0:
            raise RuntimeError("未找到头条号单图封面选项")
        if "checked" not in (radio.get_attribute("class") or "").split():
            self.human.human_click(radio)
            time.sleep(1)
            if "checked" not in (radio.get_attribute("class") or "").split():
                raise RuntimeError("头条号封面未切换到单图")

        add = self.page.locator(".article-cover-add, .article-cover-img-replace").first
        if add.count() == 0:
            raise RuntimeError("未找到头条号添加封面入口")
        self.human.human_click(add)

        file_input = self.page.locator(
            '.btn-upload-handle input[type="file"], input[type="file"][accept*="image"]'
        ).first
        file_input.wait_for(state="attached", timeout=10000)
        file_input.set_input_files(str(path))

        uploaded = self.page.locator(".pic-select-image-item:has(.success)").first
        uploaded.wait_for(state="visible", timeout=15000)
        self.human.human_click(uploaded)
        confirm = self.page.locator(
            '.byte-drawer-wrapper button:visible:has-text("确定")'
        ).first
        confirm.wait_for(state="visible", timeout=10000)
        self.human.human_click(confirm)
        print(f"[INFO] 头条号封面已上传: {path.name}")
        self.human.human_wait(0.5, 1.0)

    def configure_settings(self, article: ArticlePayload):
        need_ai = should_declare_ai(article.title, article.body, article.ai_declaration_mode or "auto")
        if need_ai:
            self._set_ai_declaration()
        feedback = _feedback_text(self.page)
        if "保存失败" in feedback:
            raise RuntimeError(f"头条号编辑器保存失败，阻止提交: {feedback.strip()}")

    def _set_ai_declaration(self):
        print("[INFO] 开始设置头条号 AI 声明...")
        try:
            cells = self.page.locator(ToutiaoLocators.AI_CHECKBOX_CONTAINER)
            for i in range(cells.count()):
                cell = cells.nth(i)
                text = cell.inner_text() or ""
                if ToutiaoLocators.AI_CHECKBOX_LABEL in text:
                    checkbox = cell.locator("input[type=checkbox], .byte-radio-inner, .byte-checkbox")
                    if checkbox.count() > 0:
                        self.human.human_click(checkbox.first)
                        print("[INFO] 头条号 AI 声明已设置")
                        return
            print("[WARN] 未找到头条号 AI 声明选项")
        except Exception as exc:
            print(f"[WARN] 设置头条号 AI 声明失败: {exc}")

    def click_publish(self):
        click_publish_common(
            self.human, self.page,
            ToutiaoLocators.PUBLISH_BUTTON_TEXTS,
            ToutiaoLocators.CONFIRM_PUBLISH_TEXTS,
            "头条号",
        )

    def save_draft(self):
        save_draft_common(self.human, self.page, ToutiaoLocators.SAVE_DRAFT_TEXTS, "头条号")

    def verify_result(self, mode: str) -> PublishResult:
        return verify_result_common(
            self.page, "头条号", mode,
            ToutiaoLocators.PUBLISHED_URL_PATTERN,
            ToutiaoLocators.PUBLISH_SUCCESS_MARKERS,
            ToutiaoLocators.DRAFT_SUCCESS_MARKERS,
            ToutiaoLocators.LIMIT_MARKERS,
            ToutiaoLocators.MANAGEMENT_URL,
            ToutiaoLocators.DRAFT_MANAGEMENT_URL,
            expected_title=self._article.title,
        )
