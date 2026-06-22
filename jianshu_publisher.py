import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from markdown_utils import normalize_markdown_source, should_declare_ai
from ordo_engine.importers.normalize import strip_title_marker
from ordo_engine.platforms.browser.node_runtime import resolve_node_executable


BASE_DIR = Path(__file__).resolve().parent
CDP_SCRIPT = BASE_DIR / "live_cdp.mjs"
JIANSHU_MATCH = "jianshu.com"
JIANSHU_WRITER_URL = "https://www.jianshu.com/writer#/"
AI_KEYWORDS = ["AI创作", "AI辅助", "AIGC", "人工智能生成", "AI生成", "AI工具", "使用AI"]
PUBLISH_OPTION_MODES = ("auto", "force_on", "force_off", "random")


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
            if not (lower.startswith("http://") or lower.startswith("https://") or lower.startswith("data:image/")):
                continue
        cleaned_lines.append(raw_line)

    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned or markdown_text


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


def take_screenshot(target_id, step):
    if not target_id:
        return
    try:
        timestamp = int(time.time() * 1000)
        screenshot_dir = BASE_DIR / ".ordo" / "screenshots" / "jianshu"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        filepath = screenshot_dir / f"{timestamp}_{step}.png"
        run_cdp("shot", target_id, str(filepath))
        print(f"[INFO] [SCREENSHOT] Saved to: {filepath}")
    except Exception as e:
        print(f"[WARN] 截图失败 ({step}): {e}")


def verify_in_management_list(target_id, expected_title, is_draft=False):
    management_url = "https://www.jianshu.com/writer#/"
    print(f"[INFO] 正在跳转至简书写作管理页面进行校验: {management_url}")
    run_cdp("nav", target_id, management_url)
    title_json = json.dumps(expected_title, ensure_ascii=False)
    list_ready_expr = """
(() => {
  const normalize = (t) => (t || '').replace(/\\s+/g, '');
  const target = normalize(""" + title_json + """);
  const els = Array.from(document.querySelectorAll('ul._2TxA- span, ul._2TxA- li, a, div, span'));
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
        raise RuntimeError(f"双重校验失败：未能在简书文章列表中找到文章《{expected_title}》")
    print(f"[INFO] 双重校验成功：在文章列表中成功检索到文章《{expected_title}》！")


def list_jianshu_targets():
    output = run_cdp("list")
    targets = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        target_id, _title, url = parts[0], parts[1], parts[2]
        if JIANSHU_MATCH in url:
            targets.append(target_id)
    return targets


def editor_ready(target_id, timeout_seconds=2):
    return wait_until(
        target_id,
        "(() => !!document.querySelector('input._24i7u') && !!document.querySelector('textarea._3swFR.source'))()",
        timeout_seconds=timeout_seconds,
        interval_seconds=0.5,
    )


def find_jianshu_target():
    bound_target = os.environ.get("PUBLISH_TARGET_JIANSHU")
    if bound_target and editor_ready(bound_target, timeout_seconds=1):
        return bound_target

    targets = list_jianshu_targets()
    for target_id in targets:
        if target_id == bound_target:
            continue
        if editor_ready(target_id, timeout_seconds=1):
            return target_id

    return bound_target or (targets[0] if targets else None)


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

    body = strip_unsupported_local_images(body)
    return title, body, normalize_markdown_source(body), path


def wait_until(target_id, expression, timeout_seconds=20, interval_seconds=1):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = run_cdp("eval", target_id, expression)
        if result == "true":
            return True
        time.sleep(interval_seconds)
    return False


def ensure_writer_ready(target_id):
    run_cdp("nav", target_id, JIANSHU_WRITER_URL)
    ready = wait_until(
        target_id,
        "(() => !!document.querySelector('div._1GsW5') && !!document.querySelector('input._24i7u') && !!document.querySelector('textarea._3swFR.source'))()",
        timeout_seconds=30,
    )
    if not ready:
        raise RuntimeError("简书写作页未就绪，请确认当前 Chrome 已登录简书")


def create_new_article(target_id):
    expression = """
(() => {
  const btn = document.querySelector('div._1GsW5');
  if (!btn) return 'new-article-not-found';
  btn.click();
  return 'clicked';
})()
"""
    return run_cdp("eval", target_id, expression)


def current_url(target_id):
    return run_cdp("eval", target_id, "location.href")


def ensure_editor_panel(target_id, timeout_seconds=10):
    ready = editor_ready(target_id, timeout_seconds=timeout_seconds)
    if ready:
        return True

    # 简书偶发停留在列表态，刷新当前 note 路由后编辑器才会挂载出来。
    run_cdp("nav", target_id, current_url(target_id))
    if editor_ready(target_id, timeout_seconds=timeout_seconds):
        return True

    # 新建后的空白文章有时只出现在左侧列表，需要补点当前高亮的新记录进入编辑态。
    click_result = run_cdp(
        "eval",
        target_id,
        """
(() => {
  const item = document.querySelector('ul._2TxA- li._25Ilv._33nt7') || document.querySelector('ul._2TxA- li._25Ilv');
  if (!item) return 'note-not-found';
  item.click();
  return 'clicked';
})()
""".strip(),
    )
    if click_result != "clicked":
        return False

    return editor_ready(target_id, timeout_seconds=timeout_seconds)


def editor_content_ready(target_id, expected_title, timeout_seconds=20, interval_seconds=1):
    title_json = json.dumps(expected_title, ensure_ascii=False)
    expression = f"""
(() => {{
  const titleEl = document.querySelector('input._24i7u');
  const source = document.querySelector('textarea._3swFR.source');
  const title = (titleEl?.value || '').trim();
  const bodyLength = (source?.value || '').trim().length;
  const pageText = document.body.innerText || '';
  return (
    pageText.includes('已保存') ||
    (title === {title_json} && bodyLength > 20)
  );
}})()
""".strip()
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def inject_article(target_id, title, source_html):
    title_json = json.dumps(title, ensure_ascii=False)
    source_json = json.dumps(source_html, ensure_ascii=False)
    expression = f"""
(() => {{
  const titleInput = document.querySelector('input._24i7u');
  const source = document.querySelector('textarea._3swFR.source');
  if (!titleInput || !source) {{
    return 'missing-editor';
  }}

  const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  if (inputSetter) {{
    inputSetter.call(titleInput, {title_json});
  }} else {{
    titleInput.value = {title_json};
  }}
  titleInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
  titleInput.dispatchEvent(new Event('change', {{ bubbles: true }}));

  const textSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
  if (textSetter) {{
    textSetter.call(source, {source_json});
  }} else {{
    source.value = {source_json};
  }}
  source.scrollIntoView({{ block: 'center' }});
  source.focus();
  source.selectionStart = 0;
  source.selectionEnd = source.value.length;
  source.dispatchEvent(new Event('input', {{ bubbles: true }}));
  source.dispatchEvent(new Event('change', {{ bubbles: true }}));
  source.dispatchEvent(new KeyboardEvent('keyup', {{ bubbles: true }}));
  source.dispatchEvent(new Event('blur', {{ bubbles: true }}));

  // 再次回写标题，避免焦点串位时正文内容污染标题输入框。
  if (inputSetter) {{
    inputSetter.call(titleInput, {title_json});
  }} else {{
    titleInput.value = {title_json};
  }}
  titleInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
  titleInput.dispatchEvent(new Event('change', {{ bubbles: true }}));

  return JSON.stringify({{
    title: titleInput.value.trim(),
    sourceLength: source.value.length
  }});
}})()
"""
    return run_cdp("eval", target_id, expression)


def click_text(target_id, text):
    text_json = json.dumps(text, ensure_ascii=False)
    expression = f"""
(() => {{
  const candidates = Array.from(document.querySelectorAll('button, a, div, span')).filter(el => (el.innerText || '').trim() === {text_json});
  const el = candidates[0];
  if (!el) return 'not-found';
  el.click();
  return 'clicked';
}})()
"""
    return run_cdp("eval", target_id, expression)


def click_publish_article(target_id):
    ready = wait_until(
        target_id,
        """
(() => {
  const isVisible = (node) => {
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  return Array.from(document.querySelectorAll('a,button')).some(
    node => (node.innerText || '').replace(/\\s+/g, '') === '发布文章' && isVisible(node)
  ) || !!document.querySelector('a[data-action="publicize"]');
})()
""".strip(),
        timeout_seconds=10,
        interval_seconds=1,
    )
    if not ready:
        raise RuntimeError("简书发布按钮未就绪")

    click_result = run_cdp(
        "eval",
        target_id,
        """
(() => {
  const isVisible = (node) => {
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const el = Array.from(document.querySelectorAll('a,button')).find(
      node => (node.innerText || '').replace(/\\s+/g, '') === '发布文章' && isVisible(node)
    )
    || document.querySelector('a[data-action="publicize"]');
  if (!el) return 'publish-not-found';
  ['mouseover', 'mousedown', 'mouseup', 'click'].forEach(type => {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
  });
  return 'clicked';
})()
""".strip(),
    )
    if click_result != "clicked":
        raise RuntimeError(f"点击简书发布按钮失败: {click_result}")


def published_state_visible(target_id, timeout_seconds=5, interval_seconds=1):
    return wait_until(
        target_id,
        "(() => ['发布成功，点击查看文章', '取消发布', '公开文章发布失败', '每天只能发布 2 篇公开文章', '达到发布上限', '请明天再来'].some(text => (document.body.innerText || '').includes(text)))()",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def wait_for_text(target_id, text, timeout_seconds=20, interval_seconds=1):
    text_json = json.dumps(text, ensure_ascii=False)
    expression = f"(() => (document.body.innerText || '').includes({text_json}))()"
    return wait_until(target_id, expression, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)


def attempt_ai_declaration(target_id):
    keywords_json = json.dumps(AI_KEYWORDS, ensure_ascii=False)
    js = (
        "(() => {"
        "  const keywords = " + keywords_json + ";"
        "  const selectors = 'label, input[type=checkbox], span, div, button, [role=checkbox], [role=switch]';"
        "  const els = Array.from(document.querySelectorAll(selectors));"
        "  const candidates = els.filter(el => {"
        "    const txt = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim();"
        "    return keywords.some(k => txt.includes(k));"
        "  });"
        "  if (candidates.length === 0) return JSON.stringify({found: false});"
        "  const el = candidates[0];"
        "  let checked = false;"
        "  try {"
        "    if (el.tagName === 'INPUT' && el.type === 'checkbox') {"
        "      if (!el.checked) { el.click(); }"
        "      checked = el.checked;"
        "    } else if (el.querySelector && el.querySelector('input[type=checkbox]')) {"
        "      const cb = el.querySelector('input[type=checkbox]');"
        "      if (!cb.checked) { cb.click(); }"
        "      checked = cb.checked;"
        "    } else if (el.getAttribute('role') === 'checkbox' || el.getAttribute('role') === 'switch') {"
        "      el.click();"
        "      checked = el.getAttribute('aria-checked') === 'true';"
        "    } else {"
        "      el.click();"
        "      checked = true;"
        "    }"
        "  } catch(e) { checked = false; }"
        "  return JSON.stringify({found: true, checked: checked, tag: el.tagName, text: (el.innerText || '').trim().substring(0, 50)});"
        "})()"
    )
    try:
        raw = run_cdp("eval", target_id, js)
        result = json.loads(raw)
    except Exception:
        print("[WARN] 简书未发现 AI 创作声明入口，跳过")
        return None

    if not result.get("found"):
        print("[WARN] 简书未发现 AI 创作声明入口，跳过")
        return None

    if result.get("checked"):
        print(f"[INFO] 简书 AI 创作声明已勾选: {result}")
    else:
        print(f"[WARN] 简书 AI 创作声明勾选可能未生效: {result}")

    return result


def apply_jianshu_cover(target_id, cover_path):
    print(f"[INFO] 正在为简书上传封面/插入头部图片: {cover_path}")
    # 1. Click upload button
    click_expression = """
(() => {
  const btn = document.querySelector('a.fa.fa-picture-o');
  if (btn) {
    btn.click();
    return 'clicked';
  }
  return 'not-found';
})()
"""
    click_res = run_cdp("eval", target_id, click_expression)
    if click_res != "clicked":
        raise RuntimeError("未找到简书编辑器图片上传按钮 `a.fa.fa-picture-o`")

    # 2. Wait for input#kalamu-upload-image[type=file]
    input_ready = wait_until(
        target_id,
        "(() => !!document.querySelector('input#kalamu-upload-image'))()",
        timeout_seconds=5
    )
    if not input_ready:
        raise RuntimeError("未在页面中找到简书图片上传文件控件 `input#kalamu-upload-image`")

    # 3. Upload file using setfile
    print("[INFO] 向 input#kalamu-upload-image 注入封面文件路径 …")
    setfile_res = run_cdp("setfile", target_id, "input#kalamu-upload-image", str(cover_path))
    print(f"[INFO] setfile 注入结果: {setfile_res}")

    # 4. Poll textarea value for the inserted Markdown link.
    print("[INFO] 等待简书图片上传完成并生成 CDN 链接 …")
    uploaded_link = None
    for _ in range(40):
        val = run_cdp("eval", target_id, "(() => document.querySelector('textarea._3swFR.source')?.value || '')()")
        match = re.search(r"!\[.*?\]\(https?://upload-images\.jianshu\.io/upload_images/[^)]+\)", val)
        if match:
            uploaded_link = match.group(0)
            break
        time.sleep(0.5)

    if not uploaded_link:
        raise RuntimeError("上传封面超时，简书未返回 CDN 图片链接")

    print(f"[INFO] 成功捕获简书封面 CDN 链接: {uploaded_link}")

    # 5. Remove the link from its current position and prepend it to the top.
    reposition_js = f"""
(() => {{
  const source = document.querySelector('textarea._3swFR.source');
  if (!source) return 'missing-textarea';
  const val = source.value;
  const linkText = {json.dumps(uploaded_link, ensure_ascii=False)};

  let cleaned = val.replace(linkText, '');
  cleaned = cleaned.trim();
  const newVal = linkText + '\\n\\n' + cleaned;

  const textSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
  if (textSetter) {{
    textSetter.call(source, newVal);
  }} else {{
    source.value = newVal;
  }}

  source.dispatchEvent(new Event('input', {{ bubbles: true }}));
  source.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return 'ok';
}})()
"""
    repo_res = run_cdp("eval", target_id, reposition_js)
    print(f"[INFO] 封面图重置首行状态: {repo_res}")
    return uploaded_link


def main():
    parser = argparse.ArgumentParser(description="Publish Markdown article to Jianshu using live Chrome.")
    parser.add_argument("markdown_file", help="Markdown article path")
    parser.add_argument(
        "--mode",
        choices=["draft", "publish"],
        default="draft",
        help="draft 新建并等待自动保存；publish 直接发布",
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
    _ = (args.theme, args.template_mode, args.article_id, args.cover_mode, args.ai_declaration_mode)

    if args.cover_mode == "force_on" and not args.cover:
        raise RuntimeError("简书已要求启用封面，但当前任务没有可用封面路径")

    title, _body, source_html, article_path = load_article(args.markdown_file)
    target_id = find_jianshu_target()
    if not target_id:
        raise RuntimeError("没有找到简书标签页，请先在当前 Chrome 中打开并登录简书写作页")

    ensure_writer_ready(target_id)
    create_result = create_new_article(target_id)
    if create_result != "clicked":
        raise RuntimeError(f"新建简书文章失败: {create_result}")

    ready = ensure_editor_panel(target_id, timeout_seconds=12)
    if not ready:
        raise RuntimeError("简书新建文章后编辑器未就绪")

    inject_result = inject_article(target_id, title, source_html)
    if inject_result == "missing-editor":
        ensure_editor_panel(target_id, timeout_seconds=10)
        time.sleep(1)
        inject_result = inject_article(target_id, title, source_html)
    if inject_result == "missing-editor":
        raise RuntimeError("简书编辑器注入失败：未找到标题或正文输入区")
    try:
        inject_info = json.loads(inject_result)
    except json.JSONDecodeError:
        inject_info = None
    if inject_info and (inject_info.get("sourceLength", 0) == 0 or "<p>" in inject_info.get("title", "")):
        time.sleep(1)
        inject_result = inject_article(target_id, title, source_html)
        inject_info = json.loads(inject_result) if inject_result != "missing-editor" else None
    if inject_info and (inject_info.get("sourceLength", 0) == 0 or "<p>" in inject_info.get("title", "")):
        raise RuntimeError(f"简书正文/标题注入异常: {inject_result}")
    print(f"[INFO] 已写入简书编辑器: {inject_result}")

    if args.cover_mode != "force_off" and args.cover:
        apply_jianshu_cover(target_id, args.cover)
        take_screenshot(target_id, "cover_ready")

    need_ai_declaration = should_declare_ai(title, source_html, args.ai_declaration_mode)
    if need_ai_declaration:
        attempt_ai_declaration(target_id)
    else:
        print("[INFO] 跳过或已显式关闭简书 AI 创作声明设置")
    take_screenshot(target_id, "ai_declared")

    if args.mode == "draft":
        if not editor_content_ready(target_id, title, timeout_seconds=20):
            raise RuntimeError("未检测到简书自动保存提示")
        take_screenshot(target_id, "draft_saved")
        verify_in_management_list(target_id, title, is_draft=True)
        print(f"[OK] 已生成简书草稿: {article_path}")
        return

    if not editor_content_ready(target_id, title, timeout_seconds=20):
        raise RuntimeError("简书正文尚未保存完成，暂不发布")

    take_screenshot(target_id, "before_publish")
    click_publish_article(target_id)
    if not published_state_visible(target_id, timeout_seconds=4, interval_seconds=1):
        click_publish_article(target_id)

    if wait_until(
        target_id,
        "(() => Array.from(document.querySelectorAll('button,a,div,span')).some(el => ['直接发布','确定发布'].includes((el.innerText || '').replace(/\\s+/g, ''))))()",
        timeout_seconds=8,
    ):
        confirm_result = run_cdp(
            "eval",
            target_id,
            """
(() => {
  const el = Array.from(document.querySelectorAll('button,a,div,span')).find(
    node => ['直接发布', '确定发布'].includes((node.innerText || '').replace(/\\s+/g, ''))
  );
  if (!el) return 'not-found';
  el.click();
  return 'clicked';
})()
""".strip(),
        )
        if confirm_result != "clicked":
            raise RuntimeError(f"点击简书确认发布失败: {confirm_result}")

    state_changed = wait_until(
        target_id,
        "(() => ['发布中...', '已发布', '发布成功，点击查看文章', '取消发布', '公开文章发布失败', '每天只能发布 2 篇公开文章', '达到发布上限', '请明天再来'].some(text => (document.body.innerText || '').includes(text)))()",
        timeout_seconds=20,
    )
    if not state_changed:
        raise RuntimeError("未检测到简书发布后的页面变化，请检查页面状态")

    if wait_for_text(target_id, "每天只能发布 2 篇公开文章", timeout_seconds=2) or wait_for_text(target_id, "达到发布上限", timeout_seconds=2):
        print(f"[WARN] 简书今日公开文章发布次数已达上限，已自动保留为草稿箱草稿")
        verify_in_management_list(target_id, title, is_draft=True)
        print(f"[OK] 已生成简书草稿: {article_path}")
        return

    take_screenshot(target_id, "published")
    verify_in_management_list(target_id, title, is_draft=False)
    print(f"[OK] 已发布到简书: {article_path}")


if __name__ == "__main__":
    main()
