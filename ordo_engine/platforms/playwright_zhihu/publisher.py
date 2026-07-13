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
    PlaywrightBasePublisher,
    PublishResult,
)
from ordo_engine.platforms.playwright.engine import PlaywrightEngine
from ordo_engine.platforms.playwright.human import HumanBehavior
from ordo_engine.platforms.playwright._common import verify_result_common
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
        """人像化输入标题"""
        title_locator = self.page.locator(ZhihuLocators.TITLE_INPUT).first
        self.human.human_click(title_locator)
        time.sleep(0.3)

        # Playwright fill() handles React controlled inputs correctly
        title_locator.fill("")
        time.sleep(0.2)

        # Type character by character for human-like effect
        self.human.human_type(title, speed="normal")

        # Verify
        has_value = title_locator.evaluate("el => 'value' in el")
        actual = title_locator.input_value() if has_value else title_locator.inner_text()
        print(f"[INFO] 知乎标题已输入: 《{actual[:50]}》")

    def fill_body(self, body: str):
        """填写正文（纯文本）"""
        plain_body = render_markdown_plain_text(body)

        editor = self._find_editor_element()
        self.human.human_click(editor)
        time.sleep(0.5)

        # Clear existing content
        mod = self.human._modifier
        self.page.keyboard.press(f"{mod}+a")
        self.page.keyboard.press("Delete")
        time.sleep(0.3)

        if len(plain_body) > 500:
            print(f"[INFO] 正文较长 ({len(plain_body)} 字)，使用剪贴板粘贴")
            self.human.human_paste_without_select(plain_body)
        else:
            print(f"[INFO] 正文较短 ({len(plain_body)} 字)，模拟打字输入")
            self.human.human_type(plain_body, speed="fast")

        time.sleep(0.5)

        body_length = self.page.evaluate(
            """
            () => {
                const editor = document.querySelector(
                    '.public-DraftEditor-content, .ProseMirror, '
                    + '[data-lexical-editor="true"], [contenteditable="true"]'
                );
                return (editor?.innerText || '').trim().length;
            }
            """
        )
        print(f"[INFO] 知乎正文已写入，编辑器字数: {body_length}")

    def upload_cover(self, cover_path: Path):
        """上传封面"""
        path = Path(cover_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"封面文件不存在: {path}")

        file_input = self.page.locator(ZhihuLocators.COVER_FILE_INPUT)
        if file_input.count() == 0:
            print("[WARN] 未找到知乎封面上传 input，跳过封面")
            return

        file_input.set_input_files(str(path))
        print(f"[INFO] 知乎封面已上传: {path.name}")
        time.sleep(2)
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
        """点击发布按钮"""
        publish_btn = self._find_visible_button(ZhihuLocators.PUBLISH_BUTTON_TEXTS)
        if not publish_btn:
            raise RuntimeError("未找到知乎发布按钮")

        print("[INFO] 点击知乎发布按钮...")
        self.human.human_click(publish_btn)
        time.sleep(1)

        # Check for confirmation dialog
        confirm_btn = self._find_visible_button(ZhihuLocators.CONFIRM_PUBLISH_TEXTS)
        if confirm_btn:
            self.human.human_wait(0.5, 1.0)
            print("[INFO] 点击知乎确认发布...")
            self.human.human_click(confirm_btn)

        time.sleep(3)

    def save_draft(self):
        """保存草稿"""
        draft_btn = self._find_visible_button(ZhihuLocators.SAVE_DRAFT_TEXTS)
        if draft_btn:
            self.human.human_click(draft_btn)
            time.sleep(2)
        else:
            # Trigger auto-save
            self.page.keyboard.press("Tab")
            time.sleep(3)
        print("[INFO] 知乎草稿已保存")

    def verify_result(self, mode: str) -> PublishResult:
        """验证结果"""
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

    def _find_editor_element(self):
        """找到符合尺寸要求的编辑区域"""
        selectors = ZhihuLocators.EDITOR_AREA.split(", ")
        for selector in selectors:
            elements = self.page.locator(selector.strip())
            count = elements.count()
            for i in range(count):
                el = elements.nth(i)
                box = el.bounding_box()
                if (
                    box
                    and box["width"] >= ZhihuLocators.EDITOR_AREA_MIN_WIDTH
                    and box["height"] >= ZhihuLocators.EDITOR_AREA_MIN_HEIGHT
                ):
                    return el
        # Fallback
        return self.page.locator('[contenteditable="true"]').first

    def _find_visible_button(self, texts: list):
        """查找包含指定文本的可见按钮"""
        for text in texts:
            btn = self.page.locator(f'button:visible:has-text("{text}")')
            if btn.count() > 0:
                return btn.first
        return None
