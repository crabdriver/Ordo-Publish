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
    ArticlePayload, DraftCheckpoint, PlaywrightBasePublisher,
    PublishLimitReached, PublishResult,
)
from ordo_engine.platforms.playwright._common import (
    fill_title_common, fill_body_common, upload_cover_common,
    click_publish_with_evidence, save_draft_common, verify_result_common,
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
        # 保存 note_id 供后续 verify_result 的 note API 核验使用
        self._note_id = self._note_id_from_url(page.url or "")
        print(f"[INFO] 简书新文章编辑器已就绪: {page.url} (note_id={self._note_id})")
        return page

    def _open_new_article(self, page: Page):
        """点击『新建文章』打开空白新笔记，并等待标题框出现

        简书点击新建文章后行为：
        1. 有时 URL 立即变为 /notes/新ID → 等 URL 变化
        2. 有时先出空白编辑器再异步分配 note ID → URL 暂时不变
        因此 URL 等待是软性的，不阻塞；以标题框可见为准。
        """
        previous_url = page.url or ""
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
        try:
            if clicked:
                # 软性等待 URL 变化（简书可能异步分配 note ID）
                try:
                    page.wait_for_url(
                        lambda url: str(url) != previous_url and "/notes/" in str(url),
                        timeout=8000,
                    )
                    print(f"[INFO] 简书新笔记 URL 已变化: {page.url}")
                except Exception:
                    print(f"[INFO] 简书新建文章后 URL 未变化（可能异步分配 note ID），以标题框为准")
            # 硬性等待：标题框必须出现
            page.wait_for_selector(JianshuLocators.TITLE_INPUT, state="visible", timeout=30000)
            # 额外等待：确保编辑器完全初始化
            time.sleep(1)
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
        # 发布前预检：检查是否已达每日限额
        page_text = self.page.evaluate("() => document.body.innerText || ''")
        for marker in JianshuLocators.LIMIT_MARKERS:
            if marker in page_text:
                raise PublishLimitReached(f"简书{marker}")
        if "发布成功，点击查看文章" in page_text:
            # 可能上一篇文章刚刚发布成功，toast 还在屏幕上，这不是错误
            print("[INFO] 检测到简书页面含发布成功提示（可能是前序文章残留），继续发布")

    def click_publish(self):
        click_publish_with_evidence(
            self.page,
            JianshuLocators.PUBLISH_BUTTON_TEXTS,
            JianshuLocators.CONFIRM_PUBLISH_TEXTS,
            "简书",
            confirm_scope_selector=JianshuLocators.CONFIRM_DIALOG_SELECTOR,
            allow_unscoped_confirm=True,
            failure_markers=JianshuLocators.SUBMIT_FAILURE_MARKERS,
        )
        # 发布确认后立即检测页面状态，捕获限额或成功信号
        self._capture_post_publish_state()

    def _capture_post_publish_state(self):
        """发布确认后立即检测页面文字，避免进入模糊的 submitted_unverified"""
        time.sleep(2)  # 等 toast / 错误提示出现
        page_text = self.page.evaluate("() => document.body.innerText || ''")
        current_url = self.page.url or ""

        # 1. 检测每日限额
        for marker in JianshuLocators.LIMIT_MARKERS:
            if marker in page_text:
                raise PublishLimitReached(f"简书{marker}")

        # 2. 检测发布成功
        for marker in JianshuLocators.PUBLISH_SUCCESS_MARKERS:
            if marker in page_text:
                # 尝试从页面提取已发布的 URL
                try:
                    published_url = self.page.evaluate(
                        """() => {
                            const a = document.querySelector('a[href*="/p/"]');
                            return a ? a.href : '';
                        }"""
                    )
                    if published_url:
                        print(f"[INFO] 简书发布成功，从页面提取到已发布URL: {published_url}")
                        self._published_url_from_toast = published_url
                except Exception:
                    pass
                print(f"[INFO] 简书页面反馈: {marker}")
                return

        # 3. 检测 URL 是否直接跳转到已发布页
        if re.search(JianshuLocators.PUBLISHED_URL_PATTERN, current_url):
            print(f"[INFO] 简书已跳转到已发布URL: {current_url}")
            return

        print(f"[INFO] 简书发布后页面URL: {current_url}，等待核验确认")

    def save_draft(self):
        # 简书的自动保存机制：切换焦点即可触发
        self.page.keyboard.press("Tab")
        time.sleep(3)
        print("[INFO] 简书草稿已保存（自动保存）")

    def verify_result(self, mode: str) -> PublishResult:
        if mode == "publish":
            # 优先使用已保存的 _note_id（正常发布流程中 navigate_to_editor 保存）
            # 兜底从当前 URL 提取（reconcile 场景）
            note_id = (
                getattr(self, "_note_id", "")
                or self._note_id_from_url(self.page.url or "")
            )
            if note_id:
                # 轮询简书 note API（发布到落库可能有几秒延迟）
                for attempt in range(6):
                    try:
                        note = self.page.evaluate(
                            """async (noteId) => {
                                const response = await fetch(
                                    `/author/notes/${noteId}`,
                                    {credentials: 'include'}
                                );
                                if (!response.ok) throw new Error(`note API ${response.status}`);
                                const data = await response.json();
                                return {shared: data.shared, slug: data.slug || ''};
                            }""",
                            note_id,
                        )
                        if note.get("shared") is True:
                            slug = note.get("slug", "")
                            if slug:
                                published_url = f"https://www.jianshu.com/p/{slug}"
                                return PublishResult(
                                    platform=self.platform,
                                    status="published",
                                    current_url=published_url,
                                    page_state="published",
                                    smoke_step="verify",
                                    message=f"API确认已发布到简书: {published_url}",
                                )
                            else:
                                # shared=True 但无 slug 极少见，再等一轮
                                print(f"[INFO] 简书 note API: shared=True 但 slug 为空，等待... (attempt {attempt+1}/6)")
                        else:
                            print(f"[INFO] 简书 note API: shared={note.get('shared')}, 等待... (attempt {attempt+1}/6)")
                    except Exception as exc:
                        if attempt == 5:
                            print(f"[WARN] 简书 note API 核验失败（已重试6次），回退页面核验: {exc}")
                        else:
                            print(f"[INFO] 简书 note API 核验异常，等待重试: {exc}")
                    if attempt < 5:
                        time.sleep(5)
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

    def _reconcile(self, mode: str) -> PublishResult:
        """简书核验：先导航笔记本列表提取 note_id，再做 API 核验"""
        self._submission_started = True
        self.page = self.engine.context.new_page()

        # 导航到笔记本列表，找到文章链接提取 note_id
        self.page.goto(JianshuLocators.DRAFT_MANAGEMENT_URL,
                       wait_until="domcontentloaded", timeout=30000)
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        time.sleep(3)

        title = getattr(self._article, "title", "")
        if title:
            try:
                escaped = title[:30].replace("'", "\\'")
                note_id = self.page.evaluate(f"""
                    () => {{
                        const links = Array.from(document.querySelectorAll(
                            'ul._2TxA- a[href*="/notes/"], a[href*="/notes/"]'
                        ));
                        const target = links.find(a =>
                            (a.innerText || '').includes('{escaped}')
                        );
                        if (!target) return '';
                        const match = target.href.match(/notes\\/(\\d+)/);
                        return match ? match[1] : '';
                    }}
                """)
                if note_id:
                    self._note_id = note_id
                    print(f"[INFO] 简书 reconcile: 从列表提取 note_id={note_id}")
            except Exception as exc:
                print(f"[INFO] 简书 reconcile: 列表提取 note_id 失败: {exc}")

        result = self.verify_result(mode)
        if not self._is_terminal(result.status, mode):
            result = self._unverified_result(result, reconciliation=True)
        self._persist_result(result, mode)
        return result
