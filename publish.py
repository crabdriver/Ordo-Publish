from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from publish_console_state import (
    advance_after_success,
    build_session,
    finalize_article,
    mark_publishing,
    record_platform_result,
    save_session,
)
from scripts.format import build_gallery_bundle, render_publish_console_page
from ordo_engine.assignment.covers import COVER_PLATFORMS, CoverPoolError, assign_covers, list_cover_files
from ordo_engine.assignment.cover_contract import (
    CoverContractError,
    resolve_publication_cover,
    validate_cover,
)
from ordo_engine.assignment.templates import assign_templates
from ordo_engine.config import load_engine_config
from ordo_engine.platforms.base import classify_process_result
from ordo_engine.platforms.browser.node_runtime import resolve_node_executable
from ordo_engine.platforms.registry import build_platform_registry
from ordo_engine.results.publish_records import (
    MAX_RECORD_LOG_LENGTH,
    PUBLISH_RECORD_FIELDNAMES,
    append_publish_record_at_path,
)
from ordo_engine.models.workbench import CoverAssignment
from ordo_engine.runner.pipeline import run_platform_task, run_publish_pipeline


BASE_DIR = Path(__file__).resolve().parent
CDP_SCRIPT = BASE_DIR / "live_cdp.mjs"
CDP_RESOLVER_SCRIPT = BASE_DIR / "live_cdp_ws_resolver.mjs"
WORKBENCH_FILE = BASE_DIR / ".publish-workbench.json"
PUBLISH_OPTION_MODES = ("auto", "force_on", "force_off", "random")
COVERS_DIR = BASE_DIR / "covers"
PUBLISH_RECORDS_FILE = BASE_DIR / "publish_records.csv"
BROWSER_SESSION_DIR = BASE_DIR / ".ordo" / "browser-session"
BROWSER_SESSION_STATE_FILE = BROWSER_SESSION_DIR / "state.json"
PUBLISH_CONSOLE_DIR = BASE_DIR / ".ordo" / "publish-console"
PUBLISH_CONSOLE_HTML = PUBLISH_CONSOLE_DIR / "console.html"
PUBLISH_CONSOLE_SESSION = PUBLISH_CONSOLE_DIR / "publish-console-session.json"
CHROME_APP_CANDIDATES = [
    "Google Chrome",
    "Google Chrome Beta",
    "Google Chrome Dev",
    "Chromium",
]
WINDOWS_CHROME_CANDIDATES = [
    "chrome",
    "chrome.exe",
    "chromium",
    "chromium-browser",
]
LINUX_CHROME_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
]
PLATFORM_SCRIPTS = {
    "wechat": "wechat_publisher.py",
    "zhihu": "zhihu_publisher.py",
    "toutiao": "toutiao_publisher.py",
    "jianshu": "jianshu_publisher.py",
    "yidian": "yidian_publisher.py",
    "bilibili": "bilibili_publisher.py",
}
PLATFORM_URLS = {
    "zhihu": "https://zhuanlan.zhihu.com/write",
    "toutiao": "https://mp.toutiao.com/profile_v4/graphic/publish",
    "jianshu": "https://www.jianshu.com/writer#/",
    "yidian": "https://mp.yidianzixun.com/#/Writing/articleEditor",
    "bilibili": "https://member.bilibili.com/platform/upload/text/new-edit",
}
PLATFORM_MATCHES = {
    "zhihu": ["zhihu.com"],
    "toutiao": ["mp.toutiao.com"],
    "jianshu": ["jianshu.com/writer"],
    "yidian": ["mp.yidianzixun.com"],
    "bilibili": ["member.bilibili.com"],
}
BROWSER_PLATFORM_LABELS = {
    "zhihu": "知乎",
    "toutiao": "头条号",
    "jianshu": "简书",
    "yidian": "一点号",
    "bilibili": "哔哩哔哩",
}
DEFAULT_PLATFORMS = ["wechat", "zhihu", "toutiao", "jianshu", "yidian", "bilibili"]
BROWSER_PLATFORMS = list(PLATFORM_URLS.keys())
COVER_PLATFORMS_SET = frozenset(COVER_PLATFORMS)
def parse_platforms(raw_value):
    value = (raw_value or "all").strip().lower()
    if value == "all":
        return DEFAULT_PLATFORMS

    platforms = []
    for item in value.split(","):
        platform = item.strip()
        if not platform:
            continue
        if platform not in PLATFORM_SCRIPTS:
            supported = ", ".join(sorted(PLATFORM_SCRIPTS))
            raise ValueError(f"不支持的平台: {platform}，可选: {supported}, all")
        if platform not in platforms:
            platforms.append(platform)
    if not platforms:
        raise ValueError("至少要指定一个平台")
    return platforms


def collect_markdown_files(raw_path, offset=0, limit=None):
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"路径不存在: {path}")

    if path.is_file():
        files = [path]
    else:
        # 支持递归检索所有子目录中的 Markdown 文件，排除任何以 '.' 开头的隐藏目录或隐藏文件
        files = sorted(
            item for item in path.rglob("*.md")
            if item.is_file() and not any(part.startswith(".") for part in item.relative_to(path).parts)
        )

    if not files:
        raise ValueError(f"没有找到 Markdown 文件: {path}")

    if offset:
        files = files[offset:]
    if limit is not None:
        files = files[:limit]

    if not files:
        raise ValueError("筛选后没有可执行的 Markdown 文件")

    return files


def get_published_stems(dashboard_path: Path) -> set[str]:
    published_stems = set()
    if not dashboard_path.exists():
        return published_stems
    try:
        import re
        content = dashboard_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            if "|" not in line:
                continue
            is_published = "✅" in line or "已发" in line or "已发表" in line
            if is_published:
                match = re.search(r"AI终稿/(.+?)\.md", line)
                if match:
                    published_stems.add(match.group(1))
                else:
                    match_any = re.search(r"\((.+?)\.md\)", line)
                    if match_any:
                        published_stems.add(Path(match_any.group(1)).stem)
    except Exception as e:
        print(f"[WARN] 读取或解析看板失败: {e}")
    return published_stems


def filter_already_published_articles(article_paths: list[Path], markdown_path: str) -> list[Path]:
    dashboard_path = None
    env_doc_root = os.getenv("DOCUMENT_ROOT")
    if env_doc_root:
        candidate = Path(env_doc_root) / "AI智能创作" / "Ordo_Scribe_AI创作看板.md"
        if candidate.is_file():
            dashboard_path = candidate

    if not dashboard_path and markdown_path:
        p = Path(markdown_path).expanduser().resolve()
        start_dir = p.parent if p.is_file() else p
        curr = start_dir
        for _ in range(4):
            candidate = curr / "Ordo_Scribe_AI创作看板.md"
            if candidate.is_file():
                dashboard_path = candidate
                break
            candidate = curr / "AI智能创作" / "Ordo_Scribe_AI创作看板.md"
            if candidate.is_file():
                dashboard_path = candidate
                break
            if curr.parent == curr:
                break
            curr = curr.parent

    if not dashboard_path:
        print("[WARN] 未找到创作状态看板 Ordo_Scribe_AI创作看板.md，将不进行已发布文章过滤。")
        return article_paths

    print(f"[INFO] 找到创作状态看板: {dashboard_path}")
    published_stems = get_published_stems(dashboard_path)
    if not published_stems:
        return article_paths

    print(f"[INFO] 看板记录已发表文章数: {len(published_stems)}")
    filtered = []
    for p in article_paths:
        if p.stem in published_stems:
            print(f"[SKIP] 根据看板记录过滤已发布文章: {p.name}")
        else:
            filtered.append(p)
    return filtered


def maybe_filter_already_published_articles(
    article_paths: list[Path],
    markdown_path: str,
    *,
    skip_published: bool,
) -> list[Path]:
    if not skip_published:
        return article_paths
    return filter_already_published_articles(article_paths, markdown_path)


def load_simple_env_file(env_path):
    values = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_project_config():
    path = BASE_DIR / "config.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def get_wechat_config_status(base_dir=None):
    config = load_engine_config(base_dir or BASE_DIR, environ=os.environ)
    status = config.get_wechat_config_status()
    status["config_warning"] = config.project_config_warning
    return status


def get_page_text_snippet(target_id, limit=2000):
    expression = f"(() => (document.body.innerText || '').slice(0, {limit}))()"
    return run_cdp("eval", target_id, expression)


def inspect_browser_platform_state(platform, target_id):
    expressions = {
        "zhihu": """
(() => {
  const href = location.href;
  const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  const titleEl = document.querySelector('textarea[placeholder*="标题"], input[placeholder*="标题"]');
  const editor = document.querySelector('.public-DraftEditor-content, .ProseMirror, [data-lexical-editor="true"], [contenteditable="true"]');
  if (titleEl && editor) {
    return JSON.stringify({ current_url: href, page_state: 'editor_ready', editor_ready: true, detail: '写作编辑器已就绪' });
  }
  const isLoginUrl = href.includes('/signin') || href.includes('/login') || href.includes('/sign_in') || href.includes('/sign_up');
  if (isLoginUrl || document.querySelector('.SignContainer, .SignFlowHeader') || text.includes('密码登录') || text.includes('验证码登录') || text.includes('立即登录') || text.includes('扫码登录')) {
    return JSON.stringify({ current_url: href, page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (!href.includes('/write') && !href.includes('/creator')) {
    return JSON.stringify({ current_url: href, page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在知乎写作页' });
  }
  return JSON.stringify({ current_url: href, page_state: 'editor_missing', editor_ready: false, detail: '已进入知乎域名，但未检测到标题框或正文编辑器' });
})()
""".strip(),
        "toutiao": f"""
(() => {{
  const href = location.href;
  const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  const titleEl = document.querySelector('textarea[placeholder="请输入文章标题（2～30个字）"]');
  const editor = document.querySelector('.ProseMirror');
  if (titleEl && editor) {{
    return JSON.stringify({{ current_url: href, page_state: 'editor_ready', editor_ready: true, detail: '图文编辑器已就绪' }});
  }}
  const isLoginUrl = href.includes('/signin') || href.includes('/login') || href.includes('/sign_in') || href.includes('/sign_up');
  if (isLoginUrl || document.querySelector('.login-panel, .login-form, .s-login') || text.includes('密码登录') || text.includes('验证码登录') || text.includes('立即登录') || text.includes('扫码登录')) {{
    return JSON.stringify({{ current_url: href, page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' }});
  }}
  if (!href.startsWith({json.dumps(PLATFORM_URLS["toutiao"])}) && !href.includes('/graphic/publish')) {{
    return JSON.stringify({{ current_url: href, page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在头条号图文写作页' }});
  }}
  return JSON.stringify({{ current_url: href, page_state: 'editor_missing', editor_ready: false, detail: '已进入头条号发文域，但未检测到标题框或正文编辑器' }});
}})()
""".strip(),
        "yidian": """
(() => {
  const href = location.href;
  const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  const titleEl = document.querySelector("input.post-title");
  const editor = document.querySelector(".editor-content[contenteditable='true']");
  const canEnterEditor = !!document.querySelector('a.editor')
    || Array.from(document.querySelectorAll('a,button')).some((el) => {
      const value = (el.innerText || '').trim();
      const href = el.getAttribute && (el.getAttribute('href') || '');
      return value === '发文章' || value === '发布' || value === '再写一篇' || href === '#/Writing/articleEditor';
    });
  if (titleEl && editor) {
    return JSON.stringify({ current_url: href, page_state: 'editor_ready', editor_ready: true, detail: '一点号编辑器已就绪' });
  }
  const isLoginUrl = href.includes('/signin') || href.includes('/login') || href.includes('/sign_in') || href.includes('/sign_up') || href === 'https://mp.yidianzixun.com/#/' || href === 'https://mp.yidianzixun.com/';
  if (isLoginUrl || document.querySelector('.login-container, .login-box, .yidian-login-box') || text.includes('密码登录') || text.includes('验证码登录') || text.includes('立即登录') || text.includes('扫码登录')) {
    return JSON.stringify({ current_url: href, page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (canEnterEditor) {
    return JSON.stringify({ current_url: href, page_state: 'need_enter_editor', editor_ready: false, detail: '当前仍停留在内容管理或审核中视图，请先点“发文章/再写一篇”进入编辑器' });
  }
  if (!href.includes('/Writing/articleEditor')) {
    return JSON.stringify({ current_url: href, page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在一点号发文编辑页' });
  }
  return JSON.stringify({ current_url: href, page_state: 'editor_missing', editor_ready: false, detail: '已进入一点号发文页，但未检测到标题框或正文编辑器' });
})()
""".strip(),
        "jianshu": """
(() => {
  const href = location.href;
  const text = (document.body.innerText || '').replace(/\\s+/g, ' ').trim();
  const titleEl = document.querySelector('input._24i7u');
  const editor = document.querySelector('textarea._3swFR.source');
  if (titleEl && editor) {
    return JSON.stringify({ current_url: href, page_state: 'editor_ready', editor_ready: true, detail: '简书编辑器已就绪' });
  }
  const isLoginUrl = href.includes('/signin') || href.includes('/login') || href.includes('/sign_in') || href.includes('/sign_up');
  if (isLoginUrl || document.querySelector('.sign, .login-container') || text.includes('密码登录') || text.includes('验证码登录') || text.includes('立即登录') || text.includes('扫码登录')) {
    return JSON.stringify({ current_url: href, page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (!href.includes('jianshu.com')) {
    return JSON.stringify({ current_url: href, page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在简书域名' });
  }
  return JSON.stringify({ current_url: href, page_state: 'editor_missing', editor_ready: false, detail: '已进入简书域名，但未检测到标题框或正文编辑器' });
})()
""".strip(),
        "bilibili": """
(() => {
  const href = location.href;
  const iframe = document.querySelector('iframe[src*="read-editor"]');
  const doc = iframe?.contentDocument || iframe?.contentWindow?.document;
  const titleEl = doc?.querySelector('textarea.title-input__inner');
  const editor = doc?.querySelector('div.tiptap.ProseMirror');
  if (titleEl && editor) {
    return JSON.stringify({ current_url: href, page_state: 'editor_ready', editor_ready: true, detail: '哔哩哔哩编辑器已就绪' });
  }
  const isLoginUrl = href.includes('/login') || href.includes('/signin') || document.body.innerText.includes('登录');
  if (isLoginUrl) {
    return JSON.stringify({ current_url: href, page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (!href.includes('bilibili.com')) {
    return JSON.stringify({ current_url: href, page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在哔哩哔哩域名' });
  }
  return JSON.stringify({ current_url: href, page_state: 'editor_missing', editor_ready: false, detail: '已进入哔哩哔哩域名，但未检测到标题框或正文编辑器' });
})()
""".strip(),
    }
    expression = expressions.get(platform)
    if not expression:
        return {"current_url": "", "page_state": "unsupported", "editor_ready": False, "detail": f"平台 {platform} 暂不支持浏览器预检"}
    output = run_cdp("eval", target_id, expression, timeout=5)
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise ValueError(f"预检返回格式异常: {payload!r}")
    return payload


def _safe_article_stem(path: Path) -> str:
    stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in path.stem)
    return stem[:80] or "article"


def article_id_for_path(article_path: Path, index: int) -> str:
    return f"{index:04d}-{_safe_article_stem(article_path)}"


def discover_cover_pool_status(base_dir: Path, cover_dir_override: Optional[Path] = None):
    """Return discover_cover_pool()-shaped dict; optional cover_dir for tests."""
    if cover_dir_override is not None:
        cover_dir = Path(cover_dir_override).expanduser().resolve()
        try:
            files = list_cover_files(cover_dir)
        except CoverPoolError as exc:
            return {
                "ok": False,
                "cover_dir": str(cover_dir),
                "paths": [],
                "count": 0,
                "error": str(exc),
            }
        paths = [str(p) for p in files]
        return {
            "ok": True,
            "cover_dir": str(cover_dir),
            "paths": paths,
            "count": len(paths),
            "error": None,
        }
    return load_engine_config(base_dir).discover_cover_pool()


def build_template_assignments_for_articles(base_dir: Path, article_ids: tuple[str, ...]):
    ec = load_engine_config(base_dir)
    themes_dir = ec.resolve_themes_dir()
    if not themes_dir.is_dir() or not any(themes_dir.glob("*.json")):
        return ()
    return assign_templates(
        article_ids,
        themes_dir=themes_dir,
        assignment_mode=ec.get_default_template_mode(),
    )


def load_recent_cover_paths(limit: int) -> list[str]:
    if not PUBLISH_RECORDS_FILE.exists():
        return []
    try:
        import csv
        with open(PUBLISH_RECORDS_FILE, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            paths = []
            for row in reader:
                path = row.get("cover_path")
                if path:
                    paths.append(path)
            return paths[-limit:] if limit > 0 else []
    except Exception as exc:
        print(f"[WARN] 读取历史封面记录失败: {exc}")
        return []


def build_cover_assignments_for_articles(base_dir: Path, article_ids: tuple[str, ...], platforms: list[str]):
    ec = load_engine_config(base_dir)
    pool = ec.discover_cover_pool()
    if not pool["ok"]:
        return ()
    need = [p for p in platforms if p in COVER_PLATFORMS_SET]
    if not need:
        return ()
    repeat_window = ec.get_cover_repeat_window()
    recent_covers = load_recent_cover_paths(repeat_window)
    return assign_covers(
        article_ids,
        platforms,
        cover_dir=ec.resolve_cover_dir(),
        recent_cover_paths=recent_covers,
        repeat_window=repeat_window,
    )


def build_publication_cover_assignments(
    article_paths: list[Path],
    platforms: list[str],
    *,
    cover_override: Optional[Path] = None,
):
    manual_cover = validate_cover(cover_override) if cover_override is not None else None
    assignments = []
    for index, article_path in enumerate(article_paths):
        article = Path(article_path).expanduser().resolve()
        cover_path = manual_cover or resolve_publication_cover(article)
        article_id = article_id_for_path(article, index)
        source = "manual" if manual_cover is not None else "publication_package"
        for platform in platforms:
            assignments.append(
                CoverAssignment(
                    article_id=article_id,
                    platform=platform,
                    cover_path=cover_path,
                    cover_source=source,
                    is_random=False,
                    is_manual_override=manual_cover is not None,
                )
            )
    return tuple(assignments)


def normalize_publish_option_mode(value, *, field_name: str):
    mode = str(value or "auto")
    if mode not in PUBLISH_OPTION_MODES:
        raise ValueError(f"{field_name} 仅支持: auto / force_on / force_off / random")
    return mode


def build_publish_context_resolver(
    article_paths: list[Path],
    platforms: list[str],
    template_assignments,
    cover_assignments,
    *,
    cover_mode="auto",
    ai_declaration_mode="auto",
):
    path_to_id = {p.resolve(): article_id_for_path(p, i) for i, p in enumerate(article_paths)}
    tmpl_by_id = {a.article_id: a for a in template_assignments} if template_assignments else {}
    cover_by_pair = {}
    for ca in cover_assignments or ():
        cover_by_pair[(ca.article_id, ca.platform)] = ca.cover_path
    normalized_cover_mode = normalize_publish_option_mode(cover_mode, field_name="cover_mode")
    normalized_ai_declaration_mode = normalize_publish_option_mode(
        ai_declaration_mode, field_name="ai_declaration_mode"
    )

    def context_resolver(article_path, platform):
        aid = path_to_id.get(Path(article_path).resolve())
        if aid is None:
            return None
        blob = {
            "article_id": aid,
            "cover_mode": normalized_cover_mode,
            "ai_declaration_mode": normalized_ai_declaration_mode,
        }
        if platform != "wechat":
            ta = tmpl_by_id.get(aid)
            if ta:
                blob["template_mode"] = ta.template_mode
                blob["theme_name"] = ta.theme_id
        cover_path = cover_by_pair.get((aid, platform))
        if cover_path:
            blob["cover_path"] = str(cover_path)
        return blob

    return context_resolver


def describe_cdp_connection(payload):
    if not payload:
        return None
    source = payload.get("source")
    detail = payload.get("detail") or ""
    if source == "managed_browser_port":
        return detail or "当前 CDP 连接来源：Ordo 托管浏览器"
    if source == "managed_browser_port_file":
        return detail or "当前 CDP 连接来源：Ordo 托管浏览器资料目录"
    if source == "env_browser_ws_url":
        return "当前 CDP 连接来源：LIVE_CDP_BROWSER_WS_URL"
    if source == "env_live_cdp_port":
        return "当前 CDP 连接来源：LIVE_CDP_PORT"
    if source == "default_port_9222":
        return "当前 CDP 连接来源：默认调试端口 9222"
    if source == "windows_devtools_port_file":
        return f"当前 CDP 连接来源：{detail or 'LOCALAPPDATA/Google/Chrome/User Data/DevToolsActivePort'}"
    if source == "windows_chromium_port_file":
        return f"当前 CDP 连接来源：{detail or 'LOCALAPPDATA/Chromium/User Data/DevToolsActivePort'}"
    if source == "macos_devtools_port_file":
        return f"当前 CDP 连接来源：{detail or 'Library/Application Support/Google/Chrome/DevToolsActivePort'}"
    if source == "linux_devtools_port_file":
        return f"当前 CDP 连接来源：{detail or '~/.config/google-chrome/DevToolsActivePort'}"
    return f"当前 CDP 连接来源：{detail or source or '远程调试 Chrome'}"


def load_browser_session_settings(base_dir=None, environ=None):
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    env = dict(os.environ if environ is None else environ)
    return load_engine_config(root, environ=env).get_browser_session_settings()


def get_cdp_runtime_env(*, base_dir=None, environ=None):
    env = dict(os.environ if environ is None else environ)
    settings = load_browser_session_settings(base_dir=base_dir, environ=env)
    if settings.get("enabled"):
        env["LIVE_CDP_PORT"] = str(settings["debug_port"])
        env["ORDO_BROWSER_SESSION_DEBUG_PORT"] = str(settings["debug_port"])
        env["ORDO_BROWSER_SESSION_PROFILE_DIR"] = str(settings["profile_dir"])
    return env


def is_managed_browser_connection(payload):
    source = (payload or {}).get("source")
    return isinstance(source, str) and source.startswith("managed_browser")


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _browser_session_state_path(base_dir=None):
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    return root / ".ordo" / "browser-session" / "state.json"


def load_browser_session_state(base_dir=None):
    path = _browser_session_state_path(base_dir)
    settings = load_browser_session_settings(base_dir=base_dir)
    payload = {
        "mode": "managed" if settings.get("enabled") else "fallback_system_browser",
        "updated_at": None,
        "last_checked_at": None,
        "platforms": {},
    }
    if not path.exists():
        return payload
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return payload
    if isinstance(raw, dict):
        payload.update({key: raw.get(key) for key in ("mode", "updated_at", "last_checked_at") if key in raw})
        if isinstance(raw.get("platforms"), dict):
            payload["platforms"] = raw["platforms"]
    return payload


def save_browser_session_state(base_dir, payload):
    path = _browser_session_state_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_browser_session_mode(cdp_connection=None, base_dir=None):
    source = (cdp_connection or {}).get("source")
    if isinstance(source, str) and source.startswith("managed_browser"):
        return "managed"
    settings = load_browser_session_settings(base_dir=base_dir)
    return "fallback_system_browser" if settings.get("enabled") else "system_browser"


def _browser_session_requires_login(state):
    page_state = str(state.get("page_state") or "").lower()
    current_url = str(state.get("current_url") or "").lower()
    detail = str(state.get("detail") or "").lower()
    markers = ("login", "signin", "passport", "验证码", "登录")
    return (
        page_state in {"login_required", "expired_or_relogin_required", "captcha_required"}
        or any(marker in current_url for marker in ("login", "signin", "passport"))
        or any(marker.lower() in detail for marker in markers)
    )


def persist_browser_session_health(base_dir, platform, state, *, cdp_connection=None):
    payload = load_browser_session_state(base_dir)
    now = _now_iso()
    payload["mode"] = resolve_browser_session_mode(cdp_connection=cdp_connection, base_dir=base_dir)
    payload["updated_at"] = now
    payload["last_checked_at"] = now
    platforms = dict(payload.get("platforms") or {})
    platform_state = dict(platforms.get(platform) or {})
    platform_state["last_checked_at"] = now
    platform_state["current_url"] = str(state.get("current_url") or "")
    platform_state["page_state"] = str(state.get("page_state") or "")
    if _browser_session_requires_login(state):
        platform_state["status"] = "expired_or_relogin_required"
        platform_state["last_relogin_required_at"] = now
    elif state.get("editor_ready"):
        platform_state["status"] = "healthy"
        platform_state["last_healthy_at"] = now
    else:
        platform_state["status"] = str(platform_state.get("status") or "healthy")
    platforms[platform] = platform_state
    payload["platforms"] = platforms
    save_browser_session_state(base_dir, payload)
    return platform_state


def get_cdp_connection_metadata(base_dir=None):
    try:
        output = subprocess.run(
            [resolve_node_executable(), str(CDP_RESOLVER_SCRIPT), "--json"],
            cwd=str(BASE_DIR),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
            env=get_cdp_runtime_env(base_dir=base_dir),
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    if not output:
        return None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    detail = describe_cdp_connection(payload)
    if detail:
        return {"source": payload.get("source"), "detail": detail}
    return None


def run_preflight_checks(
    platforms,
    mode,
    workbench,
    base_dir=None,
    cover_dir_override=None,
    cdp_connection=None,
    cover_mode="auto",
    article_paths=None,
    cover_override=None,
):
    blockers = []
    warnings = []
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    override = Path(cover_dir_override).resolve() if cover_dir_override is not None else None
    normalized_cover_mode = normalize_publish_option_mode(cover_mode, field_name="cover_mode")

    if normalized_cover_mode != "force_off" and article_paths:
        try:
            if cover_override is not None:
                validate_cover(cover_override)
            else:
                for article_path in article_paths:
                    resolve_publication_cover(article_path)
        except CoverContractError as exc:
            blockers.append(f"统一封面预检失败: {exc}")

    if "wechat" in platforms:
        wechat = get_wechat_config_status(root)
        if override is not None:
            override_cover_pool = discover_cover_pool_status(root, cover_dir_override=override)
            if override_cover_pool.get("ok"):
                wechat = {
                    **wechat,
                    "covers_ready": True,
                    "cover_count": int(override_cover_pool.get("count") or 0),
                }
        if not wechat["appid_ready"] or not wechat["secret_ready"]:
            blockers.append("微信公众号缺少 `WECHAT_APPID` 或 `WECHAT_SECRET`，请先配置 `secrets.env`")
        else:
            # 实时检查微信外网 IP 白名单配置
            try:
                env = load_simple_env_file(root / "secrets.env")
                vps_ip = env.get("VPS_IP")
                if vps_ip:
                    # 在 VPS 上远程执行微信 Access Token 验证检查，检测白名单状态
                    vps_port = env.get("VPS_PORT", "22")
                    vps_user = env.get("VPS_USER", "root")
                    vps_ssh_key = env.get("VPS_SSH_KEY")
                    appid = env.get("WECHAT_APPID") or os.environ.get("WECHAT_APPID")
                    secret = env.get("WECHAT_SECRET") or os.environ.get("WECHAT_SECRET")

                    ssh_opts = ["-p", vps_port, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
                    if vps_ssh_key:
                        ssh_opts.extend(["-i", str(Path(vps_ssh_key).expanduser())])

                    test_cmd = (
                        f"cd ~/ordo-publish && "
                        f"unset WECHAT_PROXY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy && "
                        f"export ORDO_WORKER=1 && "
                        f"if [ -f .venv/bin/python ]; then python_bin=.venv/bin/python; else python_bin=python3; fi; "
                        f"$python_bin -c 'from wechat_publisher import WeChatPublisher; p = WeChatPublisher(\"{appid}\", \"{secret}\"); p.ensure_access_token()'"
                    )
                    chk_cmd = ["ssh"] + ssh_opts + [f"{vps_user}@{vps_ip}", test_cmd]
                    res = subprocess.run(chk_cmd, capture_output=True, text=True, timeout=30)
                    if res.returncode != 0:
                        err_msg = res.stderr.strip() or res.stdout.strip()
                        raise Exception(f"VPS 上的微信接口预检失败：{err_msg}")
                else:
                    blockers.append("微信公众号发布必须走 VPS：secrets.env 缺少 `VPS_IP`，已拒绝本地发送。")

            except Exception as exc:
                err_str = str(exc)
                if "IP白名单未配置" in err_str or "40164" in err_str:
                    import re
                    m = re.search(r"invalid ip ([\d.]+)", err_str)
                    ip_str = m.group(1) if m else "未知IP"
                    blockers.append(
                        f"微信公众号 IP 白名单校验失败！您的当前网络外网 IP 「{ip_str}」 未加入微信公众平台白名单。请先登录微信后台将该 IP 添加到白名单中！"
                    )
                else:
                    blockers.append(f"微信公众号 API 预检失败：{exc}")

        if not article_paths:
            if not wechat["covers_ready"] and not wechat["ai_cover_ready"]:
                blockers.append("微信公众号缺少可用封面：请提供合格的发布包 `cover.png` 或配置 AI 封面")
            elif not wechat["covers_ready"] and wechat["ai_cover_ready"]:
                warnings.append("未检测到发布包封面，当前将尝试 AI 封面生成能力")
        if wechat.get("config_warning"):
            warnings.append(str(wechat["config_warning"]))
    else:
        config = load_engine_config(root, environ=os.environ)
        if config.project_config_warning:
            warnings.append(config.project_config_warning)

    # 严格标题完整性审核与前缀过滤检查（拒绝发布带有无意义编号/日期的文章）
    if article_paths:
        import re
        from ordo_engine.importers.sources import import_file
        # 匹配无意义日期/期号/序号前缀，如 "20250728-01_"、"01_"、"1. "、"20250728." 等
        invalid_prefix_re = re.compile(r"^\s*\d{1,8}(?:[-_.]\d{1,4})*[-_.\s:：、，]+")
        for path in article_paths:
            try:
                draft = import_file(path)
                title = draft.title
                if invalid_prefix_re.search(title):
                    blockers.append(
                        f"文章标题审核失败！文件 《{path.name}》 解析出的发布标题为 《{title}》，"
                        f"其中依然含有未成功剥离的无意义数字/编号/日期前缀！发布已被拒绝阻断。"
                    )
            except Exception as e:
                warnings.append(f"文章 《{path.name}》 标题预检读取异常: {e}")

    browser_platforms = [
        platform for platform in platforms if platform in BROWSER_PLATFORMS
    ]
    if browser_platforms:
        from ordo_engine.platforms.playwright.engine import PlaywrightEngine

        try:
            engine = PlaywrightEngine(base_dir=root, headless=True)
            initialized = engine.profile_is_initialized
        except RuntimeError as exc:
            blockers.append(f"standalone 浏览器 profile 不安全：{exc}")
        else:
            if not initialized:
                blockers.append(
                    "standalone 浏览器 profile 尚未初始化；"
                    "请运行 publish.py --bootstrap-browser"
                )

    non_wechat_cover_platforms = [p for p in platforms if p in COVER_PLATFORMS_SET]
    if non_wechat_cover_platforms and not article_paths:
        if normalized_cover_mode == "force_off":
            return blockers, warnings
        pool_info = discover_cover_pool_status(root, cover_dir_override=override)
        if not pool_info["ok"]:
            label = "、".join(non_wechat_cover_platforms)
            detail = pool_info.get("error") or "封面池不可用"
            if normalized_cover_mode == "force_on":
                blockers.append(
                    f"当前已明确要求启用封面，但本地封面池不可用（目录: {pool_info.get('cover_dir', '')}）：{detail}。"
                    f"涉及平台: {label}"
                )
            else:
                msg = (
                    f"非微信平台自动分配封面需要可用本地封面池（目录: {pool_info.get('cover_dir', '')}）：{detail}。"
                    f"涉及平台: {label}"
                )
                if mode == "publish":
                    blockers.append(msg)
                else:
                    warnings.append(msg)

    return blockers, warnings


def run_platform(
    platform,
    markdown_file,
    mode,
    theme_name=None,
    cover_path=None,
    cover_mode=None,
    ai_declaration_mode=None,
    template_mode=None,
    article_id=None,
    scheduled_publish_at=None,
):
    registry = build_platform_registry(BASE_DIR)
    result = run_platform_task(
        base_dir=BASE_DIR,
        platform=platform,
        markdown_file=markdown_file,
        mode=mode,
        theme_name=theme_name,
        cover_path=cover_path,
        cover_mode=cover_mode,
        ai_declaration_mode=ai_declaration_mode,
        template_mode=template_mode,
        article_id=article_id,
        scheduled_publish_at=scheduled_publish_at,
        registry=registry,
    )
    result["script"] = PLATFORM_SCRIPTS[platform]
    return result


def classify_result(result):
    return classify_process_result(result["platform"], result.get("mode"), result)


def append_publish_record(result):
    append_publish_record_at_path(PUBLISH_RECORDS_FILE, result)


def print_result(result):
    platform = result["platform"]
    print(f"===== {platform} =====")
    if result["stdout"]:
        print(result["stdout"])
    if result["stderr"]:
        print(result["stderr"])
    print(f"[EXIT] {result['returncode']}")
    meta = {
        "article_id": result.get("article_id"),
        "theme_name": result.get("theme_name"),
        "template_mode": result.get("template_mode"),
        "cover_path": result.get("cover_path"),
        "platform": result["platform"],
        "status": result.get("status"),
        "error_type": result.get("error_type"),
        "current_url": result.get("current_url"),
        "page_state": result.get("page_state"),
        "smoke_step": result.get("smoke_step"),
    }
    print(f"[META] {json.dumps(meta, ensure_ascii=False)}")


# ── 失败通知回路 ──────────────────────────────────────────────
# 无人值守系统第一定律：失败必须能找到人。默认用 macOS 系统通知，
# 并支持可选 webhook（环境变量 ORDO_NOTIFY_WEBHOOK，或 config.notify.webhook）。

_ERROR_GUIDANCE = {
    "login_required": "有平台登录态失效，请手动登录后重跑",
    "platform_changed": "平台可能已改版，选择器失效，需更新适配器",
    "rate_limited": "触发平台限流，请稍后重试",
    "content_rejected": "内容被平台拒绝，请检查正文/封面合规性",
    "config_error": "配置错误，请检查 config.json",
    "environment_error": "运行环境异常（浏览器/依赖）",
}


def _mac_notify(title: str, message: str):
    """macOS 系统通知（无人值守下至少能在登录时看到）"""
    try:
        safe_title = title.replace('"', "'")
        safe_msg = message.replace('"', "'")
        script = f'display notification "{safe_msg}" with title "{safe_title}"'
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass


def _post_webhook(webhook: str, title: str, body: str, ok: bool):
    """可选：把结果推送到企业微信/飞书/自定义 webhook"""
    try:
        import urllib.request
        payload = json.dumps(
            {"title": title, "body": body, "ok": ok},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            webhook, data=payload, headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass


def _load_notify_webhook(base_dir) -> Optional[str]:
    # 环境变量优先，其次 config.json
    env = os.environ.get("ORDO_NOTIFY_WEBHOOK")
    if env:
        return env
    try:
        cfg, _ = load_engine_config(base_dir)
        return (cfg.get("notify") or {}).get("webhook")
    except Exception:
        return None


def notify_run_result(results, exit_code, base_dir=BASE_DIR):
    """发布结束后通知用户（系统通知 + 可选 webhook）"""
    failed = [r for r in results if r.get("returncode") != 0]
    ok = exit_code == 0 and not failed
    lines = []
    for r in results:
        status = r.get("status") or ("OK" if r.get("returncode") == 0 else "FAIL")
        line = f"- {r['platform']}: {status}"
        if not ok and r.get("returncode") != 0:
            et = r.get("error_type")
            guide = _ERROR_GUIDANCE.get(et, "请查看日志")
            line += f"  ⚠️ {guide}"
        lines.append(line)
    body = "\n".join(lines)

    if ok:
        title = "✅ 自动发布完成"
    else:
        title = f"⚠️ 自动发布失败（{len(failed)} 个平台）"

    _mac_notify(title, body)
    webhook = _load_notify_webhook(base_dir)
    if webhook:
        _post_webhook(webhook, title, body, ok)
    print(f"[NOTIFY] {title}")


def run_probe(args):
    """发布前选择器存活探针（子命令）"""
    from ordo_engine.platforms.playwright.probe import probe_platforms
    platforms = parse_platforms(args.platform)
    out = probe_platforms(platforms, headless=not getattr(args, "headed", False))
    broken = [k for k, v in out.items() if v["status"] in ("broken", "error")]
    if broken:
        print(f"\n⚠️ 以下平台选择器异常：{broken}")
        return 1
    print("\n✅ 所有平台选择器存活正常")
    return 0


def run_cdp(*args, timeout=120, base_dir=None):
    command = [resolve_node_executable(), str(CDP_SCRIPT), *args]
    return subprocess.run(
        command,
        cwd=str(BASE_DIR),
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
        env=get_cdp_runtime_env(base_dir=base_dir),
    ).stdout.strip()


def load_workbench_targets():
    if not WORKBENCH_FILE.exists():
        return {}
    try:
        return json.loads(WORKBENCH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[WARN] 解析 .publish-workbench.json 失败，使用空目标: {exc}")
        return {}


def save_workbench_targets(targets):
    WORKBENCH_FILE.write_text(
        json.dumps(targets, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def iter_chrome_launch_commands(urls, platform=None, browser_session=None):
    launch_urls = list(urls or [])
    target_platform = (platform or sys.platform).lower()
    session = browser_session or {}
    extra_args = []
    if session.get("enabled"):
        extra_args = [
            f"--user-data-dir={session['profile_dir']}",
            f"--remote-debugging-port={session['debug_port']}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
    if target_platform == "darwin":
        if extra_args:
            return [
                ["open", "-na", app_name, *launch_urls, "--args", *extra_args]
                for app_name in CHROME_APP_CANDIDATES
            ]
        return [["open", "-a", app_name, *launch_urls] for app_name in CHROME_APP_CANDIDATES]
    if target_platform.startswith("win"):
        return [["cmd", "/c", "start", "", browser, *extra_args, *launch_urls] for browser in WINDOWS_CHROME_CANDIDATES]
    return [[browser, *extra_args, *launch_urls] for browser in LINUX_CHROME_CANDIDATES]


def describe_chrome_launch_command(command):
    if len(command) >= 3 and command[:2] in (["open", "-a"], ["open", "-na"]):
        return command[2]
    if len(command) >= 5 and command[:3] == ["cmd", "/c", "start"]:
        return command[4]
    return command[0]


def launch_chrome(urls, base_dir=None):
    last_error = None
    browser_session = load_browser_session_settings(base_dir=base_dir)
    commands = iter_chrome_launch_commands(urls, browser_session=browser_session)
    for attempt in range(3):
        for command in commands:
            app_name = describe_chrome_launch_command(command)
            try:
                subprocess.run(
                    command,
                    cwd=str(BASE_DIR),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                return app_name
            except subprocess.CalledProcessError as exc:
                last_error = exc
        if last_error and attempt < 2:
            time.sleep(1)
    if last_error:
        raise RuntimeError("未找到可用的 Chrome/Chromium 应用，无法自动启动浏览器")
    raise RuntimeError("无法自动启动浏览器")


def list_tabs_or_none(base_dir=None):
    try:
        return list_tabs(base_dir=base_dir)
    except subprocess.CalledProcessError:
        return None


def ensure_chrome_ready(platforms, base_dir=None):
    browser_session = load_browser_session_settings(base_dir=base_dir)
    managed_required = bool(browser_session.get("enabled"))
    tabs = list_tabs_or_none(base_dir=base_dir)
    cdp_connection = get_cdp_connection_metadata(base_dir=base_dir) if managed_required else None
    if tabs is not None and (not managed_required or is_managed_browser_connection(cdp_connection)):
        return tabs, None

    if managed_required:
        raise RuntimeError(
            "未检测到 Ordo 托管浏览器 CDP，已阻止自动启动/回退到系统 Chrome。"
            f"请先启动专用浏览器端口 {browser_session.get('debug_port')}。"
        )

    urls = [PLATFORM_URLS[platform] for platform in platforms]
    app_name = launch_chrome(urls, base_dir=base_dir)

    deadline = time.time() + 20
    while time.time() < deadline:
        tabs = list_tabs_or_none(base_dir=base_dir)
        cdp_connection = get_cdp_connection_metadata(base_dir=base_dir) if managed_required else None
        if tabs is not None and (not managed_required or is_managed_browser_connection(cdp_connection)):
            return tabs, app_name
        time.sleep(1)

    if managed_required:
        raise RuntimeError(
            f"已尝试自动启动 {app_name}，但仍未切换到 Ordo 托管浏览器独立调试端口。"
            "请确认 Chrome 可以以独立 profile 启动。"
        )
    raise RuntimeError(
        f"已尝试自动启动 {app_name}，但仍无法连接 CDP。请确认 Chrome 远程调试已开启。"
    )


def list_tabs(base_dir=None):
    output = run_cdp("list", base_dir=base_dir)
    tabs = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        tabs.append({"target": parts[0], "title": parts[1], "url": parts[2]})
    return tabs


def platform_tab_exists(platform, tabs):
    return any(
        any(match in tab["url"] for match in PLATFORM_MATCHES[platform])
        for tab in tabs
    )


def find_platform_target(platform, tabs):
    return next(
        (
            tab["target"]
            for tab in tabs
            if any(match in tab["url"] for match in PLATFORM_MATCHES[platform])
        ),
        None,
    )


def bind_workbench(platforms, tabs):
    # Browser platforms must keep reusing the same logged-in tab whenever possible.
    # Do not casually switch to a freshly opened tab, because it may belong to a
    # different Chrome session and lose the working login / CDP authorization chain.
    existing = load_workbench_targets()
    live_targets = {tab["target"] for tab in tabs}
    updated = {
        platform: target
        for platform, target in existing.items()
        if platform not in BROWSER_PLATFORMS or target in live_targets
    }

    for platform in platforms:
        if platform not in BROWSER_PLATFORMS:
            continue
        existing_target = updated.get(platform)
        if existing_target and existing_target in live_targets:
            continue

        target = find_platform_target(platform, tabs)
        if target:
            updated[platform] = target

    save_workbench_targets(updated)
    return updated


def open_missing_platform_tabs(platforms, auto_launch=True):
    browser_platforms = [platform for platform in platforms if platform in BROWSER_PLATFORMS]
    if not browser_platforms:
        return []

    if auto_launch:
        tabs, launched_app = ensure_chrome_ready(browser_platforms)
    else:
        tabs = list_tabs_or_none()
        launched_app = None
    if launched_app:
        print(f"[INFO] 已自动启动浏览器: {launched_app}")
    if not tabs:
        raise RuntimeError("没有检测到可用的 Chrome 标签页，请先打开 Chrome 并启用远程调试")

    live_targets = {tab["target"] for tab in tabs}
    workbench = load_workbench_targets()
    base_target = None
    for platform in browser_platforms:
        target = workbench.get(platform)
        if target in live_targets:
            base_target = target
            break
    if not base_target:
        base_target = tabs[0]["target"]

    missing_platforms = [platform for platform in browser_platforms if not platform_tab_exists(platform, tabs)]
    if not missing_platforms:
        return []

    js = " ".join(
        [f"window.open({PLATFORM_URLS[platform]!r}, '_blank');" for platform in missing_platforms]
    ) + " 'opened';"
    run_cdp("eval", base_target, js)

    confirmed_tabs = tabs
    deadline = time.time() + 10
    while time.time() < deadline:
        latest_tabs = list_tabs_or_none()
        if latest_tabs:
            confirmed_tabs = latest_tabs
        remaining = [platform for platform in missing_platforms if not platform_tab_exists(platform, confirmed_tabs)]
        if not remaining:
            return missing_platforms
        time.sleep(1)
    return [platform for platform in missing_platforms if platform_tab_exists(platform, confirmed_tabs)]


def warm_platforms(platforms):
    workbench = load_workbench_targets()
    warmed = []
    for platform in platforms:
        if platform not in BROWSER_PLATFORMS:
            continue
        try:
            target = workbench.get(platform)
            if not target:
                tabs = list_tabs()
                target = find_platform_target(platform, tabs)
            if target:
                run_cdp("warm", target)
                warmed.append(platform)
        except subprocess.CalledProcessError:
            continue
    return warmed


def resolve_wechat_theme_mode(args, available_themes):
    if args.wechat_theme_mode == "console":
        raise RuntimeError(
            "wechat_theme_mode=console 已禁用；请改用 fixed/random"
        )
    if args.wechat_theme_mode == "fixed":
        return args.wechat_theme_mode
    return "random"


def resolve_wechat_theme_for_article(article_path, theme_mode, available_themes, fixed_theme):
    if theme_mode == "random" and available_themes:
        theme_name = random.choice(available_themes)
        print(f"  [INFO] 随机分配微信排版主题: {theme_name}")
        return theme_name
    return fixed_theme


def _safe_console_name(article_path, index):
    stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in article_path.stem)
    return f"{index + 1:03d}-{stem[:60] or 'article'}"


def _file_url(path):
    return path.resolve().as_uri() + f"?ts={int(time.time() * 1000)}"


def find_console_target(tabs):
    console_url = PUBLISH_CONSOLE_HTML.resolve().as_uri().split("?", 1)[0]
    return next((tab["target"] for tab in tabs if tab["url"].split("?", 1)[0] == console_url), None)


def ensure_console_target(auto_launch=True):
    tabs = list_tabs_or_none()
    if tabs is None:
        if not auto_launch:
            raise RuntimeError("没有检测到可用的 Chrome 标签页，请先打开已启用远程调试的 Chrome")
        tabs, launched_app = ensure_chrome_ready([])
        if launched_app:
            print(f"[INFO] 已自动启动浏览器: {launched_app}")
    if tabs is None:
        raise RuntimeError("没有检测到可用的 Chrome 标签页，请先打开已启用远程调试的 Chrome")

    target = find_console_target(tabs)
    console_url = _file_url(PUBLISH_CONSOLE_HTML)
    if target:
        run_cdp("nav", target, console_url)
        return target

    if tabs:
        fallback_target = tabs[0]["target"]
        run_cdp("nav", fallback_target, console_url)
        return fallback_target

    if not auto_launch:
        raise RuntimeError("未找到发布主控台标签页，且当前设置为 `--no-auto-launch`")

    launch_chrome([console_url])
    deadline = time.time() + 10
    while time.time() < deadline:
        tabs = list_tabs_or_none() or []
        target = find_console_target(tabs)
        if target:
            return target
        time.sleep(0.5)
    raise RuntimeError("发布主控台页面未能打开，请确认 Chrome 远程调试已开启")


def wait_for_console_ready(target_id, timeout_seconds=15):
    deadline = time.time() + timeout_seconds
    expression = (
        "(() => document.readyState === 'complete' && "
        "!!(window.publishConsole && window.publishConsole.setState))()"
    )
    while time.time() < deadline:
        try:
            result = run_cdp("eval", target_id, expression).strip().lower()
        except subprocess.CalledProcessError:
            result = ""
        if result == "true":
            return
        time.sleep(0.5)
    raise RuntimeError("发布主控台页面未就绪，请确认页面已成功打开")


def sync_console_state(target_id, session):
    payload = json.dumps(session, ensure_ascii=False)
    expression = (
        "(() => {"
        f"const nextState = {payload};"
        "if (window.publishConsole && window.publishConsole.setState) {"
        "  return window.publishConsole.setState(nextState);"
        "}"
        "window.__PUBLISH_CONSOLE_STATE__ = nextState;"
        "return 'missing-controller';"
        "})()"
    )
    return run_cdp("eval", target_id, expression)


def wait_for_console_confirmation(target_id, expected_index, timeout_seconds=None):
    expression = (
        "(() => (window.publishConsole && window.publishConsole.getAction "
        "? window.publishConsole.getAction() "
        ": (window.__PUBLISH_CONSOLE_ACTION__ || '')))()"
    )
    clear_expression = (
        "(() => (window.publishConsole && window.publishConsole.clearAction "
        "? window.publishConsole.clearAction() "
        ": (window.__PUBLISH_CONSOLE_ACTION__ = '', 'ok')))()"
    )
    deadline = time.time() + timeout_seconds if timeout_seconds else None
    while deadline is None or time.time() < deadline:
        raw = run_cdp("eval", target_id, expression).strip()
        if raw:
            action = json.loads(raw)
            if action.get("type") == "confirm" and action.get("article_index") == expected_index:
                run_cdp("eval", target_id, clear_expression)
                return action
        time.sleep(0.5)
    raise RuntimeError("等待主控台确认超时")


def run_console_queue(args, platforms, article_paths, available_themes):
    raise RuntimeError(
        "浏览器发布主控台已禁用；请改用 wechat_theme_mode=fixed/random"
    )

    # Legacy implementation retained temporarily for record compatibility.
    args_mode = getattr(args, "mode", "draft")
    args_wechat_theme = getattr(args, "wechat_theme", "chinese")
    args_no_auto_launch = getattr(args, "no_auto_launch", False)
    args_cover_mode = getattr(args, "cover_mode", "auto")
    args_cover = getattr(args, "cover", None)
    args_ai_declaration_mode = getattr(args, "ai_declaration_mode", "auto")

    session = build_session(
        article_paths=[str(path) for path in article_paths],
        platforms=platforms,
        mode=args_mode,
        available_themes=available_themes,
        default_theme=args_wechat_theme,
    )
    session["phase"] = "reviewing"
    session["notice"] = {
        "id": int(time.time() * 1000),
        "level": "info",
        "message": "请选择当前文章的微信模板，然后点击确认发布。",
    }
    save_session(PUBLISH_CONSOLE_SESSION, session)

    path_to_id = {p.resolve(): article_id_for_path(p, i) for i, p in enumerate(article_paths)}
    cover_assignments = ()
    if args_cover_mode != "force_off":
        cover_assignments = build_publication_cover_assignments(
            article_paths,
            platforms,
            cover_override=Path(args_cover).expanduser().resolve() if args_cover else None,
        )

    cover_by_pair = {}
    for ca in cover_assignments or ():
        cover_by_pair[(ca.article_id, ca.platform)] = ca.cover_path

    console_target = None
    results = []

    for index, article_path in enumerate(article_paths):
        item = session["items"][index]
        render_dir = PUBLISH_CONSOLE_DIR / _safe_console_name(article_path, index)
        bundle = build_gallery_bundle(
            input_path=article_path,
            vault_root=article_path.parent,
            output_dir=render_dir,
            theme_ids=available_themes,
        )
        item["title"] = bundle["title"]
        item["word_count"] = bundle["word_count"]
        session["current_index"] = index
        session["current_theme"] = item.get("selected_theme") or session["current_theme"]
        session["phase"] = "reviewing"
        session["notice"] = {
            "id": int(time.time() * 1000),
            "level": "info",
            "message": f"正在预览《{bundle['title']}》，请先确认微信模板。",
        }
        save_session(PUBLISH_CONSOLE_SESSION, session)
        render_publish_console_page(bundle, session, PUBLISH_CONSOLE_HTML)

        console_target = ensure_console_target(auto_launch=not args_no_auto_launch)
        wait_for_console_ready(console_target)
        sync_console_state(console_target, session)

        action = wait_for_console_confirmation(console_target, index)
        chosen_theme = action.get("theme") or args_wechat_theme
        item["selected_theme"] = chosen_theme
        session["current_theme"] = chosen_theme
        session["phase"] = "publishing"
        session["notice"] = {
            "id": int(time.time() * 1000),
            "level": "info",
            "message": f"已确认模板 {chosen_theme}，开始执行全平台发布。",
        }
        save_session(PUBLISH_CONSOLE_SESSION, session)
        sync_console_state(console_target, session)

        mark_publishing(session, index)
        save_session(PUBLISH_CONSOLE_SESSION, session)
        sync_console_state(console_target, session)

        print(f"===== article {index + 1}/{len(article_paths)} =====")
        print(article_path)
        print(f"[INFO] 主控台已确认微信模板: {chosen_theme}")

        aid = path_to_id.get(Path(article_path).resolve())
        for platform in platforms:
            theme_name = chosen_theme if platform == "wechat" else None
            cover_path = cover_by_pair.get((aid, platform))
            result = run_platform(
                platform,
                str(article_path),
                args_mode,
                theme_name=theme_name,
                cover_path=cover_path,
                cover_mode=args_cover_mode,
                ai_declaration_mode=args_ai_declaration_mode,
            )
            result["article"] = str(article_path)
            result["mode"] = args_mode
            result["status"] = classify_result(result)
            results.append(result)
            append_publish_record(result)
            print_result(result)

            record_platform_result(session, index, result)
            save_session(PUBLISH_CONSOLE_SESSION, session)
            sync_console_state(console_target, session)

        article_status = finalize_article(session, index)
        if article_status == "success":
            notice_level = "success"
            notice_message = f"《{item['title']}》已完成发布，准备进入下一篇。"
        elif article_status == "partial_failed":
            notice_level = "warn"
            notice_message = f"《{item['title']}》部分平台失败，已记录后继续下一篇。"
        else:
            notice_level = "warn"
            notice_message = f"《{item['title']}》全部平台发布失败，主控台已暂停。"

        session["phase"] = "reviewing"
        session["notice"] = {
            "id": int(time.time() * 1000),
            "level": notice_level,
            "message": notice_message,
        }
        save_session(PUBLISH_CONSOLE_SESSION, session)
        sync_console_state(console_target, session)

        if article_status == "failed":
            break

        if advance_after_success(session, index):
            save_session(PUBLISH_CONSOLE_SESSION, session)
            time.sleep(1.2)
            continue

    if session["items"]:
        final_item = session["items"][session["current_index"]]
        if final_item["status"] in {"success", "partial_failed"} and session["summary"]["completed_articles"] == len(session["items"]):
            session["phase"] = "complete"
            session["notice"] = {
                "id": int(time.time() * 1000),
                "level": "success",
                "message": (
                    f"全部文章处理完成：成功 {session['summary']['success_articles']} 篇，"
                    f"部分失败 {session['summary']['partial_failed_articles']} 篇，"
                    f"失败 {session['summary']['failed_articles']} 篇。"
                ),
            }
            save_session(PUBLISH_CONSOLE_SESSION, session)
            sync_console_state(console_target, session)

    return results


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Publish one or many Markdown articles to multiple platforms.")
    parser.add_argument("markdown_path", nargs="?", default=None,
                        help="Markdown 文件或目录路径（使用 --probe 时可省略）")
    parser.add_argument(
        "--probe",
        action="store_true",
        default=False,
        help="仅跑「选择器存活探针」：检查各平台关键选择器是否还在，不真正发布",
    )
    parser.add_argument(
        "--bootstrap-browser",
        action="store_true",
        default=False,
        help="启动仓库隔离 profile 的有头浏览器，人工登录确认后初始化自动发布环境",
    )
    parser.add_argument(
        "--platform",
        default="all",
        help="平台列表，逗号分隔，可选 wechat,zhihu,toutiao,jianshu,yidian 或 all",
    )
    parser.add_argument(
        "--mode",
        choices=["draft", "publish"],
        default="draft",
        help="draft 保存草稿；publish 正式发布",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="某个平台失败后继续执行后续平台",
    )
    parser.add_argument(
        "--no-auto-open",
        action="store_true",
        help="不自动补开缺失的平台标签页",
    )
    parser.add_argument(
        "--rebind-workbench",
        action="store_true",
        help="忽略旧的固定工作台绑定，按当前打开的标签页重新绑定",
    )
    parser.add_argument(
        "--no-auto-launch",
        action="store_true",
        help="检测不到浏览器/CDP 时不自动尝试启动 Chrome",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="不在执行前自动预热平台标签页",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="目录模式下最多处理多少篇文章",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="目录模式下跳过前多少篇文章",
    )
    parser.add_argument(
        "--wechat-theme",
        default=None,
        help="微信默认主题名，非交互模式下直接使用；默认从 config.json 获取，或回退到 chinese",
    )
    parser.add_argument(
        "--template-theme",
        default=None,
        help="非微信平台固定使用的主题名，例如 sspai",
    )
    parser.add_argument(
        "--wechat-theme-mode",
        choices=["auto", "random", "fixed", "console"],
        default=None,
        help="微信主题分配方式：auto/random 随机；fixed 固定使用 --wechat-theme；console 浏览器逐篇预览确认；默认从 config.json 获取，或回退到 auto",
    )
    parser.add_argument(
        "--cover-mode",
        choices=list(PUBLISH_OPTION_MODES),
        default="random",
        help="任务级封面策略：random 随机；auto 使用默认逻辑；force_on 强制要求封面；force_off 跳过封面设置",
    )
    parser.add_argument(
        "--cover",
        default=None,
        help="手动指定封面图路径，覆盖自动生成或默认选择的封面",
    )
    parser.add_argument(
        "--ai-declaration-mode",
        choices=list(PUBLISH_OPTION_MODES),
        default="auto",
        help="任务级 AI 声明策略：auto 使用默认逻辑；force_on 强制要求声明；force_off 跳过声明设置",
    )
    parser.add_argument(
        "--remote",
        choices=["local", "vps"],
        default=None,
        help="运行模式：local 本地直接执行；vps 上传到 VPS 异步托管执行",
    )
    parser.add_argument(
        "--vps-host",
        default=None,
        help="VPS 的 IP 地址或域名，默认读取 secrets.env 的 VPS_IP/VPS_HOST",
    )
    parser.add_argument(
        "--vps-user",
        default="root",
        help="SSH 登录 VPS 的用户名，默认 root",
    )
    parser.add_argument(
        "--vps-path",
        default="/root/ordo-publish",
        help="VPS 上的代码库绝对路径，默认 /root/ordo-publish",
    )
    parser.add_argument(
        "--skip-published",
        action="store_true",
        help="读取 Ordo_Scribe_AI创作看板.md，跳过看板中已发表的文章",
    )
    parser.add_argument(
        "--assume-yes",
        action="store_true",
        help="远端版本校验不一致时继续执行，适合非交互发布",
    )
    parser.add_argument(
        "--engine",
        choices=["playwright", "standalone"],
        default="standalone",
        help="自动化引擎：standalone (独立无头浏览器,默认) / playwright (CDP 连接模式)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        default=False,
        help="standalone 模式下使用有头浏览器（首次登录扫码时需要）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="standalone 模式下使用无头浏览器（默认行为，后台静默）",
    )
    args = parser.parse_args(argv)
    if args.remote is None:
        args.remote = "vps" if args.mode == "publish" else "local"
    return args


def bootstrap_browser_profile(
    base_dir,
    platforms,
    *,
    engine_factory=None,
    input_fn=input,
):
    """显式初始化仓库专用浏览器 profile；不会读取默认 Chrome profile。"""
    from ordo_engine.platforms.playwright.engine import PlaywrightEngine

    selected = [platform for platform in platforms if platform in BROWSER_PLATFORMS]
    if not selected:
        raise ValueError("--bootstrap-browser 至少需要一个浏览器平台")

    factory = engine_factory or PlaywrightEngine
    engine = factory(mode="standalone", headless=False, base_dir=Path(base_dir))
    confirmed = False
    try:
        engine.connect()
        for platform in selected:
            engine.get_page_for_platform(platform)
        answer = input_fn(
            "请在隔离浏览器中完成登录。确认全部完成后输入 YES："
        ).strip()
        if answer != "YES":
            raise RuntimeError("未收到明确确认，浏览器 profile 未标记为已初始化")
        confirmed = True
    finally:
        engine.close()

    if confirmed:
        engine.mark_profile_initialized()


def apply_vps_config_defaults(args, base_dir=BASE_DIR):
    if getattr(args, "remote", None) != "vps":
        return args
    env = load_simple_env_file(Path(base_dir) / "secrets.env")
    if not args.vps_host:
        args.vps_host = env.get("VPS_IP") or env.get("VPS_HOST")
    if not args.vps_user:
        args.vps_user = env.get("VPS_USER") or "root"
    if not args.vps_path:
        args.vps_path = env.get("VPS_PATH") or "/root/ordo-publish"
    return args


def require_local_standalone_engine(args, base_dir=BASE_DIR):
    if getattr(args, "remote", None) != "local":
        return
    if not any(
        platform in BROWSER_PLATFORMS
        for platform in parse_platforms(getattr(args, "platform", "all"))
    ):
        return
    if getattr(args, "engine", "standalone") != "standalone":
        raise SystemExit("[BLOCK] 本地浏览器发布仅允许 standalone 隔离引擎")
    configured = load_engine_config(base_dir).project_config.get("engine", "standalone")
    if configured != "standalone":
        raise SystemExit("[BLOCK] config.json 的本地浏览器引擎必须为 standalone")


def run_remote_cdp_preflight(remote_executor):
    cmd = [
        "bash",
        "-c",
        "export LIVE_CDP_PORT=${LIVE_CDP_PORT:-9334}; node live_cdp.mjs list >/dev/null",
    ]
    res = remote_executor.execute(cmd, timeout=30)
    if res.get("returncode") != 0:
        stderr = (res.get("stderr") or res.get("stdout") or "").strip()
        raise RuntimeError(f"VPS 浏览器/CDP 预检失败（端口 9334）。请先修复 VPS 登录态或浏览器。{stderr}")
    return res


def main():
    args = parse_args()
    if args.bootstrap_browser:
        bootstrap_browser_profile(BASE_DIR, parse_platforms(args.platform))
        return
    args = apply_vps_config_defaults(args)
    require_local_standalone_engine(args, BASE_DIR)

    # 选择器存活探针模式：只检查不发布
    if getattr(args, "probe", False):
        raise SystemExit(run_probe(args))

    # Load defaults from config.json if not specified
    config_defaults = {}
    try:
        engine_config = load_engine_config(BASE_DIR)
        config_defaults = engine_config.project_config.get("terminal_wizard", {}).get("defaults", {})
    except Exception as e:
        print(f"[WARN] 无法从 config.json 加载默认排版配置: {e}")

    if args.wechat_theme is None:
        args.wechat_theme = config_defaults.get("wechat_theme") or "chinese"
    if args.wechat_theme_mode is None:
        args.wechat_theme_mode = config_defaults.get("wechat_theme_mode") or "auto"

    platforms = parse_platforms(args.platform)
    if not args.markdown_path:
        raise SystemExit("[BLOCK] 缺少 Markdown 路径参数（或使用 --probe 仅跑探针）")
    article_paths = collect_markdown_files(args.markdown_path, offset=args.offset, limit=args.limit)
    article_paths = maybe_filter_already_published_articles(
        article_paths,
        args.markdown_path,
        skip_published=args.skip_published,
    )
    if not article_paths:
        print("[INFO] 所有文章均已发布，没有需要处理的文章。任务结束。")
        return

    if args.remote == "vps":
        if not args.vps_host:
            raise SystemExit("[BLOCK] VPS 发布缺少主机：请在 secrets.env 配置 VPS_IP，或传入 --vps-host")
        print(f"[INFO] 启用了远端托管模式 (VPS-First Pipeline)")
        print(f"[INFO] 远端主机: {args.vps_user}@{args.vps_host}")
        print(f"[INFO] 远端路径: {args.vps_path}")

        # 1. 校验本地与远端代码版本
        from ordo_engine.runner.version import verify_codebase_version
        match, local_commit, remote_commit = verify_codebase_version(
            ssh_host=args.vps_host,
            ssh_user=args.vps_user,
            remote_path=args.vps_path
        )
        if not match:
            print(f"\n[WARNING] 本地与远端代码版本不一致！")
            print(f"  本地 Commit: {local_commit}")
            print(f"  远端 Commit: {remote_commit}")
            print(f"  建议在 VPS 上执行 git pull 同步最新发布适配器代码。")
            if not args.assume_yes and not sys.stdin.isatty():
                raise SystemExit("[BLOCK] 本地与 VPS 代码版本不一致，自动发布已阻断。请先同步 VPS 代码。")
            ans = "y" if args.assume_yes else input("是否忽略版本差异继续发布？[y/N]: ").strip().lower()
            if ans != "y":
                print("[INFO] 任务已取消。")
                return
        else:
            print("[INFO] 代码版本校验通过，本地与远端完全一致。")

        from ordo_engine.runner.executor import RemoteSubprocessExecutor
        remote_executor = RemoteSubprocessExecutor(
            ssh_host=args.vps_host,
            ssh_user=args.vps_user,
            remote_cwd=args.vps_path,
            proxy_tunnel="7890:127.0.0.1:7890"
        )
        print("[INFO] 正在预检 VPS 浏览器/CDP 端口 9334...")
        run_remote_cdp_preflight(remote_executor)
        print("[INFO] VPS 浏览器/CDP 预检通过。")

        # 2. 本地统一封面预分配
        cover_assignments = ()
        if args.cover_mode != "force_off":
            cover_assignments = build_publication_cover_assignments(
                article_paths,
                platforms,
                cover_override=Path(args.cover).expanduser().resolve() if args.cover else None,
            )

        cover_mapping = {}
        for ca in cover_assignments:
            for idx, p in enumerate(article_paths):
                if article_id_for_path(p, idx) == ca.article_id:
                    cover_mapping.setdefault(p.stem, {})[ca.platform] = ca.cover_path

        theme_mapping = {}
        if args.template_theme:
            for p in article_paths:
                theme_mapping[p.stem] = {
                    platform: args.template_theme
                    for platform in platforms
                    if platform != "wechat"
                }

        # 3. 本地打包 Bundle
        from ordo_engine.runner.bundle import create_publish_bundle, upload_bundle_to_vps
        bundle_name = f"bundle_{int(time.time())}.zip"
        local_bundle_path = Path(tempfile.gettempdir()) / bundle_name

        print(f"[INFO] 正在本地生成任务包: {local_bundle_path}")
        create_publish_bundle(
            article_paths=article_paths,
            cover_mapping=cover_mapping,
            platforms=platforms,
            mode=args.mode,
            output_zip_path=local_bundle_path,
            theme_mapping=theme_mapping,
        )

        # 4. 上传任务包至 VPS
        remote_runtime_root = Path(args.vps_path).parent
        remote_inbox_path = str(remote_runtime_root / "data" / "inbox" / bundle_name)
        print(f"[INFO] 正在上传任务包至远端: {remote_inbox_path}")
        try:
            upload_bundle_to_vps(
                zip_path=local_bundle_path,
                remote_path=remote_inbox_path,
                ssh_host=args.vps_host,
                ssh_user=args.vps_user,
            )
            print("[SUCCESS] 任务包上传成功！")
        except Exception as e:
            print(f"[ERROR] 上传任务包失败: {e}")
            return
        finally:
            if local_bundle_path.exists():
                local_bundle_path.unlink()

        # 5. 在远端执行发布任务
        import shlex
        remote_cmd = [
            "bash",
            "-c",
            f"export LIVE_CDP_PORT=${{LIVE_CDP_PORT:-9334}}; if [ -f .venv/bin/python ]; then .venv/bin/python ordo_worker.py run-job {shlex.quote(remote_inbox_path)}; else python3 ordo_worker.py run-job {shlex.quote(remote_inbox_path)}; fi"
        ]

        print(f"\n[INFO] 正在远端启动发布任务，开启本地 Clash 代理反向隧道...")
        print("------------------- VPS 运行输出开始 -------------------")
        res = remote_executor.execute(remote_cmd, timeout=900)
        if res["stdout"]:
            print(res["stdout"])
        print("------------------- VPS 运行输出结束 -------------------")

        # 6. 清理远端 inbox 临时文件
        cleanup_cmd = ["rm", "-f", remote_inbox_path]
        cleanup_res = remote_executor.execute(cleanup_cmd, timeout=15)
        if cleanup_res["returncode"] == 0:
            print(f"[INFO] 已清理远端临时文件: {remote_inbox_path}")
        else:
            print(f"[WARN] 远端临时文件清理失败（可手动删除）: {remote_inbox_path}")

        if res["returncode"] == 0:
            print(f"\n[SUCCESS] 远端托管发布任务成功完成！")
        else:
            print(f"\n[ERROR] 远端托管发布任务失败，退出码: {res['returncode']}")
            if res["stderr"]:
                print(f"Stderr:\n{res['stderr']}")

            remote_output = "\n".join(filter(None, [res.get("stdout", ""), res.get("stderr", "")]))
            if "login_required" in remote_output or "登录已失效" in remote_output:
                print("\n" + "="*80)
                print("[WARNING] 检测到远端平台发文需要人工协助登录！")
                print("请单独在本地终端中运行以下命令建立安全调试隧道：")
                print(f"  ssh -N -L 9999:127.0.0.1:9333 {args.vps_user}@{args.vps_host}")
                print("然后在本地 Chrome 浏览器中访问：")
                print("  http://127.0.0.1:9999/json")
                print("或在本地 Chrome 中打开开发者工具 (DevTools) -> More tools -> Remote devices，添加 'localhost:9999'，并在本地打开对应域名的 Inspect 窗口，完成扫码登录或滑块验证。")
                print("安全验证完成后，请在本地通过再次运行发布命令，或在远端执行 `python3 ordo_worker.py resume` 来恢复任务。")
                print("="*80 + "\n")
            raise SystemExit(1)
        return

    results = []

    print(f"[INFO] 准备执行平台: {', '.join(platforms)}")
    print(f"[INFO] 模式: {args.mode}")
    print(f"[INFO] 本次文章数量: {len(article_paths)}")

    browser_platforms = [platform for platform in platforms if platform in BROWSER_PLATFORMS]
    if browser_platforms:
        print("[INFO] 使用 standalone 引擎：独立无头浏览器模式，跳过 CDP 前置检查")
    workbench = {}

    theme_mode = "fixed"
    available_themes = []
    if "wechat" in platforms:
        theme_dir = BASE_DIR / "themes"
        if theme_dir.exists():
            available_themes = sorted(f.stem for f in theme_dir.glob("*.json"))

        theme_mode = resolve_wechat_theme_mode(args, available_themes)

    if theme_mode == "console":
        results = run_console_queue(args, platforms, article_paths, available_themes)
        failed = [item for item in results if item["returncode"] != 0]
        succeeded = [item for item in results if item["returncode"] == 0]
        print("===== summary =====")
        print(f"成功: {len(succeeded)}")
        print(f"失败: {len(failed)}")
        if failed:
            raise SystemExit(1)
        return

    registry = build_platform_registry(BASE_DIR)
    theme_resolver = None
    if "wechat" in platforms:
        theme_resolver = lambda article_path: resolve_wechat_theme_for_article(  # noqa: E731
            article_path,
            theme_mode,
            available_themes,
            args.wechat_theme,
        )

    article_ids = tuple(article_id_for_path(p, i) for i, p in enumerate(article_paths))
    template_assignments = build_template_assignments_for_articles(BASE_DIR, article_ids)
    cover_assignments = ()
    if args.cover_mode != "force_off":
        cover_assignments = build_publication_cover_assignments(
            article_paths,
            platforms,
            cover_override=Path(args.cover).expanduser().resolve() if args.cover else None,
        )
    context_resolver = build_publish_context_resolver(
        article_paths,
        platforms,
        template_assignments,
        cover_assignments,
        cover_mode=args.cover_mode,
        ai_declaration_mode=args.ai_declaration_mode,
    )

    results, exit_code = run_publish_pipeline(
        base_dir=BASE_DIR,
        args=args,
        article_paths=article_paths,
        platforms=platforms,
        registry=registry,
        theme_resolver=theme_resolver,
        context_resolver=context_resolver,
        append_record=append_publish_record,
        printer=print_result,
    )

    failed = [item for item in results if item["returncode"] != 0]
    succeeded = [item for item in results if item["returncode"] == 0]

    print("===== summary =====")
    print(f"成功: {len(succeeded)}")
    print(f"失败: {len(failed)}")

    # 失败通知回路：无人值守下必须能触达人（系统通知 + 可选 webhook）
    notify_run_result(results, exit_code)

    if failed or exit_code:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
