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
)
from ordo_engine.platforms.playwright_jianshu.locators import JianshuLocators


class JianshuPlaywrightPublisher(PlaywrightBasePublisher):
    """简书文章 Playwright 人像化发布器"""

    platform = "jianshu"

    def navigate_to_editor(self) -> Page:
        page = self.engine.get_page_for_platform("jianshu")
        if "writer" not in (page.url or ""):
            page.goto(JianshuLocators.EDITOR_URL, wait_until="domcontentloaded", timeout=30000)
        # 注意：不要在此处提前 wait_for_selector(TITLE_INPUT)，
        # 否则未登录时标题框永不出现会先超时。登录/就绪等待统一交给基类处理。
        self._wait_for_login_if_needed(page, "writer", JianshuLocators.TITLE_INPUT, "简书", JianshuLocators.EDITOR_URL)

        # 登录后，writer#/ 会落到「最近一篇笔记」的编辑器。
        # 发布新文章需点『新建文章』开一篇空白笔记，避免覆盖已有草稿。
        self._open_new_article(page)
        print(f"[INFO] 简书新文章编辑器已就绪: {page.url}")
        return page

    def _open_new_article(self, page: Page):
        """点击『新建文章』打开空白新笔记，并等待标题框出现"""
        btn_texts = ["新建文章", "写文章", "新建笔记"]
        clicked = False
        for txt in btn_texts:
            try:
                btn = page.get_by_text(txt, exact=False).first
                if btn.count() and btn.first.is_visible(timeout=5000):
                    btn.first.click()
                    clicked = True
                    print(f"[INFO] 已点击『{txt}』创建新笔记")
                    break
            except Exception:
                continue
        if not clicked:
            print("[WARN] 未找到『新建文章』按钮，将沿用当前编辑器")
        # 等待新笔记标题框就绪（新笔记默认标题为当天日期）
        try:
            page.wait_for_selector(JianshuLocators.TITLE_INPUT, state="visible", timeout=30000)
        except Exception as exc:
            raise RuntimeError(f"简书新建文章后标题框未出现: {exc}")

    def fill_title(self, title: str):
        fill_title_common(self.human, self.page, title, JianshuLocators.TITLE_INPUT, "简书")

    def fill_body(self, body: str):
        fill_body_common(
            self.human, self.page, body,
            JianshuLocators.EDITOR_AREA, "简书",
            JianshuLocators.EDITOR_AREA_MIN_WIDTH, JianshuLocators.EDITOR_AREA_MIN_HEIGHT,
        )

    def upload_cover(self, cover_path: Path):
        upload_cover_common(self.page, cover_path, JianshuLocators.COVER_FILE_INPUT, "简书")
        self.human.human_wait(0.5, 1.0)

    def configure_settings(self, article: ArticlePayload):
        # 简书没有专门的 AI 声明选项，暂不处理
        pass

    def click_publish(self):
        click_publish_common(
            self.human, self.page,
            JianshuLocators.PUBLISH_BUTTON_TEXTS,
            JianshuLocators.CONFIRM_PUBLISH_TEXTS,
            "简书",
        )

    def save_draft(self):
        # 简书的自动保存机制：切换焦点即可触发
        self.page.keyboard.press("Tab")
        time.sleep(3)
        print("[INFO] 简书草稿已保存（自动保存）")

    def verify_result(self, mode: str) -> PublishResult:
        return verify_result_common(
            self.page, "简书", mode,
            JianshuLocators.PUBLISHED_URL_PATTERN,
            JianshuLocators.PUBLISH_SUCCESS_MARKERS,
            JianshuLocators.DRAFT_SUCCESS_MARKERS,
            JianshuLocators.LIMIT_MARKERS,
            JianshuLocators.MANAGEMENT_URL,
            JianshuLocators.DRAFT_MANAGEMENT_URL,
        )
