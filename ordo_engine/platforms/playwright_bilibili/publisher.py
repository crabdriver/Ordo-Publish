from __future__ import annotations

import time
from pathlib import Path

try:
    from patchright.sync_api import Page, Frame
except ImportError:
    from playwright.sync_api import Page, Frame

from markdown_utils import render_markdown_plain_text, should_declare_ai
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload, DraftCheckpoint, PlaywrightBasePublisher, PublishResult,
)
from ordo_engine.platforms.playwright._common import verify_result_common, find_visible_button
from ordo_engine.platforms.playwright_bilibili.locators import BilibiliLocators


class BilibiliPlaywrightPublisher(PlaywrightBasePublisher):
    """B站专栏文章 Playwright 人像化发布器

    注意：B站 专栏编辑器的标题/正文/按钮全部位于 iframe 内
    （iframe src 含 'read-editor'）。本发布器自动检测并切换到 iframe 操作。
    为避免 Frame Locator 与主 Page HumanBehavior 混用导致递归溢出，
    所有编辑器内操作均直接使用 Frame 原生 API，不经过 human/common 函数。
    """

    platform = "bilibili"

    # ── iframe 管理 ──────────────────────────────────────────

    def _find_editor_frame(self, page: Page) -> Frame | None:
        for f in page.frames:
            if f.url and "read-editor" in f.url:
                return f
        return None

    def _ef(self) -> Frame:
        """获取编辑器 iframe 的 Frame 对象"""
        frame = getattr(self, "_editor_frame", None)
        if not frame:
            raise RuntimeError("B站编辑器 iframe 未初始化，请先调用 navigate_to_editor")
        return frame

    # ── 导航与登录 ──────────────────────────────────────────

    def navigate_to_editor(self) -> Page:
        page = self.engine.get_page_for_platform("bilibili")
        if "new-edit" not in (page.url or ""):
            page.goto(BilibiliLocators.EDITOR_URL, wait_until="domcontentloaded", timeout=30000)

        login_markers = ["登录", "扫码", "sign in", "login", "二维码", "手机号"]

        def _iframe_title_ready() -> bool:
            try:
                frame = self._find_editor_frame(page)
                if frame:
                    el = frame.locator(BilibiliLocators.TITLE_INPUT)
                    return el.count() > 0 and el.first.is_visible()
            except Exception:
                pass
            return False

        def _on_login_page() -> bool:
            try:
                txt = page.evaluate("() => document.body?.innerText || ''")
            except Exception:
                txt = ""
            return any(m in txt for m in login_markers)

        if self.engine.headless and not _iframe_title_ready() and _on_login_page():
            raise RuntimeError(
                "B站 登录已失效；请运行 publish.py --bootstrap-browser"
            )

        time.sleep(5)

        needs_login = False
        deadline = time.time() + 25
        while time.time() < deadline:
            if _iframe_title_ready():
                self._editor_frame = self._find_editor_frame(page)
                print(f"[INFO] B站编辑器已就绪 (iframe): {page.url}")
                return page
            if _on_login_page():
                needs_login = True
                break
            frame = self._find_editor_frame(page)
            if frame:
                time.sleep(3)
                if _iframe_title_ready():
                    self._editor_frame = frame
                    print(f"[INFO] B站编辑器已就绪 (iframe): {page.url}")
                    return page
            time.sleep(2)

        if not needs_login:
            needs_login = True
            print("[INFO] B站 停留在编辑器页但 iframe 未渲染，判定为需要登录")

        if self.engine.headless:
            raise RuntimeError("B站 登录已失效；请运行 publish.py --bootstrap-browser")

        print("[INFO] 检测到 B站 需要登录，请在浏览器窗口中扫码/登录...")
        print("[INFO] 等待登录完成（最多 300 秒）")
        self.engine.screenshot(page, "bilibili", "login_required")

        max_wait = 300
        for i in range(max_wait // 3):
            time.sleep(3)
            if i > 0 and i % 5 == 0:
                try:
                    page.goto(BilibiliLocators.EDITOR_URL, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(8)
                except Exception:
                    pass
            if _iframe_title_ready():
                self._editor_frame = self._find_editor_frame(page)
                print("[INFO] B站 登录成功，编辑器已就绪")
                time.sleep(2)
                return page
            if i % 5 == 4:
                print(f"[INFO] 仍在等待 B站 登录... ({(i+1)*3}s)")

        raise RuntimeError("B站 登录超时（300秒），请重试")

    # ── 编辑器内容填写（直接用 Frame API） ─────────────────

    def fill_title(self, title: str):
        """直接在 iframe 内填写标题（不走 human/common，避免递归）"""
        ef = self._ef()
        loc = ef.locator(BilibiliLocators.TITLE_INPUT).first
        loc.click()
        time.sleep(0.3)
        loc.fill("")
        time.sleep(0.2)
        # 用 evaluate 写入（Frame 无 keyboard 属性）
        ef.evaluate(
            "(title) => { "
            "  const el = document.querySelector('textarea.title-input__inner');"
            "  if (el) { el.value = title; el.dispatchEvent(new Event('input', {bubbles:true})); } "
            "}",
            title,
        )
        # 验证
        try:
            actual = loc.input_value()
            print(f"[INFO] B站标题已输入: 《{actual[:50]}》")
        except Exception:
            print(f"[INFO] B站标题已输入: 《{title[:50]}》")

    def fill_body(self, body: str):
        """直接在 iframe 内填写正文（不走 human/common）"""
        from markdown_utils import render_markdown_plain_text
        plain = render_markdown_plain_text(body)
        ef = self._ef()

        # 找到 ProseMirror/Tiptap 编辑器区域
        editor_loc = ef.locator(BilibiliLocators.EDITOR_AREA).first
        editor_loc.click()
        time.sleep(0.5)

        # 用 JS 全选 + 删除 + 插入文本
        ef.evaluate(
            "(text) => { "
            "  const ed = document.querySelector('.tiptap.ProseMirror') || document.querySelector('[role=textbox][contenteditable=true]'); "
            "  if (ed) { ed.focus(); document.execCommand('selectAll'); document.execCommand('delete'); document.execCommand('insertText', false, text); } "
            "}",
            plain,
        )
        print(f"[INFO] B站正文已写入 ({len(plain)} 字)")
        time.sleep(0.5)

    # ── 封面与设置 ──────────────────────────────────────────

    def upload_cover(self, cover_path: Path):
        self._expand_settings_in_frame()
        path = Path(cover_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"封面文件不存在: {path}")

        ef = self._ef()
        label = ef.locator(
            '.form-item-label:has-text("自定义封面"), label:has-text("自定义封面")'
        ).first
        if label.count() == 0:
            raise RuntimeError("未找到B站自定义封面设置")
        switch = label.locator("xpath=..").locator(".vui_switch--switch").first
        if switch.count() == 0:
            raise RuntimeError("未找到B站自定义封面开关")
        if "is-checked" not in (switch.get_attribute("class") or "").split():
            switch.click()
            time.sleep(1)

        upload = ef.locator("div.upload-button").first
        if upload.count() == 0:
            raise RuntimeError("未找到B站添加封面入口")
        upload.click()
        preview = ef.locator(BilibiliLocators.COVER_UPLOAD_SUCCESS)
        before_sources = {
            source for index in range(preview.count())
            if (source := preview.nth(index).get_attribute("src"))
        }
        file_input = ef.locator('input[type="file"]').first
        file_input.wait_for(state="attached", timeout=10000)
        file_input.set_input_files(str(path))

        confirm = ef.locator('button:visible:has-text("确定")').first
        if confirm.count() == 0:
            raise RuntimeError("未找到B站封面确认按钮")
        confirm.click()
        try:
            preview.first.wait_for(state="visible", timeout=30000)
        except Exception as exc:
            raise RuntimeError("未找到B站封面上传完成证据") from exc
        deadline = time.time() + 30
        while time.time() < deadline:
            after_sources = {
                source for index in range(preview.count())
                if (source := preview.nth(index).get_attribute("src"))
                and source.startswith(("http://", "https://", "//"))
            }
            if after_sources and after_sources != before_sources:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("未找到B站本次封面上传完成证据")
        print(f"[INFO] B站封面已上传: {path.name}")

    def configure_settings(self, article: ArticlePayload):
        self._expand_settings_in_frame()
        publish_btn = self._ef().locator(
            'button.vui_button--blue:visible:has-text("发布")'
        ).first
        if publish_btn.count() == 0:
            raise RuntimeError("未找到B站发布按钮")
        if not publish_btn.is_enabled():
            raise RuntimeError("B站发布按钮仍不可用，请检查封面或必填发布设置")

    def _expand_settings_in_frame(self):
        """在 iframe 内展开发布设置面板"""
        try:
            ef = self._ef()
            btn = ef.locator(f'button:has-text("{BilibiliLocators.PUBLISH_SETTINGS_TEXT}")')
            if btn.count() > 0:
                is_expanded = btn.first.get_attribute("aria-expanded")
                if is_expanded != "true":
                    btn.first.click()
                    time.sleep(1)
                    print("[INFO] B站发布设置面板已展开")
        except Exception:
            pass

    # ── 发布/草稿（直接在 Frame 内点击按钮） ────────────────

    def click_publish(self):
        ef = self._ef()
        # 使用 find_visible_button 精确匹配发布按钮（避免匹配到"定时发布"等）
        btn = find_visible_button(ef, BilibiliLocators.PUBLISH_BUTTON_TEXTS, BilibiliLocators.PUBLISH_BUTTON_CLASS)
        if not btn:
            raise RuntimeError("未找到B站发布按钮")

        pre_url = self.page.url
        print("[INFO] B站发布按钮已点击")
        btn.click()
        time.sleep(2)

        confirm_btn = find_visible_button(ef, BilibiliLocators.CONFIRM_PUBLISH_TEXTS)
        if not confirm_btn:
            confirm_btn = find_visible_button(self.page, BilibiliLocators.CONFIRM_PUBLISH_TEXTS)
        if confirm_btn:
            print("[INFO] 点击B站确认发布...")
            confirm_btn.click()
            time.sleep(2)

        # 等待页面跳转或成功反馈（最多 20 秒）
        deadline = time.time() + 20
        while time.time() < deadline:
            if (self.page.url or "") != pre_url:
                break
            time.sleep(1)

    def save_draft(self):
        ef = self._ef()
        btn = ef.locator(f'button:has-text("{BilibiliLocators.SAVE_DRAFT_TEXTS[0]}")')
        if btn.count() == 0:
            # 兜底匹配
            btn = ef.locator(f'button:has-text("保存")')
        if btn.count() > 0:
            btn.first.click()
            print("[INFO] B站草稿保存按钮已点击")
            time.sleep(2)
        else:
            raise RuntimeError("未找到B站保存草稿按钮")

    # ── 验证（发布后可能跳转到主域，用主 page） ────────────

    def verify_result(self, mode: str) -> PublishResult:
        return verify_result_common(
            self.page, "B站", mode,
            BilibiliLocators.PUBLISHED_URL_PATTERN,
            BilibiliLocators.PUBLISH_SUCCESS_MARKERS,
            BilibiliLocators.DRAFT_SUCCESS_MARKERS,
            BilibiliLocators.LIMIT_MARKERS,
            BilibiliLocators.MANAGEMENT_URL,
            BilibiliLocators.DRAFT_MANAGEMENT_URL,
            expected_title=self._article.title,
        )

    # ── 草稿检查点协议 ──────────────────────────────────────

    def verify_draft_checkpoint(self) -> DraftCheckpoint:
        from datetime import datetime, timezone
        try:
            self.page.goto(BilibiliLocators.DRAFT_MANAGEMENT_URL or BilibiliLocators.MANAGEMENT_URL,
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
