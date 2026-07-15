from __future__ import annotations

import time
from pathlib import Path

try:
    from patchright.sync_api import Page
except ImportError:
    from playwright.sync_api import Page

from markdown_utils import should_declare_ai
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload, DraftCheckpoint, PlaywrightBasePublisher, PublishResult,
)
from ordo_engine.platforms.playwright._common import (
    fill_title_common, fill_body_common,
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

        picker = self.page.locator(YidianLocators.COVER_PICKER)
        if picker.count() == 0:
            raise RuntimeError("未找到一点号单图封面上传入口")
        try:
            with self.page.expect_file_chooser(timeout=10000) as chooser_info:
                self.human.human_click(picker.first)
            chooser_info.value.set_files(str(Path(cover_path).expanduser().resolve()))
        except Exception as exc:
            raise RuntimeError(
                "一点号账号未提供本地封面上传入口：该账号只能从正文已有图片选封面，"
                "请开通站外图片上传权限或在正文加入可用图片"
            ) from exc
        self.human.human_wait(0.5, 1.0)

    def configure_settings(self, article: ArticlePayload):
        if not getattr(article, "cover_path", None) or getattr(article, "cover_mode", None) == "force_off":
            self._select_default_cover()
        need_ai = should_declare_ai(article.title, article.body, article.ai_declaration_mode or "auto")
        if need_ai:
            self._set_ai_declaration()

        # 设置个人观点声明
        self._set_personal_opinion()

    def _select_default_cover(self):
        default = self.page.locator(YidianLocators.COVER_DEFAULT_SELECTOR).first
        if default.count() == 0:
            raise RuntimeError("未找到一点号默认封面选项")
        if "checked" not in (default.get_attribute("class") or "").split():
            # 该 Vue 单选框的可点击区域与视觉框不完全重合，坐标点击会静默失败。
            default.click(force=True)
            time.sleep(0.5)
        if "checked" not in (default.get_attribute("class") or "").split():
            raise RuntimeError("一点号默认封面未选中")
        print("[INFO] 一点号已选择平台默认封面")

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

    # ── 草稿检查点协议 ──────────────────────────────────────

    def verify_draft_checkpoint(self) -> DraftCheckpoint:
        """核验一点号草稿。一点号封面限制较多，draft_ref 可能为空 → 调用方应处理为 blocked_no_draft 或 manual_verify。"""
        from datetime import datetime, timezone
        try:
            self.page.goto(YidianLocators.DRAFT_MANAGEMENT_URL or YidianLocators.MANAGEMENT_URL,
                           wait_until="domcontentloaded", timeout=15000)
            self.human.human_wait(1, 2)
            title = getattr(self._article, "title", "")
            draft_ref = ""
            if title:
                try:
                    el = self.page.locator(f'text="{title}"').first
                    if el.count() > 0:
                        draft_ref = self.page.url or ""
                except Exception:
                    pass
            return DraftCheckpoint(
                platform=self.platform, draft_ref=draft_ref,
                saved_at=datetime.now(timezone.utc).isoformat(),
                verification_evidence={"method": "draft_list_title_match",
                                       "title_matched": bool(draft_ref)})
        except Exception as exc:
            return DraftCheckpoint(
                platform=self.platform, draft_ref="",
                verification_evidence={"method": "draft_list_error", "error": str(exc)})

    def publish_from_draft(self, draft_ref: str) -> PublishResult:
        if draft_ref:
            self.page.goto(draft_ref, wait_until="domcontentloaded", timeout=15000)
        self._submission_started = True
        self.click_publish()
        return self.verify_result("publish")

    def verify_published(self, published_ref: str) -> bool:
        try:
            self.page.goto(published_ref, wait_until="domcontentloaded", timeout=10000)
            return self.page.title() != "" and "404" not in self.page.title()
        except Exception:
            return False
