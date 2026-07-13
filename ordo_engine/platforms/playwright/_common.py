from __future__ import annotations

"""Playwright 发布器共享工具函数

各平台 publisher 可复用的通用逻辑，避免重复代码。
"""

import re
import time
import unicodedata
from pathlib import Path
from typing import Optional

try:
    from patchright.sync_api import Locator, Page
except ImportError:
    from playwright.sync_api import Locator, Page

from ordo_engine.platforms.playwright.human import HumanBehavior
from ordo_engine.platforms.playwright.base_publisher import PublishResult
from markdown_utils import render_markdown_plain_text, should_declare_ai


FEEDBACK_SELECTOR = '[role="alert"], [role="status"], .toast, .Toast'


def find_visible_button(page: Page, texts: list, button_class: str = None) -> Optional[Locator]:
    """查找包含指定文本的可见按钮"""
    for text in texts:
        if button_class:
            btn = page.locator(f'button.{button_class}:visible:has-text("{text}")')
        else:
            btn = page.locator(f'button:visible:has-text("{text}")')
        if btn.count() > 0:
            return btn.first
        # 也尝试匹配 a/span/div 按钮
        for tag in ["a", "span", "div"]:
            el = page.locator(f'{tag}:visible:has-text("{text}")')
            if el.count() > 0:
                return el.first
    return None


def find_editor_element(page: Page, selectors_str: str, min_width: int = 300, min_height: int = 100) -> Locator:
    """找到符合尺寸要求的编辑区域"""
    selectors = [s.strip() for s in selectors_str.split(",")]
    for selector in selectors:
        elements = page.locator(selector)
        count = elements.count()
        for i in range(count):
            el = elements.nth(i)
            box = el.bounding_box()
            if box and box["width"] >= min_width and box["height"] >= min_height:
                return el
    return page.locator('[contenteditable="true"]').first


def fill_title_common(human: HumanBehavior, page: Page, title: str, title_selector: str, platform: str):
    """通用标题填写逻辑"""
    title_locator = page.locator(title_selector).first
    human.human_click(title_locator)
    time.sleep(0.3)

    # 清空
    title_locator.fill("")
    time.sleep(0.2)

    # 人像化输入
    human.human_type(title, speed="normal")

    # 验证
    try:
        has_value = title_locator.evaluate("el => 'value' in el")
        actual = title_locator.input_value() if has_value else title_locator.inner_text()
        print(f"[INFO] {platform}标题已输入: 《{actual[:50]}》")
    except Exception:
        pass


def fill_body_common(human: HumanBehavior, page: Page, body: str, editor_selector: str, platform: str, min_width: int = 300, min_height: int = 100):
    """通用正文填写逻辑"""
    plain_body = render_markdown_plain_text(body)

    editor = find_editor_element(page, editor_selector, min_width, min_height)
    human.human_click(editor)
    time.sleep(0.5)

    # 清空（兼容 Page / Frame）
    try:
        mod = human._modifier
        page.keyboard.press(f"{mod}+a")
        page.keyboard.press("Delete")
    except AttributeError:
        # Frame 对象没有 .keyboard 属性，用 JS execCommand 替代
        page.evaluate(
            "() => { document.execCommand('selectAll'); document.execCommand('delete'); }"
        )
    time.sleep(0.3)

    if len(plain_body) > 500:
        print(f"[INFO] {platform}正文较长 ({len(plain_body)} 字)，使用剪贴板粘贴")
        human.human_paste_without_select(plain_body)
    else:
        print(f"[INFO] {platform}正文较短 ({len(plain_body)} 字)，模拟打字输入")
        human.human_type(plain_body, speed="fast")

    time.sleep(0.5)

    # 验证
    try:
        body_length = page.evaluate(
            """() => {
                const editor = document.querySelector(
                    '.public-DraftEditor-content, .ProseMirror, .ql-editor, '
                    + '[data-lexical-editor="true"], [contenteditable="true"], '
                    + 'textarea#arthur-editor'
                );
                return (editor?.innerText || editor?.value || '').trim().length;
            }"""
        )
        print(f"[INFO] {platform}正文已写入，编辑器字数: {body_length}")
    except Exception:
        pass


def upload_cover_common(page: Page, cover_path: Path, cover_selector: str, platform: str):
    """通用封面上传逻辑"""
    path = Path(cover_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"封面文件不存在: {path}")

    file_input = page.locator(cover_selector)
    if file_input.count() == 0:
        print(f"[WARN] 未找到{platform}封面上传 input，跳过封面")
        return

    file_input.set_input_files(str(path))
    print(f"[INFO] {platform}封面已上传: {path.name}")
    time.sleep(2)


def click_publish_common(human: HumanBehavior, page: Page, publish_texts: list, confirm_texts: list, platform: str, button_class: str = None):
    """通用发布按钮点击逻辑"""
    publish_btn = find_visible_button(page, publish_texts, button_class)
    if not publish_btn:
        raise RuntimeError(f"未找到{platform}发布按钮")

    print(f"[INFO] 点击{platform}发布按钮...")
    human.human_click(publish_btn)
    time.sleep(1)

    # 检查确认对话框
    confirm_btn = find_visible_button(page, confirm_texts)
    if confirm_btn:
        human.human_wait(0.5, 1.0)
        print(f"[INFO] 点击{platform}确认发布...")
        human.human_click(confirm_btn)

    time.sleep(3)


def save_draft_common(human: HumanBehavior, page: Page, draft_texts: list, platform: str):
    """通用保存草稿逻辑"""
    draft_btn = find_visible_button(page, draft_texts)
    if draft_btn:
        human.human_click(draft_btn)
        time.sleep(2)
    else:
        page.keyboard.press("Tab")
        time.sleep(3)
    print(f"[INFO] {platform}草稿已保存")


def _normalize_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def _page_text(page: Page) -> str:
    try:
        return page.evaluate("() => document.body.innerText || ''")
    except Exception:
        return ""


def _feedback_text(page: Page) -> str:
    try:
        feedback = page.locator(FEEDBACK_SELECTOR)
        texts = []
        for index in range(feedback.count()):
            item = feedback.nth(index)
            if item.is_visible():
                texts.append(item.inner_text())
        return "\n".join(texts)
    except Exception:
        return ""


def _has_feedback_marker(feedback_text: str, markers: list) -> bool:
    lines = {_normalize_title(line) for line in feedback_text.splitlines() if _normalize_title(line)}
    return any(_normalize_title(marker) in lines for marker in markers)


def _has_feedback_phrase(feedback_text: str, markers: list) -> bool:
    lines = [_normalize_title(line) for line in feedback_text.splitlines() if _normalize_title(line)]
    phrases = [_normalize_title(marker) for marker in markers if _normalize_title(marker)]
    return any(phrase in line for phrase in phrases for line in lines)


def verify_result_common(page: Page, platform: str, mode: str, published_url_pattern: str,
                          success_markers: list, draft_markers: list, limit_markers: list,
                          management_url: str = None, draft_management_url: str = None,
                          *, expected_title: str = "") -> PublishResult:
    """通用结果验证逻辑"""
    current_url = page.url

    if mode == "publish":
        if re.search(published_url_pattern, current_url):
            print(f"[INFO] 已发布到{platform}: {current_url}")
            return PublishResult(
                platform=platform, status="published",
                current_url=current_url, page_state="published",
                smoke_step="verify", message=f"已发布到{platform}: {current_url}",
            )

    feedback_text = _feedback_text(page)
    if _has_feedback_phrase(feedback_text, limit_markers):
        return PublishResult(
            platform=platform, status="limit_reached",
            current_url=current_url, page_state="limit_reached",
            smoke_step="verify", message="达到发布上限",
        )

    if mode == "publish":
        if _has_feedback_marker(feedback_text, success_markers):
            return PublishResult(
                platform=platform, status="published",
                current_url=current_url, page_state="published",
                smoke_step="verify",
            )
    else:
        if _has_feedback_marker(feedback_text, draft_markers):
            return PublishResult(
                platform=platform, status="draft_only",
                current_url=current_url, page_state="draft_saved",
                smoke_step="verify", message=f"已写入{platform}草稿页",
            )

    # 跳转到管理页面验证
    url = draft_management_url if (mode == "draft" and draft_management_url) else management_url
    if url and expected_title:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
        except Exception:
            return PublishResult(
                platform=platform, status="submitted_unverified",
                current_url=page.url, page_state="submitted_unverified",
                smoke_step="verify", message="提交结果无法确认：管理页导航失败",
            )

        expected = _normalize_title(expected_title)
        management_titles = {
            _normalize_title(line) for line in _page_text(page).splitlines()
            if _normalize_title(line)
        }
        if expected and expected in management_titles:
            status = "draft_only" if mode == "draft" else "published"
            page_state = "draft_saved" if mode == "draft" else "published"
            return PublishResult(
                platform=platform, status=status,
                current_url=page.url, page_state=page_state,
                smoke_step="verify",
            )

    return PublishResult(
        platform=platform, status="submitted_unverified",
        current_url=page.url, page_state="submitted_unverified",
        smoke_step="verify", message="提交结果无法确认：未找到精确标题证据",
    )
