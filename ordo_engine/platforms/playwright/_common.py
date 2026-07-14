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


def find_visible_button(page, texts: list, button_class: str = None) -> Optional[Locator]:
    """查找包含指定文本的可见按钮

    优先精确文本匹配（避免"发布"匹配到"定时发布"），
    无精确匹配时回退到子串匹配并选最短文本（最具体的按钮）。
    兼容 Page 和 Frame 对象。
    """
    for text in texts:
        candidates = []
        # 收集所有包含该文本的可见按钮
        if button_class:
            btns = page.locator(f'button.{button_class}:visible:has-text("{text}")')
        else:
            btns = page.locator(f'button:visible:has-text("{text}")')
        for i in range(btns.count()):
            candidates.append(btns.nth(i))
        # 也收集 a/span/div
        for tag in ["a", "span", "div"]:
            els = page.locator(f'{tag}:visible:has-text("{text}")')
            for i in range(els.count()):
                candidates.append(els.nth(i))

        if not candidates:
            continue

        # 优先返回精确匹配
        for c in candidates:
            try:
                if c.inner_text().strip() == text:
                    return c
            except Exception:
                pass

        # 回退：选文本最短的（最具体的，如"发布"优先于"定时发布"）
        best = candidates[0]
        best_len = float('inf')
        for c in candidates:
            try:
                c_len = len(c.inner_text().strip())
                if c_len < best_len:
                    best = c
                    best_len = c_len
            except Exception:
                pass
        return best
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
    time.sleep(0.5)

    # 验证标题完整性：逐字输入可能丢字符（如头条号丢"么"），截断时用 fill() 兜底
    try:
        has_value = title_locator.evaluate("el => 'value' in el")
        actual = (title_locator.input_value() if has_value else title_locator.inner_text()).strip()
        expected = title.strip()

        if actual != expected and len(actual) < len(expected):
            print(f"[WARN] {platform}标题可能截断({len(actual)}/{len(expected)}字)，用 fill() 补救")
            title_locator.fill("")
            time.sleep(0.2)
            title_locator.fill(expected)
            time.sleep(0.3)
            has_value2 = title_locator.evaluate("el => 'value' in el")
            actual = (title_locator.input_value() if has_value2 else title_locator.inner_text()).strip()

        print(f"[INFO] {platform}标题已输入: 《{actual[:50]}》")
    except Exception:
        print(f"[INFO] {platform}标题已输入: 《{title[:50]}》（未能验证）")


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


def upload_cover_common(
    page: Page, cover_path: Path, cover_selector: str, platform: str,
    *, success_selector: str | None = None,
):
    """通用封面上传逻辑"""
    path = Path(cover_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"封面文件不存在: {path}")

    file_input = page.locator(cover_selector)
    if file_input.count() == 0:
        raise RuntimeError(f"未找到{platform}封面上传 input")

    uploaded = page.locator(success_selector) if success_selector else None
    before_sources = set()
    if uploaded is not None:
        for index in range(uploaded.count()):
            source = uploaded.nth(index).get_attribute("src")
            if source:
                before_sources.add(source)

    file_input.set_input_files(str(path))
    if not success_selector:
        raise RuntimeError(f"未配置{platform}封面上传完成证据")
    try:
        uploaded.first.wait_for(state="visible", timeout=30000)
    except Exception as exc:
        raise RuntimeError(f"未找到{platform}封面上传完成证据") from exc
    if uploaded.count() == 0:
        raise RuntimeError(f"未找到{platform}封面上传完成证据")
    deadline = time.time() + 30
    while time.time() < deadline:
        after_sources = {
            source for index in range(uploaded.count())
            if (source := uploaded.nth(index).get_attribute("src"))
            and source.startswith(("http://", "https://"))
        }
        if after_sources and after_sources != before_sources and uploaded.first.is_visible():
            break
        time.sleep(0.25)
    else:
        raise RuntimeError(f"未找到{platform}本次封面上传完成证据")
    print(f"[INFO] {platform}封面已上传: {path.name}")


def click_publish_common(human: HumanBehavior, page: Page, publish_texts: list, confirm_texts: list, platform: str, button_class: str = None):
    """通用发布按钮点击逻辑

    点击主按钮后只处理一次明确确认，避免重复点击同一提交按钮。
    """
    publish_btn = find_visible_button(page, publish_texts, button_class)
    if not publish_btn:
        raise RuntimeError(f"未找到{platform}发布按钮")

    pre_url = page.url
    print(f"[INFO] 点击{platform}发布按钮...")
    human.human_click(publish_btn)
    time.sleep(1)

    confirm_btn = find_visible_button(page, confirm_texts)
    if confirm_btn:
        human.human_wait(0.5, 1.0)
        print(f"[INFO] 点击{platform}确认发布...")
        human.human_click(confirm_btn)
        time.sleep(2)

    # 等待页面过渡：URL 变化或出现成功/失败反馈（最多 30 秒）
    deadline = time.time() + 30
    while time.time() < deadline:
        current_url = page.url or ""
        if current_url != pre_url:
            print(f"[INFO] {platform}页面已跳转: {current_url[:80]}")
            break
        # 检查是否出现反馈提示（成功/失败/限流）
        try:
            feedback = page.locator(FEEDBACK_SELECTOR)
            if feedback.count() > 0:
                visible_feedback = [feedback.nth(i) for i in range(min(feedback.count(), 5))]
                if any(f.is_visible() for f in visible_feedback):
                    break
        except Exception:
            pass
        time.sleep(1)


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


def _all_frames_text(page: Page) -> str:
    """获取主帧 + 所有 iframe 的可见文字（B站管理页用 iframe）"""
    texts = []
    # 主帧
    try:
        texts.append(page.evaluate("() => document.body?.innerText || ''"))
    except Exception:
        pass
    # 子帧
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                frame_text = frame.evaluate("() => document.body?.innerText || ''")
                if frame_text:
                    texts.append(frame_text)
            except Exception:
                pass
    except Exception:
        pass
    return "\n".join(texts)


def _feedback_text(page: Page) -> str:
    """获取所有可见反馈提示文字（主帧 + iframe，B站提示在 iframe 内）"""
    texts = []
    # 主帧
    try:
        feedback = page.locator(FEEDBACK_SELECTOR)
        for index in range(feedback.count()):
            item = feedback.nth(index)
            if item.is_visible():
                texts.append(item.inner_text())
    except Exception:
        pass
    # 子帧（B站编辑器 iframe 内可能有成功提示）
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            feedback = frame.locator(FEEDBACK_SELECTOR)
            for index in range(feedback.count()):
                item = feedback.nth(index)
                try:
                    if item.is_visible():
                        texts.append(item.inner_text())
                except Exception:
                    pass
    except Exception:
        pass
    return "\n".join(texts)


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
        except Exception:
            return PublishResult(
                platform=platform, status="submitted_unverified",
                current_url=page.url, page_state="submitted_unverified",
                smoke_step="verify", message="提交结果无法确认：管理页导航失败",
            )

        expected = _normalize_title(expected_title)

        # SPA 管理列表通常需要较长时间渲染，带重试的动态等待
        max_attempts = 3
        for attempt in range(max_attempts):
            # 等网络空闲（SPA 数据加载完）
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            time.sleep(5)

            # 取主帧 + 所有 iframe 的文字（B站管理页用 iframe）
            all_text = _all_frames_text(page)
            management_titles = {
                _normalize_title(line) for line in all_text.splitlines()
                if _normalize_title(line)
            }

            # 精确匹配
            if expected and expected in management_titles:
                status = "draft_only" if mode == "draft" else "published"
                page_state = "draft_saved" if mode == "draft" else "published"
                return PublishResult(
                    platform=platform, status=status,
                    current_url=page.url, page_state=page_state,
                    smoke_step="verify",
                )

            # 还没找到，刷新重试（最后一次不刷新）
            if attempt < max_attempts - 1:
                print(f"[INFO] {platform}管理列表未找到文章《{expected_title[:30]}...》，刷新重试 ({attempt + 1}/{max_attempts})")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass

    return PublishResult(
        platform=platform, status="submitted_unverified",
        current_url=page.url, page_state="submitted_unverified",
        smoke_step="verify", message="提交结果无法确认：未找到精确标题证据",
    )
