from __future__ import annotations

import time
from pathlib import Path

try:
    from patchright.sync_api import Page
except ImportError:
    from playwright.sync_api import Page

from markdown_utils import render_markdown_plain_text, should_declare_ai
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload,
    DraftCheckpoint,
    PlaywrightBasePublisher,
    PublishResult,
)
from ordo_engine.platforms.playwright.engine import PlaywrightEngine
from ordo_engine.platforms.playwright.human import HumanBehavior
from ordo_engine.platforms.playwright._common import (
    fill_title_common, fill_body_common, upload_cover_common,
    click_publish_common, verify_result_common, find_visible_button,
)
from ordo_engine.platforms.playwright_zhihu.locators import ZhihuLocators


class ZhihuPlaywrightPublisher(PlaywrightBasePublisher):
    """知乎文章 Playwright 人像化发布器

    发布流程：
    1. 导航到知乎写文章页面
    2. 等待编辑器就绪（标题输入框 + 富文本编辑区域）
    3. 人像化打字输入标题
    4. 剪贴板粘贴正文（纯文本）
    5. 上传封面（set_input_files）
    6. 设置 AI 声明（如需要）
    7. 点击发布/保存草稿
    8. 验证结果
    """

    platform = "zhihu"

    def navigate_to_editor(self) -> Page:
        """导航到知乎写文章页面"""
        page = self.engine.get_page_for_platform("zhihu")

        # Navigate to editor if not already there
        if "write" not in (page.url or ""):
            page.goto(
                ZhihuLocators.EDITOR_URL,
                wait_until="domcontentloaded",
                timeout=30000,
            )

        # 检测是否需要登录，如果需要则等待用户扫码
        self._wait_for_login_if_needed(page, "write", ZhihuLocators.TITLE_INPUT, "知乎", ZhihuLocators.EDITOR_URL)

        # Wait for editor area with sufficient size
        editor_selector = ZhihuLocators.EDITOR_AREA.replace("'", "\\'")
        page.wait_for_function(
            f"""
            () => {{
                const editors = document.querySelectorAll('{editor_selector}');
                return Array.from(editors).some(el => {{
                    const rect = el.getBoundingClientRect();
                    return rect.width >= {ZhihuLocators.EDITOR_AREA_MIN_WIDTH}
                        && rect.height >= {ZhihuLocators.EDITOR_AREA_MIN_HEIGHT};
                }});
            }}
            """,
            timeout=30000,
        )

        print(f"[INFO] 知乎编辑器已就绪: {page.url}")
        return page

    def fill_title(self, title: str):
        fill_title_common(self.human, self.page, title, ZhihuLocators.TITLE_INPUT, "知乎")

    def fill_body(self, body: str):
        fill_body_common(
            self.human, self.page, body,
            ZhihuLocators.EDITOR_AREA, "知乎",
            ZhihuLocators.EDITOR_AREA_MIN_WIDTH, ZhihuLocators.EDITOR_AREA_MIN_HEIGHT,
        )

    def upload_cover(self, cover_path: Path):
        upload_cover_common(
            self.page, cover_path, ZhihuLocators.COVER_FILE_INPUT, "知乎",
            success_selector=ZhihuLocators.COVER_UPLOAD_SUCCESS,
        )
        self.human.human_wait(0.5, 1.0)

    def configure_settings(self, article: ArticlePayload):
        """配置 AI 声明等设置"""
        need_ai = should_declare_ai(
            article.title,
            article.body,
            article.ai_declaration_mode or "auto",
        )
        if need_ai:
            self._set_ai_declaration()

    def _set_ai_declaration(self):
        """设置 AI 创作声明"""
        print("[INFO] 开始设置知乎 AI 创作声明...")
        try:
            label = self.page.locator(f'text="{ZhihuLocators.AI_DECLARATION_LABEL}"')
            if label.count() == 0:
                print("[WARN] 未找到知乎创作声明区域")
                return

            combobox = self.page.locator(
                f'[role="{ZhihuLocators.AI_COMBOBOX_ROLE}"]'
            ).first
            if combobox.count() == 0:
                print("[WARN] 未找到知乎创作声明下拉框")
                return

            self.human.human_click(combobox)
            time.sleep(0.5)

            option = self.page.locator(
                f'[role="{ZhihuLocators.AI_OPTION_ROLE}"]:has-text("{ZhihuLocators.AI_DECLARATION_OPTION_TEXT}")'
            )
            option.wait_for(state="visible", timeout=5000)
            self.human.human_click(option)
            print("[INFO] 知乎 AI 创作声明已设置")
        except Exception as exc:
            print(f"[WARN] 设置知乎 AI 创作声明失败: {exc}")

    def click_publish(self):
        click_publish_common(
            self.human, self.page,
            ZhihuLocators.PUBLISH_BUTTON_TEXTS,
            ZhihuLocators.CONFIRM_PUBLISH_TEXTS,
            "知乎",
        )

    def save_draft(self):
        """保存草稿"""
        draft_btn = find_visible_button(self.page, ZhihuLocators.SAVE_DRAFT_TEXTS)
        if draft_btn:
            self.human.human_click(draft_btn)
            time.sleep(2)
        else:
            # Trigger auto-save
            self.page.keyboard.press("Tab")
            time.sleep(3)
        print("[INFO] 知乎草稿已保存")

    def verify_result(self, mode: str) -> PublishResult:
        return verify_result_common(
            self.page, "知乎", mode,
            ZhihuLocators.PUBLISHED_URL_PATTERN,
            ZhihuLocators.PUBLISH_SUCCESS_MARKERS,
            ZhihuLocators.DRAFT_SUCCESS_MARKERS,
            ZhihuLocators.LIMIT_MARKERS,
            ZhihuLocators.MANAGEMENT_URL,
            ZhihuLocators.DRAFT_MANAGEMENT_URL,
            expected_title=self._article.title,
        )

    # ── 草稿检查点协议 ──────────────────────────────────────

    def verify_draft_checkpoint(self) -> DraftCheckpoint:
        """核验知乎草稿：导航到创作中心草稿列表，匹配标题。

        无法核验 → draft_ref 为空，调用方应处理为 manual_verify。
        """
        from datetime import datetime, timezone

        try:
            # 导航到草稿管理页
            self.page.goto(
                ZhihuLocators.DRAFT_MANAGEMENT_URL,
                wait_until="domcontentloaded",
                timeout=15000,
            )
            self.human.human_wait(1, 2)

            # 尝试匹配标题
            title = getattr(self._article, "title", "")
            draft_ref = ""
            if title:
                try:
                    link = self.page.locator(f'a[href*="/p/"]:has-text("{title}")').first
                    if link.count() > 0:
                        href = link.get_attribute("href") or ""
                        if "/p/" in href:
                            draft_ref = href.split("/p/")[-1].split("?")[0]
                except Exception:
                    pass

            return DraftCheckpoint(
                platform=self.platform,
                draft_ref=draft_ref,
                draft_url=ZhihuLocators.DRAFT_MANAGEMENT_URL,
                saved_at=datetime.now(timezone.utc).isoformat(),
                verification_evidence={
                    "method": "draft_list_title_match",
                    "title_matched": bool(draft_ref),
                    "draft_list_url": ZhihuLocators.DRAFT_MANAGEMENT_URL,
                },
            )
        except Exception as exc:
            return DraftCheckpoint(
                platform=self.platform,
                draft_ref="",
                verification_evidence={
                    "method": "draft_list_error",
                    "error": str(exc),
                },
            )

    def publish_from_draft(self, draft_ref: str) -> PublishResult:
        """从已有草稿发布：打开草稿编辑页 → 点击发布。"""
        draft_url = f"https://zhuanlan.zhihu.com/p/{draft_ref}/edit"
        self.page.goto(draft_url, wait_until="domcontentloaded", timeout=15000)
        self.human.human_wait(2, 3)
        self._submission_started = True
        self.click_publish()
        return self.verify_result("publish")

    def verify_published(self, published_ref: str) -> bool:
        """核验知乎是否已正式发布。"""
        try:
            article_url = f"https://zhuanlan.zhihu.com/p/{published_ref}"
            self.page.goto(article_url, wait_until="domcontentloaded", timeout=10000)
            # 如果有标题内容出现即视为已发布
            return self.page.title() != "" and "404" not in self.page.title()
        except Exception:
            return False
