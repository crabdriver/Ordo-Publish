import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from markdown_utils import render_markdown_plain_text, should_declare_ai
from ordo_engine.importers.normalize import strip_title_marker
from ordo_engine.platforms.browser.node_runtime import resolve_node_executable


BASE_DIR = Path(__file__).resolve().parent
CDP_SCRIPT = BASE_DIR / "live_cdp.mjs"
ZHIHU_MATCHES = ("zhuanlan.zhihu.com", "www.zhihu.com/write", "www.zhihu.com/creator")
ZHIHU_EDITOR_URL = "https://zhuanlan.zhihu.com/write"
ZHIHU_COVER_FILE_INPUT = "input.UploadPicture-input"
ZHIHU_AI_DECLARATION = "包含 AI 辅助创作"
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


def find_zhihu_target():
    bound_target = os.environ.get("PUBLISH_TARGET_ZHIHU")
    if bound_target:
        return bound_target
    output = run_cdp("list")
    fallback_target = None
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        target_id, _title, url = parts[0], parts[1], parts[2]
        if "zhihu.com" in url and fallback_target is None:
            fallback_target = target_id
        if any(match in url for match in ZHIHU_MATCHES):
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

    title = title[:100]
    plain_body = render_markdown_plain_text(body)
    return title, plain_body, path


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
  const titleEl = document.querySelector('textarea[placeholder*="标题"], input[placeholder*="标题"]');
  const editor = document.querySelector('.public-DraftEditor-content, .ProseMirror, [data-lexical-editor="true"], [contenteditable="true"]');
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
        screenshot_dir = BASE_DIR / ".ordo" / "screenshots" / "zhihu"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        filepath = screenshot_dir / f"{timestamp}_{step}.png"
        run_cdp("shot", target_id, str(filepath))
        print(f"[INFO] [SCREENSHOT] Saved to: {filepath}")
    except Exception as e:
        print(f"[WARN] 截图失败 ({step}): {e}")


def verify_in_management_list(target_id, expected_title, is_draft=False):
    if is_draft:
        management_url = "https://www.zhihu.com/creator/manage/creation/draft?type=article"
    else:
        management_url = "https://www.zhihu.com/creator/manage/creation/all?type=article"
    print(f"[INFO] 正在跳转至知乎内容管理页面进行校验: {management_url}")
    run_cdp("nav", target_id, management_url)
    time.sleep(3)

    click_tabs_expr = f"""
    (() => {{
        const isDraft = {str(is_draft).lower()};
        const tabs = Array.from(document.querySelectorAll('div, span, li, a, p'));
        if (isDraft) {{
            // 尝试点击子标签「文章草稿」作为二次确认
            const subTabs = Array.from(document.querySelectorAll('div, span, li, a, p'));
            const articleSubTab = subTabs.find(el => {{
                const txt = (el.innerText || el.textContent || '').trim();
                return txt.includes('文章草稿') && el.offsetHeight > 0;
            }});
            if (articleSubTab) {{
                articleSubTab.click();
                return 'draft_subtab_clicked';
            }}
            return 'no_draft_subtab';
        }} else {{
            // 尝试点击主标签「文章」作为二次确认
            const articleTab = tabs.find(el => {{
                const txt = (el.innerText || el.textContent || '').trim();
                return txt === '文章' && el.offsetHeight > 0;
            }});
            if (articleTab) {{
                articleTab.click();
                return 'article_tab_clicked';
            }}
            return 'no_article_tab';
        }}
    }})()
    """
    res = run_cdp("eval", target_id, click_tabs_expr)
    print(f"[INFO] 执行知乎标签切换动作结果: {res}")
    time.sleep(2)

    title_json = json.dumps(expected_title, ensure_ascii=False)
    list_ready_expr = """
(() => {
  const normalize = (t) => (t || '').replace(/\\s+/g, '');
  const target = normalize(""" + title_json + """);
  const els = Array.from(document.querySelectorAll('a, div, span, h1, h2, h3, h4'));
  const found = els.find(el => {
    const txt = normalize(el.innerText || el.textContent || '');
    return txt.includes(target) && el.offsetHeight > 0;
  });
  return !!found;
})()
"""
    print(f"[INFO] 正在校验列表是否包含文章标题：《{expected_title}》...")
    success = wait_until(target_id, list_ready_expr, timeout_seconds=15, interval_seconds=1)
    take_screenshot(target_id, "verified_list")
    if not success:
        raise RuntimeError(f"双重校验失败：未能在知乎作品列表中找到文章《{expected_title}》")
    print(f"[INFO] 双重校验成功：在作品列表中成功检索到文章《{expected_title}》！")


def apply_cover(target_id, cover_path, run_cdp_fn=None):
    """通过 CDP 向知乎「添加文章封面」对应的 file input 注入本地文件。"""
    runner = run_cdp_fn or run_cdp
    path = Path(cover_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"封面文件不存在: {path}")
    runner("setfile", target_id, ZHIHU_COVER_FILE_INPUT, str(path))


def ensure_editor_ready(target_id):
    already_ready = wait_until(
        target_id,
        """
(() => {
  const titleEl = Array.from(document.querySelectorAll('textarea[placeholder*="标题"], input[placeholder*="标题"]'))
    .find(el => isVisible(el));
  const editor = document.querySelector('.public-DraftEditor-content, .ProseMirror, [data-lexical-editor="true"], [contenteditable="true"]');
  return !!titleEl && !!editor;
})()
""".strip(),
        timeout_seconds=3,
        interval_seconds=0.5,
    )
    if already_ready:
        return

    run_cdp("nav", target_id, ZHIHU_EDITOR_URL)
    ready = wait_until(
        target_id,
        """
(() => {
  const titleEl = document.querySelector('textarea[placeholder*="标题"], input[placeholder*="标题"]');
  const editor = Array.from(document.querySelectorAll('.public-DraftEditor-content,.ProseMirror,[data-lexical-editor="true"],[contenteditable="true"]')).find(el => {
    const rect = el.getBoundingClientRect();
    return rect.width >= 300 && rect.height >= 10;
  });
  return !!titleEl && !!editor;
})()
""".strip(),
        timeout_seconds=30,
    )
    if not ready:
        raise RuntimeError("知乎写作页未就绪，请确认当前 Chrome 已登录知乎并能进入写文章页面")


def inject_article(target_id, title, plain_body):
    title_json = json.dumps(title, ensure_ascii=False)
    expression = f"""
(() => {{
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }};

  const titleEl = document.querySelector('textarea[placeholder*="标题"], input[placeholder*="标题"]');
  const editor = Array.from(document.querySelectorAll(
    '.public-DraftEditor-content,.ProseMirror,[data-lexical-editor="true"],[contenteditable="true"]'
  )).find(el => {{
    if (!isVisible(el)) return false;
    const rect = el.getBoundingClientRect();
    return rect.width >= 300 && rect.height >= 10;
  }});

  if (!titleEl || !editor) {{
    return JSON.stringify({{
      ok: false,
      reason: 'missing-editor',
      hasTitle: !!titleEl
    }});
  }}

  if ('value' in titleEl) {{
    const proto = titleEl.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) {{
      setter.call(titleEl, {title_json});
    }} else {{
      titleEl.value = {title_json};
    }}
  }} else {{
    titleEl.textContent = {title_json};
  }}
  titleEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
  titleEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
  editor.focus();
  editor.click();
  try {{
    document.execCommand('selectAll');
    document.execCommand('delete');
  }} catch (_err) {{}}
  const rect = editor.getBoundingClientRect();

  return JSON.stringify({{
    ok: true,
    title: ('value' in titleEl ? titleEl.value : titleEl.innerText || titleEl.textContent || '').trim(),
    editorTag: editor.tagName,
    point: {{
      x: rect.left + Math.min(rect.width / 2, 80),
      y: rect.top + Math.max(rect.height / 2, 12)
    }}
  }});
}})()
"""
    output = run_cdp("eval", target_id, expression)
    parsed = json.loads(output)
    if not parsed.get("ok"):
        raise RuntimeError(f"写入知乎编辑器失败: {parsed}")
    point = parsed["point"]
    run_cdp("clickxy", target_id, str(point["x"]), str(point["y"]))
    time.sleep(0.3)
    run_cdp("type", target_id, plain_body)
    time.sleep(0.5)

    verify_output = run_cdp(
        "eval",
        target_id,
        r"""
(() => {
  const titleEl = document.querySelector('textarea[placeholder*="标题"], input[placeholder*="标题"]');
  const editor = document.querySelector('.public-DraftEditor-content, .ProseMirror, [data-lexical-editor="true"], [contenteditable="true"]');
  const bodyText = (editor?.innerText || editor?.textContent || editor?.value || '').trim();
  const wordCount = Number((document.body.innerText || '').match(/字数：(\d+)/)?.[1] || 0);
  return JSON.stringify({
    title: (titleEl?.value || titleEl?.innerText || '').trim(),
    bodyLength: bodyText.length || wordCount
  });
})()
""".strip(),
    )
    verify = json.loads(verify_output)
    return json.dumps({**parsed, **verify}, ensure_ascii=False)


def get_click_center(target_id, text, selector):
    text_json = json.dumps(text, ensure_ascii=False)
    selector_json = json.dumps(selector, ensure_ascii=False)
    expression = f"""
(() => {{
  const isVisible = (el) => {{
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }};

  const nodes = Array.from(document.querySelectorAll({selector_json})).filter(isVisible);
  const target = nodes.find(node => (node.innerText || '').replace(/\\s+/g, '') === {text_json}.replace(/\\s+/g, ''));
  if (!target) return '';
  const rect = target.getBoundingClientRect();
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


def click_text_by_xy(target_id, text, selector):
    point = get_click_center(target_id, text, selector=selector)
    if not point:
        return "target-not-found"
    run_cdp("clickxy", target_id, str(point["x"]), str(point["y"]))
    return "clicked"


def wait_for_any_text(target_id, texts, timeout_seconds=20, interval_seconds=1):
    texts_json = json.dumps(texts, ensure_ascii=False)
    expression = f"""
(() => {{
  const content = document.body.innerText || '';
  return {texts_json}.some(text => content.includes(text));
}})()
"""
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def declare_ai_creation(target_id):
    print("[INFO] 开始设置AI创作声明 …")

    already_visible = wait_until(
        target_id,
        """(() => {
  const labels = Array.from(document.querySelectorAll('label'));
  return labels.some(l => (l.innerText || '').includes('创作声明'));
})()""",
        timeout_seconds=2,
        interval_seconds=0.5,
    )

    if not already_visible:
        print("[INFO] 展开「发布设置」面板 …")
        run_cdp(
            "eval",
            target_id,
            """(() => {
  const btn = Array.from(document.querySelectorAll('button'))
    .find(e => (e.innerText || '').trim().startsWith('发布设置'));
  if (btn) { btn.click(); return 'clicked'; }
  return 'not-found';
})()""",
        )
        if not wait_until(
            target_id,
            """(() => {
  const labels = Array.from(document.querySelectorAll('label'));
  return labels.some(l => (l.innerText || '').includes('创作声明'));
})()""",
            timeout_seconds=10,
        ):
            raise RuntimeError("展开「发布设置」后未找到「创作声明」标签，请确认页面状态")

    combo_info = run_cdp(
        "eval",
        target_id,
        """(() => {
  const label = Array.from(document.querySelectorAll('label'))
    .find(l => (l.innerText || '').includes('创作声明'));
  if (!label) return JSON.stringify({ok: false, reason: 'label-not-found'});
  const row = label.parentElement;
  const combo = row && row.querySelector('[role=combobox]');
  if (!combo) return JSON.stringify({ok: false, reason: 'combobox-not-found'});
  const text = (combo.innerText || '').trim();
  return JSON.stringify({
    ok: true,
    text: text
  });
})()""",
    )
    info = json.loads(combo_info)
    if not info.get("ok"):
        raise RuntimeError(f"未找到「创作声明」下拉框: {info.get('reason')}")

    if normalize_ui_text(info.get("text", "")) == normalize_ui_text(ZHIHU_AI_DECLARATION):
        print(f"[INFO] AI创作声明已选中: {info['text']}")
        return

    print(f"[INFO] 当前创作声明值: {info['text']}，点击下拉框 …")
    click_result = run_cdp(
        "eval",
        target_id,
        """(() => {
  const label = Array.from(document.querySelectorAll('label'))
    .find(l => (l.innerText || '').includes('创作声明'));
  if (!label) return 'label-not-found';
  const combo = label.parentElement && label.parentElement.querySelector('[role=combobox]');
  if (!combo) return 'combobox-not-found';
  combo.scrollIntoView({ block: 'center', inline: 'center' });
  combo.click();
  return combo.getAttribute('aria-expanded') === 'true' ? 'clicked' : 'clicked';
})()""",
    )
    if click_result not in {"clicked"}:
        raise RuntimeError(f"点击「创作声明」下拉框失败: {click_result}")

    options_visible = wait_until(
        target_id,
        f"""(() => {{
  const opts = Array.from(document.querySelectorAll('[role=option]'));
  const normalize = (text) => (text || '').replace(/\\s+/g, '');
  return opts.some(o => normalize(o.innerText || '').includes(normalize({json.dumps(ZHIHU_AI_DECLARATION, ensure_ascii=False)})));
}})()""",
        timeout_seconds=8,
    )

    print(f"[INFO] 选择「{ZHIHU_AI_DECLARATION}」…")
    if options_visible:
        select_result = run_cdp(
            "eval",
            target_id,
            f"""(() => {{
  const normalize = (text) => (text || '').replace(/\\s+/g, '');
  const opt = Array.from(document.querySelectorAll('[role=option]'))
    .find(o => normalize(o.innerText || '').includes(normalize({json.dumps(ZHIHU_AI_DECLARATION, ensure_ascii=False)})));
  if (!opt) return 'not-found';
  opt.click();
  return 'clicked';
}})()""",
        )
    else:
        select_result = run_cdp(
            "eval",
            target_id,
            f"""(() => {{
  const normalize = (text) => (text || '').replace(/\\s+/g, '');
  const targetText = normalize({json.dumps(ZHIHU_AI_DECLARATION, ensure_ascii=False)});
  const nodes = Array.from(document.querySelectorAll('button,li,div,span,label'));
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const target = nodes.find(node => {{
    const text = normalize(node.innerText || node.textContent || '');
    if (!text.includes(targetText) || !isVisible(node)) return false;
    const row = node.closest('[role=listbox], ul, li, .css-be2u3, .css-13mrzb0, .Popover-content');
    return !!row;
  }});
  if (!target) return 'not-found';
  target.scrollIntoView({{ block: 'center', inline: 'center' }});
  target.click();
  return 'clicked';
}})()""",
        )

    if select_result != "clicked":
        raise RuntimeError(f"未能点击「{ZHIHU_AI_DECLARATION}」选项")

    time.sleep(0.5)

    verify_output = run_cdp(
        "eval",
        target_id,
        """(() => {
  const label = Array.from(document.querySelectorAll('label'))
    .find(l => (l.innerText || '').includes('创作声明'));
  if (!label) return '';
  const combo = label.parentElement && label.parentElement.querySelector('[role=combobox]');
  return combo ? (combo.innerText || '').trim() : '';
})()""",
    )
    if normalize_ui_text(ZHIHU_AI_DECLARATION) not in normalize_ui_text(verify_output):
        raise RuntimeError(f"选择后验证失败，下拉框当前值: {verify_output}")

    print(f"[INFO] 已设置AI创作声明: {verify_output}")


def detect_publish_limit(target_id):
    output = run_cdp(
        "eval",
        target_id,
        """
(() => document.body.innerText || '')()
""".strip(),
    )
    markers = [
        "达到发布上限",
        "发布次数已达上限",
        "今日发布次数",
        "请明天再来",
        "频繁发布",
        "稍后再试",
    ]
    for marker in markers:
        if marker in output:
            return marker
    return None


def main():
    parser = argparse.ArgumentParser(description="Publish Markdown article to Zhihu using live Chrome.")
    parser.add_argument("markdown_file", help="Markdown article path")
    parser.add_argument(
        "--mode",
        choices=["draft", "publish"],
        default="draft",
        help="draft 只写入并等待自动保存；publish 尝试直接发布",
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

    title, plain_body, article_path = load_article(args.markdown_file)
    target_id = None
    smoke_step = "find_target"
    page_state = "starting"

    try:
        target_id = find_zhihu_target()
        if not target_id:
            raise RuntimeError("没有找到知乎标签页，请先在当前 Chrome 中打开并登录任意知乎页面")

        smoke_step = "ensure_editor_ready"
        ensure_editor_ready(target_id)
        page_state = "editor_ready"

        if args.cover_mode == "force_on" and not args.cover:
            raise RuntimeError("知乎已要求启用封面，但当前任务没有可用封面路径")

        if args.cover_mode != "force_off" and args.cover:
            smoke_step = "apply_cover"
            apply_cover(target_id, args.cover)
            page_state = "cover_ready"
            print(f"[INFO] 已注入知乎封面: {args.cover}")
            take_screenshot(target_id, "cover_ready")
        elif args.cover_mode == "force_off":
            print("[INFO] 已显式关闭知乎封面设置")

        smoke_step = "inject_article"
        inject_result = inject_article(target_id, title, plain_body)
        page_state = "article_injected"
        print(f"[INFO] 已写入知乎编辑器: {inject_result}")
        take_screenshot(target_id, "injected")

        need_ai_declaration = should_declare_ai(title, plain_body, args.ai_declaration_mode)
        if need_ai_declaration:
            smoke_step = "declare_ai_creation"
            declare_ai_creation(target_id)
            page_state = "ai_declared"
        else:
            print("[INFO] 跳过或已显式关闭知乎 AI 创作声明设置")
        take_screenshot(target_id, "ai_declared")

        if args.mode == "draft":
            smoke_step = "draft_saved"
            draft_saved = wait_for_any_text(
                target_id,
                ["草稿", "已保存", "保存成功", "正在保存", "已自动保存"],
                timeout_seconds=15,
            )
            if not draft_saved:
                print("[WARN] 未明确检测到知乎保存文案，可能仍在自动保存")
            page_state = "draft_saved"
            take_screenshot(target_id, "draft_saved")
            verify_in_management_list(target_id, title, is_draft=True)
            emit_smoke_state(target_id, smoke_step, page_state)
            print(f"[OK] 已写入知乎草稿页: {article_path}")
            return

        smoke_step = "publish_click"
        take_screenshot(target_id, "before_publish")
        publish_result = click_text_by_xy(target_id, "发布", selector="button,a,div,span")
        if publish_result != "clicked":
            publish_result = click_text_by_xy(target_id, "发布文章", selector="button,a,div,span")
        if publish_result != "clicked":
            raise RuntimeError(f"点击知乎发布失败: {publish_result}")

        if wait_until(
            target_id,
            "(() => Array.from(document.querySelectorAll('button,a,div,span')).some(el => ['确认发布','立即发布','发布'].includes((el.innerText || '').replace(/\\s+/g, ''))))()",
            timeout_seconds=8,
        ):
            smoke_step = "publish_confirm"
            confirm_result = click_text_by_xy(target_id, "确认发布", selector="button,a,div,span")
            if confirm_result != "clicked":
                confirm_result = click_text_by_xy(target_id, "立即发布", selector="button,a,div,span")
            if confirm_result != "clicked":
                confirm_result = click_text_by_xy(target_id, "发布", selector="button,a,div,span")
            if confirm_result != "clicked":
                raise RuntimeError(f"点击知乎确认发布失败: {confirm_result}")

        limit_marker = detect_publish_limit(target_id)
        if limit_marker:
            raise RuntimeError(f"知乎发布受限: {limit_marker}")

        smoke_step = "published"
        published = wait_until(
            target_id,
            """
(() => {
  const text = document.body.innerText || '';
  return (
    text.includes('发布成功') ||
    text.includes('文章已发布') ||
    text.includes('查看文章') ||
    /^https:\\/\\/zhuanlan\\.zhihu\\.com\\/p\\/\\d+(?:[?#].*)?$/.test(location.href)
  );
})()
""".strip(),
            timeout_seconds=30,
        )
        if not published:
            limit_marker = detect_publish_limit(target_id)
            if limit_marker:
                raise RuntimeError(f"知乎发布受限: {limit_marker}")
            raise RuntimeError("未检测到知乎发布成功提示，请检查页面状态")

        page_state = "published"
        take_screenshot(target_id, "published")
        verify_in_management_list(target_id, title, is_draft=False)
        emit_smoke_state(target_id, smoke_step, page_state)
        print(f"[OK] 已发布到知乎: {article_path}")
    except Exception as exc:
        take_screenshot(target_id, "error")
        emit_smoke_state(target_id, smoke_step, page_state, error=exc)
        raise


if __name__ == "__main__":
    main()
