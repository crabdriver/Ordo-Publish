import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from markdown_utils import render_markdown_html
from tiandi_engine.importers.normalize import strip_title_marker
from tiandi_engine.platforms.browser.node_runtime import resolve_node_executable


BASE_DIR = Path(__file__).resolve().parent
CDP_SCRIPT = BASE_DIR / "live_cdp.mjs"
TOUTIAO_MATCH = "mp.toutiao.com"
TOUTIAO_EDITOR_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
TOUTIAO_COVER_FILE_INPUT = "#upload-drag-input"
TOUTIAO_COVER_FILE_INPUT_SELECTORS = ('.btn-upload-handle input[type=file]', TOUTIAO_COVER_FILE_INPUT)
TOUTIAO_TITLE_SELECTORS = (
    'textarea[placeholder="请输入文章标题（2～30个字）"]',
    'textarea[placeholder*="标题"]',
    'textarea[aria-label*="标题"]',
    'input[placeholder*="标题"]',
    'textarea.semi-input-textarea',
)
TOUTIAO_EDITOR_SELECTORS = (
    '.ProseMirror',
    '[contenteditable="true"][role="textbox"]',
    '[contenteditable="true"][data-testid*="editor"]',
    '.editor-shell [contenteditable="true"]',
    '[contenteditable="true"]',
)
AI_CHECKBOX_LABEL = "引用AI"
SMOKE_STATE_PREFIX = "[SMOKE_STATE] "
PUBLISH_OPTION_MODES = ("auto", "force_on", "force_off")


def clean_title(title):
    return strip_title_marker(title)


def strip_unsupported_local_images(markdown_text):
    cleaned_lines = []
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        markdown_image = re.match(r"^!\[[^\]]*\]\(([^)]+)\)$", line)
        if markdown_image:
            image_path = markdown_image.group(1).strip()
            lower = image_path.lower()
            if lower.startswith("../") or lower.startswith("./") or lower.startswith("/") or lower.startswith("covers/"):
                continue
        cleaned_lines.append(raw_line)

    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned or markdown_text


def run_cdp(command, *args, timeout=120):
    for attempt in range(3):
        try:
            result = subprocess.run(
                [resolve_node_executable(), str(CDP_SCRIPT), command, *args],
                cwd=str(BASE_DIR),
                text=True,
                capture_output=True,
                check=True,
                timeout=timeout,
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"CDP call timed out after {timeout}s: {command} {args}")
        except subprocess.CalledProcessError as exc:
            output = "\n".join(part for part in [exc.stdout.strip(), exc.stderr.strip()] if part).strip()
            if "Broker failed to start" in output and attempt < 2:
                time.sleep(1)
                continue
            raise RuntimeError(output or f"CDP call failed: {command} {args}")


def normalize_ui_text(text):
    return "".join((text or "").split())


def _visible_picker_js(const_name, selectors):
    selectors_json = json.dumps(list(selectors), ensure_ascii=False)
    return f"""
  const {const_name} = (() => {{
    const selectors = {selectors_json};
    const isVisible = (el) => {{
      if (!el) return false;
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    }};
    return Array.from(document.querySelectorAll(selectors.join(','))).find(isVisible) || null;
  }})();
""".rstrip()


def find_toutiao_target():
    bound_target = os.environ.get("PUBLISH_TARGET_TOUTIAO")
    if bound_target:
        return bound_target
    output = run_cdp("list")
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        target_id, _title, url = parts[0], parts[1], parts[2]
        if TOUTIAO_MATCH in url:
            return target_id
    return None


def load_article(markdown_path):
    path = Path(markdown_path).expanduser().resolve()
    raw_text = path.read_text(encoding="utf-8")
    title = clean_title(path.stem)
    body = raw_text

    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = clean_title(stripped[2:].strip())
            body = raw_text.replace(line, "", 1).lstrip()
            break

    title = title[:30]
    body = strip_unsupported_local_images(body)
    html_body = render_markdown_html(body)
    return title, body, html_body, path


def wait_until(target_id, expression, timeout_seconds=20, interval_seconds=1):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = run_cdp("eval", target_id, expression)
        if result == "true":
            return True
        time.sleep(interval_seconds)
    return False


def emit_smoke_state(target_id, smoke_step, page_state, *, error=None):
    if not target_id:
        return
    try:
        output = run_cdp(
            "eval",
            target_id,
            f"""
(() => {{
{_visible_picker_js('titleEl', TOUTIAO_TITLE_SELECTORS)}
{_visible_picker_js('editor', TOUTIAO_EDITOR_SELECTORS)}
  const bodyText = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  return JSON.stringify({{
    current_url: location.href,
    has_title_input: !!titleEl,
    has_editor: !!editor,
    page_hint: bodyText.slice(0, 120)
  }});
}})()
""".strip(),
        )
        payload = json.loads(output)
    except Exception as exc:
        payload = {"current_url": "", "capture_error": str(exc)}
    payload["smoke_step"] = smoke_step
    payload["page_state"] = page_state
    if error:
        payload["error"] = str(error)
    print(f"{SMOKE_STATE_PREFIX}{json.dumps(payload, ensure_ascii=False)}")


def ensure_editor_ready(target_id):
    already_ready = wait_until(
        target_id,
        f"""
(() => {{
{_visible_picker_js('titleEl', TOUTIAO_TITLE_SELECTORS)}
{_visible_picker_js('editor', TOUTIAO_EDITOR_SELECTORS)}
  return location.href.startsWith({json.dumps(TOUTIAO_EDITOR_URL)}) && !!titleEl && !!editor;
}})()
""".strip(),
        timeout_seconds=3,
        interval_seconds=0.5,
    )
    if already_ready:
        return

    run_cdp("nav", target_id, TOUTIAO_EDITOR_URL)
    ready = wait_until(
        target_id,
        f"""
(() => {{
{_visible_picker_js('titleEl', TOUTIAO_TITLE_SELECTORS)}
{_visible_picker_js('editor', TOUTIAO_EDITOR_SELECTORS)}
  return !!titleEl && !!editor;
}})()
""".strip(),
        timeout_seconds=30,
    )
    if not ready:
        raise RuntimeError("头条号图文编辑器未就绪，请确认当前 Chrome 已登录头条号")


def inject_article(target_id, title, html_body):
    title_json = json.dumps(title, ensure_ascii=False)
    title_expression = f"""
(() => {{
{_visible_picker_js('titleEl', TOUTIAO_TITLE_SELECTORS)}
  if (!titleEl) return 'missing-title';

  const valueProto = titleEl.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const titleSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
  if (titleSetter) {{
    titleSetter.call(titleEl, {title_json});
  }} else {{
    titleEl.value = {title_json};
  }}
  titleEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
  titleEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return titleEl.value;
}})()
"""
    title_result = run_cdp("eval", target_id, title_expression)

    html_json = json.dumps(html_body, ensure_ascii=False)
    body_expression = f"""
(() => {{
{_visible_picker_js('editor', TOUTIAO_EDITOR_SELECTORS)}
  if (!editor) return 'missing-editor';
  editor.focus();
  editor.innerHTML = {html_json};
  editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
  editor.dispatchEvent(new Event('blur', {{ bubbles: true }}));
  return JSON.stringify({{
    bodyLength: (editor.innerText || '').trim().length
  }});
}})()
"""
    body_result = run_cdp("eval", target_id, body_expression)
    try:
        body_info = json.loads(body_result)
    except (json.JSONDecodeError, TypeError):
        body_info = {"bodyLength": -1, "raw": str(body_result)[:200]}
    return json.dumps({"title": title_result, **body_info}, ensure_ascii=False)


def cover_mode_is_selected(target_id, label_text="无封面"):
    """Only trust .byte-radio-inner.checked — the framework's real state."""
    label_json = json.dumps(label_text, ensure_ascii=False)
    expression = f"""
(() => {{
  const cell = Array.from(document.querySelectorAll('.pgc-edit-cell')).find(
    node => (node.innerText || '').includes('展示封面')
  );
  if (!cell) return false;
  const label = Array.from(cell.querySelectorAll('label')).find(
    node => (node.innerText || '').trim() === {label_json}
  );
  if (!label) return false;
  return !!label.querySelector('.byte-radio-inner.checked');
}})()
"""
    return run_cdp("eval", target_id, expression) == "true"


def choose_cover_mode(target_id, label_text="无封面", attempts=3):
    """Use only real mouse clicks (clickxy) — DOM clicks don't trigger the framework."""
    for attempt in range(attempts):
        if cover_mode_is_selected(target_id, label_text):
            return "already-checked"

        run_cdp(
            "eval",
            target_id,
            f"""
(() => {{
  const cell = Array.from(document.querySelectorAll('.pgc-edit-cell')).find(
    node => (node.innerText || '').includes('展示封面')
  );
  if (!cell) return 'no-cell';
  const label = Array.from(cell.querySelectorAll('label')).find(
    node => (node.innerText || '').trim() === {json.dumps(label_text, ensure_ascii=False)}
  );
  if (!label) return 'no-label';
  label.scrollIntoView({{ block: 'center', inline: 'center' }});
  return 'ok';
}})()
""".strip(),
        )

        point = get_click_center(target_id, label_text, selector=".pgc-edit-cell label .byte-radio-inner-text")
        if not point:
            point = get_click_center(target_id, label_text, selector="label")
        if not point:
            return "cover-option-not-found"
        run_cdp("clickxy", target_id, str(point["x"]), str(point["y"]))
        time.sleep(1.5)

        if cover_mode_is_selected(target_id, label_text):
            return "checked"

    return "click-no-effect"


def apply_cover(target_id, cover_path):
    """切换为「单图」后点击添加封面，再向头条 file input 注入本地文件。"""
    path = Path(cover_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"封面文件不存在: {path}")
    cover_result = choose_cover_mode(target_id, label_text="单图", attempts=5)
    if not cover_mode_is_selected(target_id, "单图"):
        raise RuntimeError(f"头条封面未切换到单图: {cover_result}")
    try:
        run_cdp("click", target_id, ".article-cover-add")
    except RuntimeError as exc:
        if ".article-cover-add" not in str(exc):
            raise
        run_cdp("click", target_id, ".article-cover-img-replace")
    time.sleep(0.7)
    upload_error = None
    for selector in TOUTIAO_COVER_FILE_INPUT_SELECTORS:
        try:
            run_cdp("setfile", target_id, selector, str(path))
            upload_error = None
            break
        except RuntimeError as exc:
            upload_error = exc
    if upload_error is not None:
        raise RuntimeError(f"头条封面未找到可用上传 input: {upload_error}") from upload_error
    uploaded = wait_for_cover_upload(target_id, timeout_seconds=15, interval_seconds=1)
    if not uploaded:
        raise RuntimeError(f"头条封面上传后未检测到预览或成功状态: {path.name}")
    confirm_result = click_visible_button(target_id, "确定")
    if confirm_result == "button-disabled":
        for _ in range(5):
            time.sleep(1)
            confirm_result = click_visible_button(target_id, "确定")
            if confirm_result != "button-disabled":
                break
    if confirm_result == "clicked":
        time.sleep(1)
        if not wait_for_cover_dialog_closed(target_id, timeout_seconds=6, interval_seconds=1):
            xy_result = click_text_by_xy(target_id, "确定")
            if xy_result == "clicked":
                time.sleep(1)
            if not wait_for_cover_dialog_closed(target_id, timeout_seconds=6, interval_seconds=1):
                raise RuntimeError("头条封面上传后确认弹窗未关闭")
    elif confirm_result not in {"button-not-found"}:
        raise RuntimeError(f"头条封面上传后确认按钮状态异常: {confirm_result}")
    print(f"[INFO] 已向头条封面控件注入文件: {path}")


def choose_required_radio(target_id, cell_title, option_text):
    cell_json = json.dumps(cell_title, ensure_ascii=False)
    option_json = json.dumps(option_text, ensure_ascii=False)
    expression = f"""
(() => {{
  const isChecked = (label) => !!(
    label?.querySelector('input:checked') ||
    label?.querySelector('.checked') ||
    label?.querySelector('.byte-radio-inner.checked')
  );
  const cell = Array.from(document.querySelectorAll('.pgc-edit-cell')).find(node => (node.innerText || '').includes({cell_json}));
  if (!cell) return 'cell-not-found';
  const label = Array.from(cell.querySelectorAll('label')).find(node => (node.innerText || '').trim() === {option_json});
  if (!label) return 'option-not-found';
  if (isChecked(label)) return 'already-checked';
  label.click();
  return isChecked(label) ? 'checked' : 'click-no-effect';
}})()
"""
    return run_cdp("eval", target_id, expression)


def click_button(target_id, button_text):
    button_json = json.dumps(button_text, ensure_ascii=False)
    expression = f"""
(() => {{
  const btn = Array.from(document.querySelectorAll('button')).find(node => (node.innerText || '').trim() === {button_json});
  if (!btn) return 'button-not-found';
  if (btn.disabled) return 'button-disabled';
  btn.click();
  return 'clicked';
}})()
"""
    return run_cdp("eval", target_id, expression)


def wait_for_publish_button_effect(target_id, button_text, timeout_seconds=3):
    if button_text not in {"预览并发布", "确认发布"}:
        return True
    expression = """
(() => {
  const content = document.body.innerText || '';
  return content.includes('确认发布')
    || content.includes('发布成功')
    || content.includes('查看作品')
    || location.href.includes('/graphic/articles')
    || !location.href.includes('/graphic/publish');
})()
""".strip()
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=1)


def click_button_with_fallback(target_id, button_text):
    result = click_button(target_id, button_text)
    if result == "clicked":
        if wait_for_publish_button_effect(target_id, button_text, timeout_seconds=3):
            return result
        xy_result = click_text_by_xy(target_id, button_text)
        return "clicked" if xy_result == "clicked" else result
    if result == "button-disabled":
        return result
    xy_result = click_text_by_xy(target_id, button_text)
    return "clicked" if xy_result == "clicked" else result


def click_visible_button(target_id, button_text, class_keyword=None):
    button_json = json.dumps(button_text, ensure_ascii=False)
    class_json = json.dumps(class_keyword or "", ensure_ascii=False)
    expression = f"""
(() => {{
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  }};
  const btn = Array.from(document.querySelectorAll('button')).find(el => {{
    const text = (el.innerText || '').trim();
    const className = el.className || '';
    return text === {button_json} && isVisible(el) && (!{class_json} || className.includes({class_json}));
  }});
  if (!btn) return 'button-not-found';
  if (btn.disabled) return 'button-disabled';
  btn.scrollIntoView({{ block: 'center', inline: 'center' }});
  btn.click();
  return 'clicked';
}})()
"""
    return run_cdp("eval", target_id, expression.strip())


def get_click_center(target_id, text, selector="button"):
    text_json = json.dumps(text, ensure_ascii=False)
    selector_json = json.dumps(selector, ensure_ascii=False)
    expression = f"""
(() => {{
  const nodes = Array.from(document.querySelectorAll({selector_json}));
  const target = nodes.find(node => (node.innerText || '').replace(/\\s+/g, '') === {text_json}.replace(/\\s+/g, ''));
  if (!target) return '';
  const rect = target.getBoundingClientRect();
  if (!rect.width || !rect.height) return '';
  return JSON.stringify({{
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2
  }});
}})()
"""
    output = run_cdp("eval", target_id, expression)
    if not output:
        return None
    return json.loads(output)


def click_text_by_xy(target_id, text, selector="button"):
    point = get_click_center(target_id, text, selector=selector)
    if not point:
        return "target-not-found"
    run_cdp("clickxy", target_id, str(point["x"]), str(point["y"]))
    return "clicked"


def wait_for_text(target_id, text, timeout_seconds=20, interval_seconds=1):
    text_json = json.dumps(text, ensure_ascii=False)
    expression = f"(() => (document.body.innerText || '').includes({text_json}))()"
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def wait_for_any_text(target_id, texts, timeout_seconds=20, interval_seconds=1):
    texts_json = json.dumps(texts, ensure_ascii=False)
    expression = f"""
(() => {{
  const content = document.body.innerText || '';
  return {texts_json}.some(text => content.includes(text));
}})()
"""
    return wait_until(target_id, expression.strip(), timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def select_byte_option(target_id, trigger_selector, option_text):
    selector_json = json.dumps(trigger_selector, ensure_ascii=False)
    trigger_result = run_cdp(
        "eval",
        target_id,
        f"""
(() => {{
  const trigger = document.querySelector({selector_json});
  if (!trigger) return 'trigger-not-found';
  trigger.click();
  return 'clicked';
}})()
""".strip(),
    )
    if trigger_result != "clicked":
        raise RuntimeError(f"头条号下拉触发失败: {trigger_selector} ({trigger_result})")
    time.sleep(0.5)
    option_json = json.dumps(str(option_text), ensure_ascii=False)
    result = run_cdp(
        "eval",
        target_id,
        f"""
(() => {{
  const normalize = (text) => (text || '').replace(/\\s+/g, '');
  const trigger = document.querySelector({selector_json});
  if (!trigger) return 'trigger-not-found';
  const current = trigger.querySelector('.byte-select-view-value');
  if (current && normalize(current.innerText || current.textContent || '') === normalize({option_json})) return 'already-selected';
  const option = Array.from(document.querySelectorAll('li.byte-select-option, .byte-select-option, [role="option"]')).find(
    el => normalize(el.innerText || el.textContent || '') === normalize({option_json})
  );
  if (!option) return 'option-not-found';
  option.click();
  return 'clicked';
}})()
""".strip(),
    )
    if result not in {"clicked", "already-selected"}:
        raise RuntimeError(f"头条号下拉选择失败: {trigger_selector} -> {option_text} ({result})")
    return result


def wait_for_cover_upload(target_id, timeout_seconds=15, interval_seconds=1):
    expression = """
(() => {
  const root = document.querySelector('.upload-image-panel, .cover-component, .article-cover, .article-cover-wrap, .article-cover-list') || document.body;
  const pageText = (document.body.innerText || '').replace(/\\s+/g, '');
  const hasPreview = !!root.querySelector(
    'img[src^="http"], .article-cover-item img, .article-cover-preview img, .byte-upload-list-item img, .upload-list-item img, [style*="background-image"]'
  );
  const bodyText = ((root.innerText || '') + pageText).replace(/\\s+/g, '');
  const hasSuccessText = [
    '更换封面',
    '重新上传',
    '裁剪',
    '裁切',
    '封面管理',
    '删除',
    '已上传',
    '支持拖拽调整图片顺序'
  ].some(text => bodyText.includes(text));
  return hasPreview || hasSuccessText;
})()
""".strip()
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def wait_for_cover_dialog_closed(target_id, timeout_seconds=10, interval_seconds=1):
    expression = """
(() => {
  const content = (document.body.innerText || '').replace(/\\s+/g, '');
  return !content.includes('已上传1张图片')
    && !content.includes('支持拖拽调整图片顺序')
    && !content.includes('本地上传')
    && !content.includes('扫码上传');
})()
""".strip()
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def wait_for_draft_saved(target_id, timeout_seconds=12):
    signals = [
        "草稿已保存",
        "草稿将自动保存",
        "草稿保存中",
        "已保存到草稿",
    ]
    return wait_for_any_text(target_id, signals, timeout_seconds=timeout_seconds, interval_seconds=1)


def normalize_scheduled_publish_at(value):
    raw = str(value or "").strip()
    if not raw:
        raise RuntimeError("头条号定时发布缺少 scheduled_publish_at")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError("头条号定时发布时间格式无效，应为 ISO 本地时间，例如 2026-03-30T09:30") from exc
    return parsed.strftime("%Y-%m-%dT%H:%M")


def schedule_publish(target_id, scheduled_publish_at):
    normalized = normalize_scheduled_publish_at(scheduled_publish_at)
    date_value, time_value = normalized.split("T", 1)
    schedule_dt = datetime.fromisoformat(normalized)

    schedule_controls_ready = run_cdp(
        "eval",
        target_id,
        """
(() => !!document.querySelector('.day-select, .hour-select, .minute-select'))()
""".strip(),
    )
    if schedule_controls_ready != "true":
        mode_result = run_cdp(
        "eval",
        target_id,
        """
(() => {
  const normalize = (text) => (text || '').replace(/\s+/g, '');
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const options = ['定时发布', '预约发布'];
  const nodes = Array.from(document.querySelectorAll('label, button, [role="radio"], .byte-radio, .radio, span, div'));
  const node = nodes.find((el) => visible(el) && options.includes(normalize(el.innerText || el.textContent || '')));
  if (!node) return 'schedule-option-not-found';
  const clickable = node.closest('label,button,[role="radio"],.byte-radio,.radio') || node;
  clickable.click();
  return 'clicked';
})()
""".strip(),
        )
        if mode_result not in {"clicked", "already-selected"}:
            raise RuntimeError(f"头条号未找到定时发布入口: {mode_result}")

        time.sleep(1)
    schedule_result = run_cdp(
        "eval",
        target_id,
        f"""
(() => {{
  const datetimeValue = {json.dumps(normalized)};
  const dateValue = {json.dumps(date_value)};
  const timeValue = {json.dumps(time_value)};
  const visible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  }};
  const setValue = (input, value) => {{
    const setter = Object.getOwnPropertyDescriptor(input.__proto__, 'value')?.set;
    if (setter) {{
      setter.call(input, value);
    }} else {{
      input.value = value;
    }}
    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
    input.dispatchEvent(new Event('blur', {{ bubbles: true }}));
  }};
  const inputs = Array.from(document.querySelectorAll('input')).filter(visible);
  const touched = [];
  for (const input of inputs) {{
    const type = (input.type || '').toLowerCase();
    const placeholder = input.placeholder || '';
    if (type === 'datetime-local') {{
      setValue(input, datetimeValue);
      touched.push(type || 'datetime-local');
      continue;
    }}
    if (type === 'date' || /日期|年月日|选择日期/.test(placeholder)) {{
      setValue(input, dateValue);
      touched.push(type || 'date');
      continue;
    }}
    if (type === 'time' || /时间|时刻|选择时间/.test(placeholder)) {{
      setValue(input, timeValue);
      touched.push(type || 'time');
      continue;
    }}
  }}
  return JSON.stringify({{ count: touched.length, touched }});
}})()
""".strip(),
    )
    info = json.loads(schedule_result or "{}")
    if int(info.get("count") or 0) <= 0:
        day_candidates = [schedule_dt.strftime("%m月%d日"), f"{schedule_dt.month:02d}月{schedule_dt.day:02d}日", f"{schedule_dt.month}月{schedule_dt.day}日"]
        day_error = None
        for day_text in day_candidates:
            try:
                select_byte_option(target_id, ".day-select", day_text)
                day_error = None
                break
            except RuntimeError as exc:
                day_error = exc
        if day_error is not None:
            raise RuntimeError(f"头条号定时发布未找到日期选项: {day_error}") from day_error
        select_byte_option(target_id, ".hour-select", str(schedule_dt.hour))
        select_byte_option(target_id, ".minute-select", str(schedule_dt.minute))
        preview_ready = wait_until(
            target_id,
            f"(() => (document.body.innerText || '').includes({json.dumps(schedule_dt.strftime('%Y-%m-%d %H:%M'))}))()",
            timeout_seconds=5,
            interval_seconds=1,
        )
        if not preview_ready:
            raise RuntimeError("头条号定时发布时间未同步到预览文案")

    for button_text in ("预览并定时发布", "确认发布", "确认预约", "预约发布", "定时发布"):
        confirm_result = click_visible_button(target_id, button_text)
        if confirm_result == "button-disabled":
            time.sleep(1)
            confirm_result = click_visible_button(target_id, button_text)
        if confirm_result == "clicked":
            return {"scheduled_publish_at": normalized, "confirm_text": button_text}
        if confirm_result not in {"button-not-found", "button-disabled"}:
            raise RuntimeError(f"头条号定时发布确认按钮异常: {confirm_result}")
    raise RuntimeError("头条号未找到定时发布确认按钮")


def wait_for_scheduled_publish(target_id, timeout_seconds=30):
    signals = [
        "已设置定时发布",
        "预约发布成功",
        "定时发布成功",
        "已预约发布",
        "查看作品",
    ]
    return wait_for_any_text(target_id, signals, timeout_seconds=timeout_seconds, interval_seconds=1)


def attempt_ai_declaration(target_id):
    """在「作品声明」区域精确勾选「引用AI」并做结果校验。"""
    expand_expression = """
(() => {
  if ((document.body.innerText || '').includes('作品声明')) return 'already-open';
  const el = document.querySelector('.footer-back-content');
  if (el && (el.innerText || '').includes('发文设置')) {
    el.click();
    return 'expanded';
  }
  return 'skip';
})()
""".strip()
    expand_result = run_cdp("eval", target_id, expand_expression)
    if expand_result == "expanded":
        time.sleep(1)
    elif expand_result not in {"already-open", "skip"}:
        raise RuntimeError(f"头条作品声明区域展开失败: {expand_result}")

    label_json = json.dumps(AI_CHECKBOX_LABEL, ensure_ascii=False)
    check_expression = f"""
(() => {{
  const normalize = (text) => (text || '').replace(/\s+/g, '');
  const labels = Array.from(document.querySelectorAll('.byte-checkbox-group .byte-checkbox, label, .byte-checkbox'));
  const lb = labels.find(node => normalize(node.innerText || node.textContent || '') === normalize({label_json}));
  if (!lb) return JSON.stringify({{found: false}});
  const input = lb.querySelector('input[type=checkbox]');
  const isChecked = () => !!(
    input?.checked ||
    lb.classList.contains('checked') ||
    lb.querySelector('.byte-checkbox-input-checked, .checked')
  );
  if (isChecked()) {{
    return JSON.stringify({{found: true, checked: true, already: true}});
  }}
  lb.click();
  return JSON.stringify({{found: true, checked: isChecked(), already: false}});
}})()
""".strip()
    raw = run_cdp("eval", target_id, check_expression)
    if not raw:
        raise RuntimeError("头条 AI 创作声明未返回校验结果")
    result = json.loads(raw)

    if not result.get("found"):
        raise RuntimeError('头条作品声明中未找到「引用AI」选项')

    if result.get("checked"):
        if result.get("already"):
            print('[INFO] AI创作声明: 「引用AI」已勾选')
        else:
            print('[INFO] AI创作声明: 已成功勾选「引用AI」')
    else:
        raise RuntimeError('头条作品声明中「引用AI」勾选失败')
    return result


def detect_publish_limit(target_id):
    output = run_cdp("eval", target_id, "(() => document.body.innerText || '')()")
    markers = [
        "今日发文已达",
        "今日发文已达 50 篇上限",
        "达到发布上限",
        "发布上限",
        "未来7天",
        "最多支持发布未来7天的文章",
        "请明天再来",
        "时间限制",
        "排期",
    ]
    for marker in markers:
        if marker in output:
            return marker
    return None


def main():
    parser = argparse.ArgumentParser(description="Publish Markdown article to Toutiao using live Chrome.")
    parser.add_argument("markdown_file", help="Markdown article path")
    parser.add_argument(
        "--mode",
        choices=["draft", "publish"],
        default="draft",
        help="draft 等待自动存草稿；publish 直接发布",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help="可选主题标识（编排层预留，当前发布流程可不使用）。",
    )
    parser.add_argument(
        "--cover",
        default=None,
        metavar="PATH",
        help="可选封面图路径（编排层预留，当前发布流程可不使用）。",
    )
    parser.add_argument(
        "--template-mode",
        dest="template_mode",
        default=None,
        help="可选模板模式（编排层预留，当前发布流程可不使用）。",
    )
    parser.add_argument(
        "--article-id",
        dest="article_id",
        default=None,
        help="可选文章标识（编排层预留，当前发布流程可不使用）。",
    )
    parser.add_argument(
        "--cover-mode",
        choices=PUBLISH_OPTION_MODES,
        default="auto",
        help="任务级封面策略：auto / force_on / force_off",
    )
    parser.add_argument(
        "--ai-declaration-mode",
        dest="ai_declaration_mode",
        choices=PUBLISH_OPTION_MODES,
        default="auto",
        help="任务级 AI 声明策略：auto / force_on / force_off",
    )
    parser.add_argument(
        "--scheduled-publish-at",
        dest="scheduled_publish_at",
        default=None,
        help="仅头条号 publish 模式使用的定时发布时间，格式如 2026-03-30T09:30",
    )
    args = parser.parse_args()
    _ = (args.theme, args.template_mode, args.article_id)

    title, _body, html_body, article_path = load_article(args.markdown_file)
    target_id = None
    smoke_step = "find_target"
    page_state = "starting"

    try:
        target_id = find_toutiao_target()
        if not target_id:
            raise RuntimeError("没有找到头条号标签页，请先在当前 Chrome 中打开并登录头条号")

        smoke_step = "ensure_editor_ready"
        ensure_editor_ready(target_id)
        page_state = "editor_ready"

        smoke_step = "inject_article"
        inject_result = inject_article(target_id, title, html_body)
        page_state = "article_injected"
        print(f"[INFO] 已写入头条号编辑器: {inject_result}")

        smoke_step = "apply_cover"
        if args.cover_mode == "force_on" and not args.cover:
            raise RuntimeError("头条号已要求启用封面，但当前任务没有可用封面路径")
        if args.cover_mode != "force_off" and args.cover:
            apply_cover(target_id, args.cover)
            page_state = "cover_ready"
        else:
            cover_result = choose_cover_mode(target_id, label_text="无封面")
            if not cover_mode_is_selected(target_id, "无封面"):
                raise RuntimeError(f"头条封面未切换到无封面: {cover_result}")
            print(f"[INFO] 已切换头条封面为无封面: {cover_result}")

        smoke_step = "ad_selection"
        ad_result = choose_required_radio(target_id, "投放广告", "投放广告赚收益")
        if ad_result == "click-no-effect":
            ad_result = click_text_by_xy(target_id, "投放广告赚收益", selector="label")
        print(f"[INFO] 已尝试设置头条广告选项: {ad_result}")

        if args.ai_declaration_mode != "force_off":
            smoke_step = "attempt_ai_declaration"
            attempt_ai_declaration(target_id)
            page_state = "ai_declared"
        else:
            print("[INFO] 已显式关闭头条号 AI 声明设置")

        if args.mode == "draft":
            smoke_step = "draft_saved"
            if not wait_for_draft_saved(target_id, timeout_seconds=12):
                raise RuntimeError("未检测到头条号草稿提示")
            page_state = "draft_saved"
            emit_smoke_state(target_id, smoke_step, page_state)
            print(f"[OK] 已写入头条草稿页: {article_path}")
            return

        smoke_step = "publish_click"
        publish_button_text = "定时发布" if args.scheduled_publish_at else "预览并发布"
        publish_result = click_button_with_fallback(target_id, publish_button_text)
        if publish_result != "clicked":
            raise RuntimeError(f"点击头条发布失败: {publish_result}")

        if args.scheduled_publish_at:
            smoke_step = "schedule_publish"
            schedule_publish(target_id, args.scheduled_publish_at)
            limit_marker = detect_publish_limit(target_id)
            if limit_marker:
                raise RuntimeError(f"头条号定时发布受限: {limit_marker}")
            smoke_step = "scheduled"
            if not wait_for_scheduled_publish(target_id, timeout_seconds=30):
                limit_marker = detect_publish_limit(target_id)
                if limit_marker:
                    raise RuntimeError(f"头条号定时发布受限: {limit_marker}")
                raise RuntimeError("未检测到头条号定时发布成功提示，请检查页面状态")
            page_state = "scheduled"
            emit_smoke_state(target_id, smoke_step, page_state)
            print(f"[OK] 已设置头条号定时发布: {article_path}")
            return

        if wait_until(
            target_id,
            "(() => Array.from(document.querySelectorAll('button')).some(btn => (btn.innerText || '').replace(/\\s+/g, '') === '确认发布'))()",
            timeout_seconds=8,
        ):
            smoke_step = "publish_confirm"
            confirm_result = click_button_with_fallback(target_id, "确认发布")
            if confirm_result != "clicked":
                raise RuntimeError(f"点击头条确认发布失败: {confirm_result}")

        limit_marker = detect_publish_limit(target_id)
        if limit_marker:
            raise RuntimeError(f"头条号发布受限: {limit_marker}")

        smoke_step = "published"
        if not wait_until(
            target_id,
            "(() => (document.body.innerText || '').includes('发布成功') || (document.body.innerText || '').includes('查看作品') || location.href.includes('/graphic/articles') || !location.href.includes('/graphic/publish'))()",
            timeout_seconds=30,
        ):
            limit_marker = detect_publish_limit(target_id)
            if limit_marker:
                raise RuntimeError(f"头条号发布受限: {limit_marker}")
            raise RuntimeError("未检测到头条号发布成功提示，请检查页面状态")

        page_state = "published"
        emit_smoke_state(target_id, smoke_step, page_state)
        print(f"[OK] 已发布到头条号: {article_path}")
    except Exception as exc:
        emit_smoke_state(target_id, smoke_step, page_state, error=exc)
        raise


if __name__ == "__main__":
    main()
