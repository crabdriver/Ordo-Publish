from __future__ import annotations

import time
from pathlib import Path

try:
    from patchright.sync_api import Page, Frame
except ImportError:
    from playwright.sync_api import Page, Frame

from markdown_utils import render_markdown_plain_text, should_declare_ai
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload, PlaywrightBasePublisher, PublishResult,
)
from ordo_engine.platforms.playwright._common import verify_result_common
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
        ef = self._ef()
        file_input = ef.locator(BilibiliLocators.COVER_FILE_INPUT)
        if file_input.count() > 0:
            path = Path(cover_path).expanduser().resolve()
            file_input.set_input_files(str(path))
            print(f"[INFO] B站封面已上传: {path.name}")
            time.sleep(2)
        else:
            print("[WARN] 未找到B站封面上传 input，跳过封面")

    def configure_settings(self, article: ArticlePayload):
        self._expand_settings_in_frame()

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
        # 优先用 class 精确匹配蓝色发布按钮
        btn = ef.locator(f'button.{BilibiliLocators.PUBLISH_BUTTON_CLASS}:has-text("发布")')
        if btn.count() == 0:
            btn = ef.locator(f'button:has-text("发布")')
        if btn.count() > 0:
            btn.first.click()
            print("[INFO] B站发布按钮已点击")
            time.sleep(2)
        else:
            raise RuntimeError("未找到B站发布按钮")

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
