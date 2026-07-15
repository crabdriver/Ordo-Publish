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
    fill_title_common, fill_body_common, upload_cover_common,
    click_publish_with_evidence, save_draft_common, verify_result_common,
    _feedback_text,
)
from ordo_engine.platforms.playwright_toutiao.locators import ToutiaoLocators


class ToutiaoPlaywrightPublisher(PlaywrightBasePublisher):
    """头条号文章 Playwright 人像化发布器"""

    platform = "toutiao"

    def navigate_to_editor(self) -> Page:
        page = self.engine.get_page_for_platform("toutiao")
        if "publish" not in (page.url or ""):
            try:
                page.goto(ToutiaoLocators.EDITOR_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                # 头条 SPA 的后台长连接可能让 domcontentloaded 超时；只有标题框
                # 已真实可见时才允许继续，否则保留原异常并 fail closed。
                title = page.locator(ToutiaoLocators.TITLE_INPUT).first
                if title.count() == 0 or not title.is_visible():
                    raise
                print("[WARN] 头条号导航超时，但编辑器已可见，继续执行")
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

        # 当前头条抽屉先显示「本地上传」入口；点击后才挂载真正的 file input。
        local_upload = self.page.locator('button:visible:has-text("本地上传")').first
        if local_upload.count() == 0:
            raise RuntimeError("未找到头条号本地上传入口")
        self.human.human_click(local_upload)

        file_input = self.page.locator(
            '#upload-drag-input, .btn-upload-handle input[type="file"], input[type="file"][accept*="image"]'
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
        feedback = _feedback_text(self.page)
        if "保存失败" in feedback:
            raise RuntimeError(f"头条号编辑器保存失败，阻止提交: {feedback.strip()}")
        if not getattr(article, "cover_path", None) or getattr(article, "cover_mode", None) == "force_off":
            self._select_no_cover()
        need_ai = should_declare_ai(article.title, article.body, article.ai_declaration_mode or "auto")
        if need_ai:
            self._set_ai_declaration()

    def _select_no_cover(self):
        # 正文输入后头条会自动打开 AI 助手抽屉；点击官方遮罩关闭，
        # 否则其 mask 会拦截所有封面单选框的真实鼠标事件。
        mask = self.page.locator(".ai-assistant-drawer .byte-drawer-mask:visible").first
        if mask.count() > 0:
            mask.click(force=True, position={"x": 10, "y": 10})
            time.sleep(0.3)

        radio = self.page.locator('label.byte-radio:has-text("无封面")').first
        if radio.count() == 0:
            raise RuntimeError("未找到头条号无封面选项")

        radio_input = radio.locator('input[type="radio"]').first

        def selected():
            try:
                if radio_input.count() > 0 and radio_input.is_checked():
                    return True
            except Exception:
                pass
            classes = (radio.get_attribute("class") or "").split()
            return "byte-radio-checked" in classes or radio.get_attribute("aria-checked") == "true"

        if not selected():
            # AI 助手遮罩可能不可关闭；force click 仍把真实点击发送到 React 单选框，
            # 并以 input.checked 而非易变 class 作为最终证据。
            radio.click(force=True)
            time.sleep(0.5)
        if not selected():
            raise RuntimeError("头条号未切换到无封面")
        print("[INFO] 头条号已切换为无封面")

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
        mask = self.page.locator(
            ".ai-assistant-drawer .byte-drawer-mask:visible"
        ).first
        if mask.count() > 0:
            mask.click(force=True, position={"x": 10, "y": 10})
            time.sleep(0.3)

        click_publish_with_evidence(
            self.page,
            ToutiaoLocators.PUBLISH_BUTTON_TEXTS,
            ToutiaoLocators.CONFIRM_PUBLISH_TEXTS,
            "头条号",
            confirm_scope_selector=ToutiaoLocators.CONFIRM_DIALOG_SELECTOR,
            allow_unscoped_confirm=True,
            failure_markers=ToutiaoLocators.SUBMIT_FAILURE_MARKERS,
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

    # ── 草稿检查点协议 ──────────────────────────────────────

    def verify_draft_checkpoint(self) -> DraftCheckpoint:
        from datetime import datetime, timezone
        try:
            self.page.goto(ToutiaoLocators.DRAFT_MANAGEMENT_URL or ToutiaoLocators.MANAGEMENT_URL,
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
