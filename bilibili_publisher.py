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
BILIBILI_MATCHES = ("member.bilibili.com",)
BILIBILI_EDITOR_URL = "https://member.bilibili.com/platform/upload/text/new-edit"
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


def find_bilibili_target():
    bound_target = os.environ.get("PUBLISH_TARGET_BILIBILI")
    if bound_target:
        return bound_target
    output = run_cdp("list")
    fallback_target = None
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        target_id, _title, url = parts[0], parts[1], parts[2]
        if "bilibili.com" in url and fallback_target is None:
            fallback_target = target_id
        if any(match in url for match in BILIBILI_MATCHES):
            return target_id
    return fallback_target


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

    title = title[:80]
    html_body = render_markdown_html(body)
    return title, html_body, path


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
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document || document;
  const titleEl = doc.querySelector('textarea.title-input__inner');
  const editor = doc.querySelector('div.tiptap.ProseMirror');
  const bodyText = (doc.body.innerText || '').replace(/\\s+/g, ' ').trim();
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
        screenshot_dir = BASE_DIR / ".ordo" / "screenshots" / "bilibili"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        filepath = screenshot_dir / f"{timestamp}_{step}.png"
        run_cdp("shot", target_id, str(filepath))
        print(f"[INFO] [SCREENSHOT] Saved to: {filepath}")
    except Exception as e:
        print(f"[WARN] 截图失败 ({step}): {e}")


def ensure_editor_ready(target_id):
    check_ready_js = """(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  return !!(doc && doc.querySelector('textarea.title-input__inner') && doc.querySelector('div.tiptap.ProseMirror'));
})()"""
    if run_cdp("eval", target_id, check_ready_js) == "true":
        print("[INFO] 哔哩哔哩编辑器已就绪，跳过页面导航")
        return

    run_cdp("nav", target_id, BILIBILI_EDITOR_URL)
    ready = wait_until(
        target_id,
        check_ready_js,
        timeout_seconds=20,
    )
    if not ready:
        raise RuntimeError("哔哩哔哩专栏编辑器加载超时")


def inject_article(target_id, title, html_body):
    title_json = json.dumps(title, ensure_ascii=False)
    html_json = json.dumps(html_body, ensure_ascii=False)
    expression = f"""
(() => {{
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';

  const titleInput = doc.querySelector('textarea.title-input__inner');
  const editor = doc.querySelector('div.tiptap.ProseMirror');
  if (!titleInput || !editor) {{
    return 'missing-editor';
  }}

  // 1. Set title
  titleInput.value = {title_json};
  titleInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
  titleInput.dispatchEvent(new Event('change', {{ bubbles: true }}));

  // 2. Set body HTML
  editor.innerHTML = {html_json};
  editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
  editor.dispatchEvent(new Event('change', {{ bubbles: true }}));
  editor.dispatchEvent(new Event('blur', {{ bubbles: true }}));

  return 'injected';
}})()
"""
    return run_cdp("eval", target_id, expression)


def ensure_settings_panel_expanded(target_id):
    expand_js = """
(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';

  const label = Array.from(doc.querySelectorAll('.form-item-label, label'))
    .find(l => l.innerText.includes('自定义封面'));

  if (label) {
    label.scrollIntoView({ block: 'center' });
    return 'scrolled';
  }

  const btn = doc.querySelector('button.settings-button');
  if (btn) {
    btn.click();
    return 'clicked';
  }

  const buttons = Array.from(doc.querySelectorAll('button'));
  const textBtn = buttons.find(b => b.innerText.includes('发布设置'));
  if (textBtn) {
    textBtn.click();
    return 'clicked-by-text';
  }

  return 'not-found';
})()
"""
    res = run_cdp("eval", target_id, expand_js)
    print(f"[INFO] 展开发布设置面板结果: {res}")
    time.sleep(1.5)
    return res


def ensure_custom_cover_on(target_id):
    # Ensure custom cover switch is checked
    switch_js = """
(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';
  const labels = Array.from(doc.querySelectorAll('.form-item-label, label'));
  const label = labels.find(l => l.innerText.includes('自定义封面'));
  if (!label) return 'label-not-found';
  const parent = label.parentElement;
  const sw = parent.querySelector('.vui_switch--switch');
  if (!sw) return 'switch-not-found';

  sw.scrollIntoView({ block: 'center' });
  const isChecked = sw.classList.contains('is-checked') || sw.querySelector('input').checked;
  if (!isChecked) {
    sw.click();
    return 'clicked';
  }
  return 'already-checked';
})()
"""
    res = run_cdp("eval", target_id, switch_js)
    if res == "clicked":
        time.sleep(1.5)
    return res


def apply_cover(target_id, cover_path):
    print(f"[INFO] 正在为哔哩哔哩专栏上传封面图: {cover_path}")
    ensure_custom_cover_on(target_id)

    click_local_upload_js = """(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';
  const upload = doc.querySelector('div.upload-button');
  if (upload) upload.click();
  const items = Array.from(doc.querySelectorAll('button, div, span, a'));
  const item = items.find(el => (el.innerText || '').trim() === '本地上传');
  if (item) {
    item.click();
    return 'clicked-local-upload';
  }
  return upload ? 'clicked-upload-button' : 'upload-button-not-found';
})()"""
    local_upload_res = run_cdp("eval", target_id, click_local_upload_js)
    print(f"[INFO] 本地上传入口点击结果: {local_upload_res}")
    time.sleep(1.0)

    check_input_js = """(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  return !!(doc && doc.querySelector('input[type="file"]'));
})()"""

    if run_cdp("eval", target_id, check_input_js) != "true":
        print("[INFO] 未检测到 file input 元素，尝试点击 upload-button 唤醒...")
        click_upload_js = """(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';
  const btn = doc.querySelector('div.upload-button');
  if (btn) {
    btn.click();
    return 'clicked';
  }
  return 'not-found';
})()"""
        run_cdp("eval", target_id, click_upload_js)
        time.sleep(1.5)

    # Wait for input[type="file"] to be present in the editor iframe
    print("[INFO] 等待 file input 控件加载...")
    input_ready = wait_until(
        target_id,
        check_input_js,
        timeout_seconds=10,
    )
    if not input_ready:
        raise RuntimeError("未在哔哩哔哩编辑器内找到 file input 元素")

    # Use pierced setfile to upload to hidden file input
    # Bilibili only has one input[type=file] which is accept=".jpg,.jpeg,.png"
    selector = 'input[type="file"]'
    print(f"[INFO] CDP setfile on {selector} …")
    setfile_res = run_cdp("setfile", target_id, selector, str(cover_path))
    print(f"[INFO] setfile upload result: {setfile_res}")
    time.sleep(2.5)

    # Click crop confirm button "确定"
    confirm_js = """
(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';
  const buttons = Array.from(doc.querySelectorAll('button, div, span, a'));
  const confirmBtn = buttons.find(b => b.innerText.trim() === '确定');
  if (confirmBtn) {
    confirmBtn.click();
    return 'confirmed';
  }
  return 'crop-dialog-not-found';
})()
"""
    confirm_res = run_cdp("eval", target_id, confirm_js)
    print(f"[INFO] 封面裁剪确认结果: {confirm_res}")
    if confirm_res != "confirmed":
        preview_js = """
(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';
  const area = Array.from(doc.querySelectorAll('div, section')).find(el => (el.innerText || '').includes('自定义封面'));
  const root = area?.parentElement || doc;
  const hasImage = !!root.querySelector('img, canvas') || Array.from(root.querySelectorAll('*')).some(el => {
    const bg = getComputedStyle(el).backgroundImage || '';
    return bg && bg !== 'none';
  });
  return hasImage ? 'preview-ready' : 'preview-missing';
})()
"""
        preview_res = run_cdp("eval", target_id, preview_js)
        print(f"[INFO] 封面预览检测结果: {preview_res}")
        if preview_res != "preview-ready":
            raise RuntimeError(f"哔哩哔哩封面裁剪确认失败: {confirm_res}")
    time.sleep(1.5)


def ensure_ai_declaration(target_id, should_declare):
    print(f"[INFO] 设置 AI 创作声明: {should_declare} …")
    ai_js = f"""
(() => {{
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';
  const labels = Array.from(doc.querySelectorAll('label, .vui_checkbox'));
  const lb = labels.find(node => (node.innerText || '').includes('AI辅助创作声明'));
  if (!lb) return 'label-not-found';

  const input = lb.querySelector('input[type=checkbox]');
  const isChecked = !!(input?.checked || lb.classList.contains('is-checked') || lb.querySelector('.is-checked') || lb.classList.contains('vui_checkbox--checked'));
  const targetState = {str(should_declare).lower()};

  if (isChecked === targetState) {{
    return 'already-in-target-state';
  }}
  lb.click();
  return 'clicked';
}})()
"""
    res = run_cdp("eval", target_id, ai_js)
    print(f"[INFO] AI 辅助声明点击结果: {res}")
    time.sleep(1)


def click_draft_button(target_id):
    click_js = """
(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';
  const buttons = Array.from(doc.querySelectorAll('button'));
  const btn = buttons.find(b => b.innerText.trim() === '保存为草稿');
  if (btn) {
    btn.click();
    return 'clicked';
  }
  return 'not-found';
})()
"""
    return run_cdp("eval", target_id, click_js)


def click_publish_button(target_id):
    click_js = """
(() => {
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-doc';
  const buttons = Array.from(doc.querySelectorAll('button'));
  const btn = buttons.find(b => b.innerText.trim() === '发布' && b.classList.contains('vui_button--blue'));
  if (btn) {
    btn.click();
    return 'clicked';
  }
  return 'not-found';
})()
"""
    return run_cdp("eval", target_id, click_js)


def verify_in_management_list(target_id, expected_title, is_draft=False):
    management_url = "https://member.bilibili.com/platform/upload-manager/opus"
    print(f"[INFO] 正在跳转至B站稿件管理页面进行校验: {management_url}")
    run_cdp("nav", target_id, management_url)
    time.sleep(4)

    tab_href = "/opus/management/drafts" if is_draft else "/opus/management/opus"
    click_tab_js = f"""
(() => {{
  const iframe = document.querySelector('iframe[src*="opus/management"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return 'no-iframe-doc';
  const a = doc.querySelector('a[href="{tab_href}"]');
  if (a) {{
    a.click();
    return 'clicked';
  }}
  return 'tab-not-found';
}})()
"""
    click_res = run_cdp("eval", target_id, click_tab_js)
    print(f"[INFO] 切换B站Tab结果: {click_res}")
    time.sleep(2)

    verify_js = f"""
(() => {{
  const iframe = document.querySelector('iframe[src*="opus/management"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  if (!doc) return JSON.stringify({{ok: false, reason: 'no-iframe-doc'}});

  const expectedTitle = {json.dumps(expected_title, ensure_ascii=False)};
  const normalize = (text) => (text || '').replace(/\\s+/g, '');

  const cards = Array.from(doc.querySelectorAll('.draft-card, .opus-card'));
  const found = cards.some(c => {{
    const titleEl = c.querySelector('.draft-card__title, .opus-card-meta__title, h2, h3, a');
    const text = (titleEl ? titleEl.innerText : c.innerText) || '';
    return normalize(text).includes(normalize(expectedTitle));
  }});

  return JSON.stringify({{
    ok: found,
    count: cards.length
  }});
}})()
"""
    raw_res = run_cdp("eval", target_id, verify_js)
    res = json.loads(raw_res)
    if res.get("ok"):
        print(f"[OK] 双重校验成功！稿件列表中找到标题为 「{expected_title}」 的文章。")
        take_screenshot(target_id, "verified_list")
        return True
    else:
        print(f"[WARN] 双重校验未在列表中找到标题为 「{expected_title}」 的文章: {res}")
        take_screenshot(target_id, "verify_failed")
        raise RuntimeError(f"哔哩哔哩稿件列表校验失败: {res}")


def main():
    parser = argparse.ArgumentParser(description="Publish Markdown article to Bilibili using live Chrome.")
    parser.add_argument("markdown_file", help="Markdown article path")
    parser.add_argument(
        "--mode",
        choices=["draft", "publish"],
        default="draft",
        help="draft 保存为草稿；publish 直接发布",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help="可选主题标识（编排层预留）。",
    )
    parser.add_argument(
        "--cover",
        default=None,
        metavar="PATH",
        help="可选封面图路径。",
    )
    parser.add_argument(
        "--template-mode",
        dest="template_mode",
        default=None,
        help="可选模板模式。",
    )
    parser.add_argument(
        "--article-id",
        dest="article_id",
        default=None,
        help="可选文章标识。",
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

    title, html_body, article_path = load_article(args.markdown_file)
    target_id = None
    smoke_step = "find_target"
    page_state = "starting"

    try:
        target_id = find_bilibili_target()
        if not target_id:
            raise RuntimeError("没有找到哔哩哔哩标签页，请先在当前 Chrome 中打开并登录任意哔哩哔哩页面")

        smoke_step = "ensure_editor_ready"
        ensure_editor_ready(target_id)
        page_state = "editor_ready"

        if args.cover_mode == "force_on" and not args.cover:
            raise RuntimeError("哔哩哔哩已要求启用封面，但当前任务没有可用封面路径")

        # First expand publish settings panel to ensure elements exist in DOM
        ensure_settings_panel_expanded(target_id)

        if args.cover_mode != "force_off" and args.cover:
            smoke_step = "apply_cover"
            apply_cover(target_id, args.cover)
            page_state = "cover_ready"
            print(f"[INFO] 已设置哔哩哔哩自定义封面: {args.cover}")
            take_screenshot(target_id, "cover_ready")
        elif args.cover_mode == "force_off":
            print("[INFO] 已显式关闭哔哩哔哩封面设置")

        smoke_step = "inject_article"
        inject_result = inject_article(target_id, title, html_body)
        if inject_result == "missing-editor":
            raise RuntimeError("编辑器注入失败：未找到标题或正文输入区")
        page_state = "article_injected"
        print(f"[INFO] 已写入哔哩哔哩编辑器: {inject_result}")
        take_screenshot(target_id, "injected")

        # Set AI declaration
        need_ai_declaration = should_declare_ai(title, html_body, args.ai_declaration_mode)
        smoke_step = "ensure_ai_declaration"
        ensure_ai_declaration(target_id, need_ai_declaration)
        page_state = "ai_declared"
        take_screenshot(target_id, "ai_declared")

        if args.mode == "draft":
            smoke_step = "draft_saved"
            click_res = click_draft_button(target_id)
            if click_res != "clicked":
                raise RuntimeError(f"点击保存为草稿按钮失败: {click_res}")
            print("[INFO] 已点击保存为草稿按钮，等待保存完成 …")
            time.sleep(3)
            page_state = "draft_saved"
            take_screenshot(target_id, "draft_saved")
            verify_in_management_list(target_id, title, is_draft=True)
            emit_smoke_state(target_id, smoke_step, page_state)
            print(f"[OK] 已保存哔哩哔哩草稿: {article_path}")
            return

        # publish mode
        smoke_step = "publishing"
        click_res = click_publish_button(target_id)
        if click_res != "clicked":
            raise RuntimeError(f"点击发布按钮失败: {click_res}")
        print("[INFO] 已点击发布按钮，等待发布完成 …")
        time.sleep(5)
        page_state = "published"
        take_screenshot(target_id, "published")
        verify_in_management_list(target_id, title, is_draft=False)
        emit_smoke_state(target_id, smoke_step, page_state)
        print(f"[OK] 已发布哔哩哔哩文章: {article_path}")

    except Exception as e:
        take_screenshot(target_id, "error")
        emit_smoke_state(target_id, smoke_step, page_state, error=e)
        raise


if __name__ == "__main__":
    main()
