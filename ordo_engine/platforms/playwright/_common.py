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
from ordo_engine.platforms.playwright.base_publisher import (
    PublishClickNoEffect,
    PublishResult,
)
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
        for i in range(min(btns.count(), 12)):
            candidates.append(btns.nth(i))
        # 只收集可交互元素。禁止扫描普通 span/div 祖先：SPA 页面中它们可能
        # 成百上千，并在节点失效时让 inner_text 默认等待 30 秒。
        for tag in ["a", '[role="button"]']:
            els = page.locator(f'{tag}:visible:has-text("{text}")')
            for i in range(min(els.count(), 12)):
                candidates.append(els.nth(i))

        if not candidates:
            continue

        # 优先返回精确匹配
        for c in candidates:
            try:
                if c.inner_text(timeout=500).strip() == text:
                    return c
            except Exception:
                pass

        # 回退：选文本最短的（最具体的，如"发布"优先于"定时发布"）
        best = candidates[0]
        best_len = float('inf')
        for c in candidates:
            try:
                c_len = len(c.inner_text(timeout=500).strip())
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

    def editor_text():
        has_value = editor.evaluate("el => 'value' in el")
        return editor.input_value() if has_value else editor.inner_text()

    def normalized(value):
        return " ".join(unicodedata.normalize("NFKC", value or "").split())

    # 必须验证同一个编辑器，而不是 document.querySelector 找到的任意旧编辑器。
    try:
        actual = editor_text()
        if normalized(actual) != normalized(plain_body):
            print(f"[WARN] {platform}正文未完整替换，使用 fill() 回退")
            editor.fill(plain_body)
            time.sleep(0.5)
            actual = editor_text()
        if normalized(actual) != normalized(plain_body):
            raise RuntimeError(f"{platform}正文写入校验失败，阻止提交")
        print(f"[INFO] {platform}正文已写入，编辑器字数: {len(actual.strip())}")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{platform}正文写入无法核验，阻止提交: {exc}") from exc


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


def _is_interactive(locator: Locator) -> bool:
    try:
        return locator.is_visible() and locator.is_enabled()
    except Exception:
        return False


def _find_scoped_confirm(
    page: Page,
    scope_selector: str,
    confirm_texts: list,
) -> Optional[Locator]:
    try:
        scopes = page.locator(scope_selector)
        for index in range(scopes.count()):
            scope = scopes.nth(index)
            if not scope.is_visible():
                continue
            confirm = find_visible_button(scope, confirm_texts)
            if confirm and _is_interactive(confirm):
                return confirm
    except Exception:
        pass
    return None


def _locator_diagnostics(page: Page, locator: Locator) -> str:
    """返回有界、只读的控件诊断信息。"""
    parts = []
    try:
        parts.append(f"text={locator.inner_text(timeout=500).strip()[:120]!r}")
    except Exception:
        parts.append("text=<unavailable>")
    for name in ("class", "disabled", "aria-disabled"):
        try:
            value = locator.get_attribute(name, timeout=500)
        except Exception:
            value = None
        if value is not None:
            parts.append(f"{name}={value!r}")
    try:
        box = locator.bounding_box(timeout=500)
    except Exception:
        box = None
    if box:
        parts.append(f"box={box!r}")

    feedback = _feedback_text(page).strip()
    if feedback:
        parts.append(f"feedback={feedback[:300]!r}")
    try:
        validation = page.locator(
            ':invalid, [aria-invalid="true"], .is-error, .has-error, '
            '.el-form-item__error, .error-message'
        )
        validation_texts = []
        for index in range(min(validation.count(), 8)):
            item = validation.nth(index)
            if item.is_visible():
                text = (item.inner_text(timeout=500) or "").strip()
                if text:
                    validation_texts.append(text)
        if validation_texts:
            parts.append(f"validation={' | '.join(validation_texts)[:300]!r}")
    except Exception:
        pass
    return ", ".join(parts)[:900]


def _raise_if_submit_failed(
    feedback: str,
    failure_markers: Optional[list],
    platform: str,
    *,
    page: Optional[Page] = None,
    locator: Optional[Locator] = None,
) -> None:
    if failure_markers and _has_feedback_phrase(feedback, failure_markers):
        diagnostics = ""
        if page is not None and locator is not None:
            diagnostics = f"; diagnostics: {_locator_diagnostics(page, locator)}"
        raise PublishClickNoEffect(
            f"{platform}提交出现明确失败反馈: {feedback.strip()[:500]}"
            f"{diagnostics}"
        )


def _wait_for_submit_effect(
    page: Page,
    publish_btn: Locator,
    pre_url: str,
    pre_feedback: str,
    confirm_texts: list,
    confirm_scope_selector: str,
    timeout_seconds: float,
    *,
    platform: str,
    allow_unscoped_confirm: bool = False,
    failure_markers: Optional[list] = None,
) -> tuple[bool, Optional[Locator]]:
    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        current_feedback = _feedback_text(page)
        _raise_if_submit_failed(
            current_feedback,
            failure_markers,
            platform,
            page=page,
            locator=publish_btn,
        )

        confirm = _find_scoped_confirm(
            page,
            confirm_scope_selector,
            confirm_texts,
        )
        if confirm is None and allow_unscoped_confirm:
            confirm = find_visible_button(page, confirm_texts)
            if confirm is not None and not _is_interactive(confirm):
                confirm = None
        if confirm:
            return True, confirm

        if (
            (page.url or "") != pre_url
            or (current_feedback and current_feedback != pre_feedback)
            or not _is_interactive(publish_btn)
        ):
            return True, None

        if time.monotonic() >= deadline:
            return False, None
        time.sleep(0.2)


def _wait_for_confirm_effect(
    page: Page,
    confirm_btn: Locator,
    pre_url: str,
    pre_feedback: str,
    timeout_seconds: float,
    *,
    platform: str,
    failure_markers: Optional[list] = None,
) -> bool:
    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        current_feedback = _feedback_text(page)
        _raise_if_submit_failed(
            current_feedback,
            failure_markers,
            platform,
            page=page,
            locator=confirm_btn,
        )
        if (
            (page.url or "") != pre_url
            or (current_feedback and current_feedback != pre_feedback)
            or not _is_interactive(confirm_btn)
        ):
            return True

        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def click_publish_with_evidence(
    page: Page,
    publish_texts: list,
    confirm_texts: list,
    platform: str,
    *,
    confirm_scope_selector: str,
    allow_unscoped_confirm: bool = False,
    failure_markers: Optional[list] = None,
    timeout_seconds: float = 10,
):
    """点击发布，并要求每次点击产生可观察页面变化。"""
    publish_btn = find_visible_button(page, publish_texts)
    if not publish_btn or not _is_interactive(publish_btn):
        raise PublishClickNoEffect(f"{platform}发布按钮不可交互")

    pre_url = page.url or ""
    pre_feedback = _feedback_text(page)
    print(f"[INFO] 点击{platform}发布按钮...")
    try:
        publish_btn.click()
    except Exception as exc:
        raise PublishClickNoEffect(f"{platform}发布按钮不可点击: {exc}") from exc

    changed, confirm_btn = _wait_for_submit_effect(
        page,
        publish_btn,
        pre_url,
        pre_feedback,
        confirm_texts,
        confirm_scope_selector,
        timeout_seconds,
        platform=platform,
        allow_unscoped_confirm=allow_unscoped_confirm,
        failure_markers=failure_markers,
    )
    if not changed:
        raise PublishClickNoEffect(
            f"{platform}发布按钮点击后页面无变化; "
            f"diagnostics: {_locator_diagnostics(page, publish_btn)}"
        )
    if confirm_btn is None:
        return

    confirm_pre_url = page.url or ""
    confirm_pre_feedback = _feedback_text(page)
    print(f"[INFO] 点击{platform}确认发布...")
    try:
        confirm_btn.click()
    except Exception as exc:
        raise PublishClickNoEffect(f"{platform}确认发布按钮不可点击: {exc}") from exc
    if not _wait_for_confirm_effect(
        page,
        confirm_btn,
        confirm_pre_url,
        confirm_pre_feedback,
        timeout_seconds,
        platform=platform,
        failure_markers=failure_markers,
    ):
        raise PublishClickNoEffect(
            f"{platform}确认发布后页面无变化; "
            f"diagnostics: {_locator_diagnostics(page, confirm_btn)}"
        )


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

    # 页面提示、按钮点击、编辑器 URL 都会残留或误报，不能单独证明落库。
    # 发布模式依次核验正式列表、草稿列表，避免把“已存草稿”误报为发布成功。
    checks = []
    if mode == "publish" and management_url:
        checks.append((management_url, "published", "published"))
    if draft_management_url:
        checks.append((draft_management_url, "draft_only", "draft_saved"))

    expected = _normalize_title(expected_title)
    for url, status, page_state in checks:
        if not expected:
            continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            continue

        # SPA 管理列表通常需要较长时间渲染，带重试的动态等待。
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
