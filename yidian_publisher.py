import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from markdown_utils import render_markdown_html, should_declare_ai
from ordo_engine.importers.normalize import strip_title_marker
from ordo_engine.platforms.browser.node_runtime import resolve_node_executable


BASE_DIR = Path(__file__).resolve().parent
CDP_SCRIPT = BASE_DIR / "live_cdp.mjs"
YIDIAN_MATCH = "mp.yidianzixun.com"
YIDIAN_EDITOR_URL = "https://mp.yidianzixun.com/#/Writing/articleEditor"
YIDIAN_COVER_FILE_INPUT = "input.upload-input"
YIDIAN_SINGLE_COVER_TEXT = "单图"
YIDIAN_PERSONAL_OPINION = "个人观点，仅供参考"
YIDIAN_AI_DECLARATION = "内容由AI生成"
AI_KEYWORDS = ["AI创作", "AI辅助", "AIGC", "人工智能生成", "AI生成", "AI工具", "使用AI"]
SMOKE_STATE_PREFIX = "[SMOKE_STATE] "
PUBLISH_OPTION_MODES = ("auto", "force_on", "force_off", "random")


def clean_title(title):
    return strip_title_marker(title)


def run_cdp(command, *args, timeout=120):
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
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"CDP call timed out after {timeout}s: {command} {args}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"CDP call failed ({command}): exit={exc.returncode}, stderr={exc.stderr[:500]}") from exc


def normalize_ui_text(text):
    return "".join((text or "").split())


def list_yidian_targets():
    output = run_cdp("list")
    targets = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        target_id, _title, url = parts[0], parts[1], parts[2]
        if YIDIAN_MATCH in url:
            targets.append(target_id)
    return targets


def editor_ready(target_id, timeout_seconds=3):
    return wait_until(
        target_id,
        "(() => !!document.querySelector(\"input.post-title\") && !!document.querySelector(\".editor-content[contenteditable='true']\"))()",
        timeout_seconds=timeout_seconds,
        interval_seconds=1,
    )


def open_fresh_editor_tab(target_id):
    before_targets = set(list_yidian_targets())
    result = run_cdp(
        "eval",
        target_id,
        f"window.open({json.dumps(YIDIAN_EDITOR_URL, ensure_ascii=False)}, '_blank'); 'opened'",
    )
    if result != "opened":
        return None

    deadline = time.time() + 12
    while time.time() < deadline:
        current_targets = list_yidian_targets()
        for candidate in current_targets:
            if candidate not in before_targets and editor_ready(candidate, timeout_seconds=2):
                return candidate
        time.sleep(1)
    return None


def find_yidian_target():
    bound_target = os.environ.get("PUBLISH_TARGET_YIDIAN")
    if bound_target and editor_ready(bound_target, timeout_seconds=1):
        return bound_target

    targets = list_yidian_targets()
    for target_id in targets:
        if target_id == bound_target:
            continue
        if editor_ready(target_id, timeout_seconds=1):
            return target_id

    return bound_target or (targets[0] if targets else None)


def strip_unsupported_local_images(markdown_text):
    cleaned_lines = []
    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        markdown_image = re.match(r"^!\[[^\]]*\]\(([^)]+)\)$", line)
        if markdown_image:
            image_path = markdown_image.group(1).strip()
            lower = image_path.lower()
            if not (lower.startswith("http://") or lower.startswith("https://") or lower.startswith("data:image/")):
                continue
        cleaned_lines.append(raw_line)

    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned or markdown_text


def load_article(markdown_path):
    path = Path(markdown_path).expanduser().resolve()
    raw_text = path.read_text(encoding="utf-8")
    title = clean_title(path.stem)
    body = raw_text

    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") or (stripped.startswith("## ") and not stripped.startswith("###")):
            title = clean_title(stripped.lstrip("#").strip())
            body = raw_text.replace(line, "", 1).lstrip()
            break

    title = title[:64]
    body = strip_unsupported_local_images(body)
    html = render_markdown_html(body)
    return title, body, html, path


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
            """
(() => {
  const titleEl = document.querySelector("input.post-title");
  const editor = document.querySelector(".editor-content[contenteditable='true']");
  const bodyText = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  return JSON.stringify({
    current_url: location.href,
    has_title_input: !!titleEl,
    has_editor: !!editor,
    page_hint: bodyText.slice(0, 120)
  });
})()
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


def take_screenshot(target_id, step):
    if not target_id:
        return
    try:
        timestamp = int(time.time() * 1000)
        screenshot_dir = BASE_DIR / ".ordo" / "screenshots" / "yidian"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        filepath = screenshot_dir / f"{timestamp}_{step}.png"
        run_cdp("shot", target_id, str(filepath))
        print(f"[INFO] [SCREENSHOT] Saved to: {filepath}")
    except Exception as e:
        print(f"[WARN] 截图失败 ({step}): {e}")


def verify_in_management_list(target_id, expected_title, is_draft=False):
    if is_draft:
        management_url = "https://mp.yidianzixun.com/#/ArticleManual/original/draft"
    else:
        management_url = "https://mp.yidianzixun.com/#/ArticleManual/original/review"
    print(f"[INFO] 正在跳转至一点号内容管理页面进行校验: {management_url}")
    run_cdp("nav", target_id, management_url)
    time.sleep(2)

    if is_draft:
        click_draft_tab_expr = """
        (() => {
            const tabs = Array.from(document.querySelectorAll('div, span, li, a, p'));
            const draftTab = tabs.find(el => {
                const txt = (el.innerText || el.textContent || '').trim();
                return (txt === '草稿' || txt === '草稿箱') && el.offsetHeight > 0;
            });
            if (draftTab) {
                draftTab.click();
                return 'clicked';
            }
            return 'not_found';
        })()
        """
        res = run_cdp("eval", target_id, click_draft_tab_expr)
        print(f"[INFO] 尝试点击一点号「草稿箱」标签结果: {res}")
        time.sleep(1.5)

    title_json = json.dumps(expected_title, ensure_ascii=False)
    list_ready_expr = """
(() => {
  const normalize = (t) => (t || '').replace(/\\s+/g, '');
  const target = normalize(""" + title_json + """);
  const els = Array.from(document.querySelectorAll('.card-title, .title, a, div, span, h1, h2, h3, h4'));
  const found = els.find(el => {
    const txt = normalize(el.innerText || el.textContent || '');
    return txt.includes(target) && el.offsetHeight > 0;
  });
  return !!found;
})()
"""
    print(f"[INFO] 正在校验列表是否包含文章标题：《{expected_title}》...")
    success = wait_until(target_id, list_ready_expr, timeout_seconds=15, interval_seconds=1)
    if not success:
        print("[WARN] 一点号内容管理列表未就绪，刷新后重试校验")
        run_cdp("eval", target_id, "location.reload(); 'reloading'")
        time.sleep(6)
        if is_draft:
            run_cdp("eval", target_id, click_draft_tab_expr)
            time.sleep(1.5)
        success = wait_until(target_id, list_ready_expr, timeout_seconds=15, interval_seconds=1)
    take_screenshot(target_id, "verified_list")
    if not success:
        raise RuntimeError(f"双重校验失败：未能在一点号稿件列表中找到文章《{expected_title}》")
    print(f"[INFO] 双重校验成功：在稿件列表中成功检索到文章《{expected_title}》！")


def scroll_settings_into_view(target_id):
    expression = """
(() => {
  const el = document.querySelector('.article-setting');
  if (el) {
    el.scrollIntoView({ block: 'start' });
    return 'scrolled';
  }
  return 'not_found';
})()
"""
    res = run_cdp("eval", target_id, expression)
    print(f"[INFO] 已将一点号文章设置面板滚动至可视区域: {res}")
    time.sleep(1)


def ensure_editor_ready(target_id):
    run_cdp("nav", target_id, YIDIAN_EDITOR_URL)
    # 强制执行一次 reload 以确保打破 Yidian SPA 路由锁，强制重新渲染编辑器
    run_cdp("eval", target_id, "location.reload()")
    time.sleep(2)

    if wait_for_button(target_id, "再写一篇", timeout_seconds=3, interval_seconds=1):
        action = click_action(target_id, "再写一篇")
        if action != "clicked":
            raise RuntimeError(f"一点号返回编辑器失败: {action}")
    ready = editor_ready(target_id, timeout_seconds=10)
    if ready:
        return target_id

    # 一点号偶尔停在“内容管理/审核中”视图，虽然 URL 还是编辑页，但需要手动点一次“发布/发文章”才能回到编辑器。
    reopen_result = run_cdp(
        "eval",
        target_id,
        """
(() => {
  const link = document.querySelector('a.editor')
    || Array.from(document.querySelectorAll('a')).find((el) => {
      const text = (el.innerText || '').trim();
      const href = el.getAttribute('href') || '';
      return text === '发文章' || text === '发布' || href === '#/Writing/articleEditor';
    });
  if (!link) return 'entry-not-found';
  link.click();
  return 'clicked';
})()
""".strip(),
    )
    if reopen_result != "clicked":
        raise RuntimeError(f"一点号无法切回编辑器: {reopen_result}")

    ready = editor_ready(target_id, timeout_seconds=10)
    if not ready:
        for candidate in list_yidian_targets():
            if candidate != target_id and editor_ready(candidate, timeout_seconds=2):
                return candidate
        fresh_target = open_fresh_editor_tab(target_id)
        if fresh_target:
            return fresh_target
        raise RuntimeError("一点号编辑器未就绪，请确认当前标签已登录并可进入发文页")
    return target_id


def inject_article(target_id, title, html):
    title_json = json.dumps(title, ensure_ascii=False)
    html_json = json.dumps(html, ensure_ascii=False)
    expression = f"""
(() => {{
  const title = {title_json};
  const html = {html_json};
  const titleInput = document.querySelector("input.post-title");
  const editor = document.querySelector(".editor-content[contenteditable='true']");
  if (!titleInput || !editor) {{
    return "missing-editor";
  }}

  const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
  if (inputSetter) {{
    inputSetter.call(titleInput, title);
  }} else {{
    titleInput.value = title;
  }}
  titleInput.dispatchEvent(new Event("input", {{ bubbles: true }}));
  titleInput.dispatchEvent(new Event("change", {{ bubbles: true }}));

  editor.focus();
  editor.innerHTML = html;
  editor.dispatchEvent(new InputEvent("input", {{ bubbles: true, inputType: "insertFromPaste" }}));
  editor.dispatchEvent(new Event("keyup", {{ bubbles: true }}));
  editor.dispatchEvent(new Event("blur", {{ bubbles: true }}));

  return JSON.stringify({{
    title: titleInput.value,
    bodyLength: (editor.innerText || "").trim().length
  }});
}})()
"""
    return run_cdp("eval", target_id, expression)


def click_action(target_id, button_text):
    button_json = json.dumps(button_text, ensure_ascii=False)
    expression = f"""
(() => {{
  const text = {button_json};
  const buttons = Array.from(document.querySelectorAll("button"));
  let button = buttons.find((btn) => btn.innerText.trim() === text && btn.classList.contains("mp-btn-large-article"));
  if (!button) {{
    button = buttons.find((btn) => btn.innerText.trim() === text);
  }}
  if (!button) {{
    return "button-not-found";
  }}
  if (button.disabled) {{
    return "button-disabled";
  }}
  button.scrollIntoView({{ block: "center", inline: "center" }});
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {{
    button.dispatchEvent(new MouseEvent(type, {{ bubbles: true, cancelable: true, view: window }}));
  }}
  return "clicked";
}})()
"""
    return run_cdp("eval", target_id, expression)


def set_latest_cover(target_id):
    expression = """
(() => {
  const button = document.querySelector('.pre-img-item .setting-btn');
  if (button) {
    button.click();
    return 'clicked';
  }
  const el = document.querySelector('.cover-crop-container');
  if (!el || !el.__vue__) return 'missing';
  const vue = el.__vue__;
  const latestImg = vue.images[vue.images.length - 1];
  if (!latestImg) return 'no_img';
  vue.setCurrentCover(latestImg);
  return 'called';
})()
"""
    return run_cdp("eval", target_id, expression)


def apply_cover(target_id, cover_path):
    import time
    path = Path(cover_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"封面文件不存在: {path}")
    mode_result = select_cover_type(target_id, YIDIAN_SINGLE_COVER_TEXT)
    if not wait_for_cover_type(target_id, YIDIAN_SINGLE_COVER_TEXT, timeout_seconds=8):
        raise RuntimeError(f"一点号封面未切换到单图: {mode_result}")

    # 1. 直接将文件注入到 input.upload-input 控件中
    run_cdp("setfile", target_id, YIDIAN_COVER_FILE_INPUT, str(path))
    time.sleep(0.5)

    # 2. 直接调用一点号封面组件绑定的 Vue 方法 uploadLocalImg 进行上传
    trigger_upload_expr = """
(() => {
  const el = document.querySelector('.cover-crop-container');
  const input = document.querySelector('input.upload-input');
  if (el && el.__vue__ && input && input.files.length > 0) {
    el.__vue__.uploadLocalImg(input.files);
    return 'triggered';
  }
  return 'missing';
})()
"""
    triggered = run_cdp("eval", target_id, trigger_upload_expr)
    if triggered != "triggered":
        raise RuntimeError(f"无法触发一点号 Vue 封面上传函数: {triggered}")

    # 3. 轮询等待后台上传完成（监听 Vue 内部的 m_isUploading 状态并验证 images 集合不为空）
    print("[INFO] 正在上传封面图片至一点号...")
    time.sleep(1.0)  # 预留上传开始与状态变更时间
    upload_success = False
    for _ in range(40):
        state_expr = """
(() => {
  const el = document.querySelector('.cover-crop-container');
  if (!el || !el.__vue__) return JSON.stringify({status: 'missing'});
  const vue = el.__vue__;
  return JSON.stringify({
    isUploading: vue.m_isUploading,
    imagesCount: vue.images.length
  });
})()
"""
        res = json.loads(run_cdp("eval", target_id, state_expr))
        if res.get("status") == "missing":
            time.sleep(0.5)
            continue
        if not res.get("isUploading") and res.get("imagesCount", 0) > 0:
            upload_success = True
            break
        time.sleep(0.5)

    if not upload_success:
        raise RuntimeError("一点号封面上传超时，未能成功上传封面图")

    # 5. 一点号上传后只把图片放入 images，必须显式设置为当前封面。
    set_res = set_latest_cover(target_id)
    if set_res not in {"clicked", "called"}:
        raise RuntimeError(f"无法调用一点号 Vue 封面设置函数: {set_res}")

    print("[INFO] 等待一点号封面设置同步...")
    cover_synced = False
    for _ in range(20):
        time.sleep(0.5)
        cover_state_expr = """
(() => {
  const el = document.querySelector('.cover-crop-container');
  if (!el || !el.__vue__) return JSON.stringify({error: 'missing'});
  const vue = el.__vue__;
  if (vue.singleCover && vue.singleCover.url) {
    return JSON.stringify({status: 'done', url: vue.singleCover.url});
  }
  return JSON.stringify({status: 'waiting'});
})()
"""
        state = json.loads(run_cdp("eval", target_id, cover_state_expr))
        if state.get("status") == "done":
            cover_synced = True
            break

    if not cover_synced:
        raise RuntimeError("一点号封面设置超时，未能成功设置封面图")

    print(f"[INFO] 已成功上传并设置一点号封面图: {path}")


def select_cover_type(target_id, option_text):
    option_json = json.dumps(option_text, ensure_ascii=False)
    expression = f"""
(() => {{
  const items = Array.from(document.querySelectorAll('.cover-type .item'));
  const targetItem = items.find((item) => (item.innerText || '').replace(/\\s+/g, '') === {option_json}.replace(/\\s+/g, ''));
  if (!targetItem) {{
    return 'cover-option-not-found';
  }}
  targetItem.click();
  return Array.from(document.querySelectorAll('.cover-type .item')).map((item) => ({{
    text: item.innerText.trim(),
    checked: item.classList.contains('checked')
  }}));
}})()
"""
    return run_cdp("eval", target_id, expression)


def wait_for_cover_type(target_id, option_text, timeout_seconds=10, interval_seconds=1):
    option_json = json.dumps(option_text, ensure_ascii=False)
    expression = f"""
(() => {{
  const items = Array.from(document.querySelectorAll('.cover-type .item'));
  const targetItem = items.find((item) => (item.innerText || '').replace(/\\s+/g, '') === {option_json}.replace(/\\s+/g, ''));
  return !!targetItem && targetItem.classList.contains('checked');
}})()
"""
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def select_default_cover(target_id):
    return select_cover_type(target_id, "默认")


def wait_for_default_cover(target_id, timeout_seconds=10, interval_seconds=1):
    return wait_for_cover_type(target_id, "默认", timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def wait_for_cover_upload(target_id, timeout_seconds=10, interval_seconds=1):
    expression = """
(() => {
  const root = document.querySelector('.cover-content, .cover-wrap, .cover-box, .cover-type, .article-setting') || document.body;
  const imgs = Array.from(root.querySelectorAll('img'));
  const hasRealImg = imgs.some(img => img.src && !img.src.startsWith('data:image/gif;base64'));
  const hasPreview = hasRealImg || !!root.querySelector(
    '.cover-preview img, .preview img, .upload-list img, .cover-box img, [style*="background-image"]'
  );
  const hasCoverItems = Array.from(root.querySelectorAll('.cover-item.draggable, .cover-item')).some(
    el => el.querySelector('img') || el.style.backgroundImage || el.getAttribute('style')
  );
  const bodyText = (root.innerText || document.body.innerText || '').replace(/\\s+/g, '');
  const hasSuccessText = ['更换封面', '重新上传', '裁剪', '删除', '预览'].some(text => bodyText.includes(text));
  return hasPreview || hasCoverItems || hasSuccessText;
})()
"""
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def wait_for_button(target_id, button_text, timeout_seconds=10, interval_seconds=1):
    button_json = json.dumps(button_text, ensure_ascii=False)
    expression = f"""
(() => {{
  const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.trim() === {button_json});
  return !!btn && !btn.disabled;
}})()
"""
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def wait_for_text(target_id, text, timeout_seconds=15, interval_seconds=1):
    text_json = json.dumps(text, ensure_ascii=False)
    expression = f"(() => (document.body.innerText || '').includes({text_json}))()"
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def wait_for_any_text(target_id, texts, timeout_seconds=20, interval_seconds=1):
    texts_json = json.dumps(texts, ensure_ascii=False)
    expression = f"""
(() => {{
  const body = document.body.innerText || '';
  return {texts_json}.some(text => body.includes(text));
}})()
"""
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def ensure_content_statement(target_id, option_text):
    target_json = json.dumps(option_text, ensure_ascii=False)
    expression = (
        "(() => {"
        "  const targetText = " + target_json + ";"
        "  const normalize = (text) => (text || '').replace(/\\s+/g, '');"
        "  const selectors = '.content-statement-container .item, .content-statement-container .text, .content-claim label, .content-claim .item, label, .item, [role=radio], [role=checkbox], span, div';"
        "  const nodes = Array.from(document.querySelectorAll(selectors));"
        "  const target = nodes.map(node => ({node, text: normalize(node.innerText || node.textContent || node.getAttribute('aria-label') || '')}))"
        "    .filter(({text}) => text === normalize(targetText) || text.includes(normalize(targetText)))"
        "    .sort((a, b) => a.text.length - b.text.length)[0]?.node;"
        "  if (!target) return JSON.stringify({found: false});"
        "  const control = target.closest('.item, label, [role=radio], [role=checkbox]') || target;"
        "  const readChecked = () => !!("
        "    control.classList.contains('checked') ||"
        "    target.classList.contains('checked') ||"
        "    control.getAttribute('aria-checked') === 'true' ||"
        "    target.getAttribute('aria-checked') === 'true' ||"
        "    control.querySelector('input:checked') ||"
        "    target.querySelector('input:checked') ||"
        "    control.querySelector('.checked') ||"
        "    target.querySelector('.checked')"
        "  );"
        "  if (readChecked()) return JSON.stringify({found: true, checked: true, already: true});"
        "  control.click();"
        "  return JSON.stringify({found: true, checked: readChecked(), already: false, text: (control.innerText || target.innerText || target.textContent || '').trim()});"
        "})()"
    )
    raw = run_cdp("eval", target_id, expression)
    result = json.loads(raw)

    if not result.get("found"):
        raise RuntimeError(f"一点号未找到内容声明选项「{option_text}」")

    if result.get("checked"):
        print(f"[INFO] 一点号内容声明已勾选: {result}")
        return result

    time.sleep(0.6)
    verify_raw = run_cdp("eval", target_id, expression)
    verify_result = json.loads(verify_raw)
    if verify_result.get("found") and verify_result.get("checked"):
        print(f"[INFO] 一点号内容声明已勾选: {verify_result}")
        return verify_result

    raise RuntimeError(f"一点号内容声明「{option_text}」勾选失败: {result}")


def attempt_ai_declaration(target_id):
    return ensure_content_statement(target_id, YIDIAN_AI_DECLARATION)


def detect_publish_limit(target_id):
    output = run_cdp("eval", target_id, "(() => document.body.innerText || '')()")
    # These markers indicate a genuine rate-limit / publish quota error
    markers = [
        "达到发布上限",
        "发布上限",
        "发布次数",
        "请明天再来",
        "时间限制",
    ]
    for marker in markers:
        if marker in output:
            return marker
    return None


def detect_review_submitted(target_id):
    """Return True if the article was submitted for review (审核中), which
    is a successful outcome on Yidian even though the page shows a warning
    saying editing is locked until the review passes."""
    # Check current URL – Yidian redirects to the review list after submission
    url = run_cdp("eval", target_id, "location.href")
    if url and "ArticleManual" in url:
        return True
    text = run_cdp("eval", target_id, "(document.body.innerText || '').slice(0, 3000)")
    review_signals = [
        "审核中",
        "审核通过前你将无法继续编辑",
        "内容管理",  # redirected to article list
    ]
    return any(sig in (text or "") for sig in review_signals)


def main():
    parser = argparse.ArgumentParser(description="Publish Markdown article to Yidian using live Chrome.")
    parser.add_argument("markdown_file", help="Markdown article path")
    parser.add_argument(
        "--mode",
        choices=["draft", "publish"],
        default="draft",
        help="draft 保存草稿，publish 直接发布",
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
    args = parser.parse_args()
    _ = (args.theme, args.template_mode, args.article_id)

    title, _body, html, article_path = load_article(args.markdown_file)
    target_id = None
    smoke_step = "find_target"
    page_state = "starting"

    try:
        target_id = find_yidian_target()
        if not target_id:
            raise RuntimeError("没有找到一点号标签页，请先在当前 Chrome 中打开并登录一点号发文页")

        smoke_step = "ensure_editor_ready"
        target_id = ensure_editor_ready(target_id)
        page_state = "editor_ready"

        smoke_step = "inject_article"
        result = inject_article(target_id, title, html)
        page_state = "article_injected"
        print(f"[INFO] 已写入一点号编辑器: {result}")
        take_screenshot(target_id, "injected")

        # 将设置面板滚动到可视区域，以确保AI声明与封面上传元素处于激活与交互状态
        scroll_settings_into_view(target_id)
        take_screenshot(target_id, "settings_scrolled")

        need_ai_declaration = should_declare_ai(title, html, args.ai_declaration_mode)
        if need_ai_declaration:
            smoke_step = "attempt_ai_declaration"
            attempt_ai_declaration(target_id)
            page_state = "ai_declared"
        else:
            smoke_step = "clear_ai_declaration"
            clear_result = ensure_content_statement(target_id, YIDIAN_PERSONAL_OPINION)
            print(f"[INFO] 已切换一点号内容声明为个人观点，仅供参考: {clear_result}")
        take_screenshot(target_id, "ai_declaration")

        smoke_step = "apply_cover"
        if args.cover_mode == "force_on" and not args.cover:
            raise RuntimeError("一点号已要求启用封面，但当前任务没有可用封面路径")
        if args.cover_mode != "force_off" and args.cover:
            apply_cover(target_id, args.cover)
            page_state = "cover_ready"
        elif args.cover_mode == "force_off":
            cover_result = select_default_cover(target_id)
            print(f"[INFO] 已切换一点号封面为默认: {cover_result}")
            if not wait_for_default_cover(target_id):
                raise RuntimeError("一点号默认封面未选中，无法继续保存草稿")
        take_screenshot(target_id, "cover_applied")

        if args.mode == "draft":
            smoke_step = "draft_saved"
            action = click_action(target_id, "存草稿")
            if action != "clicked":
                raise RuntimeError(f"点击存草稿失败: {action}")
            page_state = "draft_saved"
            take_screenshot(target_id, "draft_saved")
            verify_in_management_list(target_id, title, is_draft=True)
            emit_smoke_state(target_id, smoke_step, page_state)
            print(f"[OK] 已存草稿: {article_path}")
            return

        if args.cover_mode == "force_off":
            smoke_step = "select_default_cover"
            cover_result = select_default_cover(target_id)
            print(f"[WARN] 一点号发布模式暂不支持彻底关闭封面，已回退到平台默认封面: {cover_result}")
            if not wait_for_default_cover(target_id):
                raise RuntimeError("一点号默认封面未选中，无法继续发布")
        elif not args.cover:
            smoke_step = "select_default_cover"
            cover_result = select_default_cover(target_id)
            print(f"[INFO] 已尝试切换默认封面: {cover_result}")
            if not wait_for_default_cover(target_id):
                raise RuntimeError("默认封面未选中，无法继续发布")

        try:
            smoke_step = "publish_ready"
            publish_ready = wait_for_button(target_id, "发布", timeout_seconds=10)
            if not publish_ready:
                raise RuntimeError("发布按钮仍不可点击，请检查页面是否还有未填项")
            take_screenshot(target_id, "before_publish")

            smoke_step = "publish_click"
            action = click_action(target_id, "发布")
            if action != "clicked":
                raise RuntimeError(f"点击发布失败: {action}")

            if wait_for_button(target_id, "确定", timeout_seconds=8):
                smoke_step = "publish_confirm"
                confirm_action = click_action(target_id, "确定")
                if confirm_action != "clicked":
                    raise RuntimeError(f"点击发布确认失败: {confirm_action}")
                print("[INFO] 已确认发布弹窗")

            limit_marker = detect_publish_limit(target_id)
            if limit_marker:
                raise RuntimeError(f"一点号发布受限: {limit_marker}")

            smoke_step = "published"
            if not wait_for_any_text(target_id, ["发布成功", "查看文章", "再写一篇", "审核通过前"], timeout_seconds=20):
                # Give an extra chance: Yidian may redirect to the review list
                if detect_review_submitted(target_id):
                    print("[INFO] 一点号文章已提交审核（审核中），视为发布成功")
                else:
                    limit_marker = detect_publish_limit(target_id)
                    if limit_marker:
                        raise RuntimeError(f"一点号发布受限: {limit_marker}")
                    raise RuntimeError("未检测到一点号发布成功提示，请检查页面状态")

            if wait_for_button(target_id, "查看文章", timeout_seconds=5):
                view_action = click_action(target_id, "查看文章")
                if view_action != "clicked":
                    raise RuntimeError(f"点击查看文章失败: {view_action}")

            page_state = "published"
            take_screenshot(target_id, "published")
            verify_in_management_list(target_id, title, is_draft=False)
            emit_smoke_state(target_id, smoke_step, page_state)
            print(f"[OK] 已发布成功: {article_path}")
        except Exception as exc:
            err_msg = str(exc)
            if "发布受限" in err_msg or "不可点击" in err_msg or "确认发布" in err_msg or "发布失败" in err_msg:
                print(f"[WARN] 一点号直接发布遇到限制 ({err_msg})，正在尝试以降级保存为草稿模式执行...")
                run_cdp(
                    "eval",
                    target_id,
                    """
(() => {
  const dialog = document.querySelector('.el-dialog__wrapper, .dialog-wrapper, .modal, .popup');
  if (dialog && dialog.style.display !== 'none') {
    const closeBtn = dialog.querySelector('.el-dialog__close, .close, .btn-close') || dialog.querySelector('button');
    if (closeBtn) closeBtn.click();
  }
})()
""".strip(),
                )
                time.sleep(1)
                smoke_step = "draft_saved"
                action = click_action(target_id, "存草稿")
                if action == "clicked":
                    page_state = "draft_saved"
                    verify_in_management_list(target_id, title, is_draft=True)
                    emit_smoke_state(target_id, smoke_step, page_state)
                    print(f"[WARN] 一点号直接发表失败，已成功以降级保存草稿模式保存该文章！")
                    print(f"[OK] 已存草稿: {article_path}")
                    return
                else:
                    print(f"[ERROR] 降级存草稿失败: {action}")
            raise
    except Exception as exc:
        take_screenshot(target_id, "error")
        emit_smoke_state(target_id, smoke_step, page_state, error=exc)
        raise


if __name__ == "__main__":
    main()
