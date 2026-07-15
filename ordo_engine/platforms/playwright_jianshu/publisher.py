from __future__ import annotations

import re
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
        path = Path(cover_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"封面文件不存在: {path}")
        image_tool = self.page.locator("a.fa.fa-picture-o").first
        if image_tool.count() == 0:
            raise RuntimeError("未找到简书图片上传按钮")
        self.human.human_click(image_tool)
        file_input = self.page.locator("input#kalamu-upload-image").first
        file_input.wait_for(state="attached", timeout=5000)
        file_input.set_input_files(str(path))
        self.page.wait_for_function(
            """() => /!\\[.*?\\]\\(https?:\\/\\/upload-images\\.jianshu\\.io\\/upload_images\\/[^)]+\\)/.test(
                document.querySelector('textarea._3swFR.source, textarea#arthur-editor')?.value || ''
            )""",
            timeout=20000,
        )
        self.page.evaluate(
            """() => {
                const source = document.querySelector('textarea._3swFR.source, textarea#arthur-editor');
                if (!source) throw new Error('简书正文源文本框不存在');
                const match = source.value.match(/!\\[.*?\\]\\(https?:\\/\\/upload-images\\.jianshu\\.io\\/upload_images\\/[^)]+\\)/);
                if (!match) throw new Error('简书封面 CDN 链接不存在');
                source.value = match[0] + '\\n\\n' + source.value.replace(match[0], '').trim();
                source.dispatchEvent(new Event('input', {bubbles: true}));
                source.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
        print(f"[INFO] 简书封面已插入正文首行: {path.name}")
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
        if mode == "publish":
            note_id = getattr(self, "_note_id", "") or self._note_id_from_url(self.page.url or "")
            if note_id:
                try:
                    note = self.page.evaluate(
                        """async (noteId) => {
                            const response = await fetch(`/author/notes/${noteId}`, {credentials: 'include'});
                            if (!response.ok) throw new Error(`note API ${response.status}`);
                            return await response.json();
                        }""",
                        note_id,
                    )
                    if note.get("shared") is True and note.get("slug"):
                        published_url = f"https://www.jianshu.com/p/{note['slug']}"
                        return PublishResult(
                            platform=self.platform,
                            status="published",
                            current_url=published_url,
                            page_state="published",
                            smoke_step="verify",
                            message=f"已发布到简书: {published_url}",
                        )
                except Exception as exc:
                    print(f"[WARN] 简书文章 API 核验失败，回退页面核验: {exc}")
        return verify_result_common(
            self.page, "简书", mode,
            JianshuLocators.PUBLISHED_URL_PATTERN,
            JianshuLocators.PUBLISH_SUCCESS_MARKERS,
            JianshuLocators.DRAFT_SUCCESS_MARKERS,
            JianshuLocators.LIMIT_MARKERS,
            JianshuLocators.MANAGEMENT_URL,
            JianshuLocators.DRAFT_MANAGEMENT_URL,
            expected_title=self._article.title,
        )

    @staticmethod
    def _note_id_from_url(url: str) -> str:
        match = re.search(r"/notes/(\d+)", url or "")
        return match.group(1) if match else ""

    # ── 草稿检查点协议 ──────────────────────────────────────

    def verify_draft_checkpoint(self) -> DraftCheckpoint:
        """核验简书草稿：导航到文章管理页，匹配标题。"""
        from datetime import datetime, timezone
        try:
            self.page.goto(JianshuLocators.DRAFT_MANAGEMENT_URL or JianshuLocators.MANAGEMENT_URL,
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
        """从简书草稿发布。"""
        self._note_id = self._note_id_from_url(draft_ref)
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
