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
from tiandi_engine.assignment.covers import COVER_PLATFORMS, CoverPoolError, assign_covers, list_cover_files
from tiandi_engine.assignment.templates import assign_templates
from tiandi_engine.config import load_engine_config
from tiandi_engine.platforms.base import classify_process_result
from tiandi_engine.platforms.browser.node_runtime import resolve_node_executable
from tiandi_engine.platforms.registry import build_platform_registry
from tiandi_engine.results.publish_records import (
    MAX_RECORD_LOG_LENGTH,
    PUBLISH_RECORD_FIELDNAMES,
    append_publish_record_at_path,
)
from tiandi_engine.runner.pipeline import run_platform_task, run_publish_pipeline


BASE_DIR = Path(__file__).resolve().parent
CDP_SCRIPT = BASE_DIR / "live_cdp.mjs"
CDP_RESOLVER_SCRIPT = BASE_DIR / "live_cdp_ws_resolver.mjs"
WORKBENCH_FILE = BASE_DIR / ".publish-workbench.json"
PUBLISH_OPTION_MODES = ("auto", "force_on", "force_off")
COVERS_DIR = BASE_DIR / "covers"
PUBLISH_RECORDS_FILE = BASE_DIR / "publish_records.csv"
BROWSER_SESSION_DIR = BASE_DIR / ".tiandidistribute" / "browser-session"
BROWSER_SESSION_STATE_FILE = BROWSER_SESSION_DIR / "state.json"
PUBLISH_CONSOLE_DIR = BASE_DIR / ".tiandidistribute" / "publish-console"
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
}
PLATFORM_URLS = {
    "zhihu": "https://zhuanlan.zhihu.com/write",
    "toutiao": "https://mp.toutiao.com/profile_v4/graphic/publish",
    "jianshu": "https://www.jianshu.com/writer#/",
    "yidian": "https://mp.yidianzixun.com/#/Writing/articleEditor",
}
PLATFORM_MATCHES = {
    "zhihu": ["zhihu.com"],
    "toutiao": ["mp.toutiao.com"],
    "jianshu": ["jianshu.com/writer"],
    "yidian": ["mp.yidianzixun.com"],
}
BROWSER_PLATFORM_LABELS = {
    "zhihu": "知乎",
    "toutiao": "头条号",
    "jianshu": "简书",
    "yidian": "一点号",
}
DEFAULT_PLATFORMS = ["wechat", "zhihu", "toutiao", "jianshu", "yidian"]
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
        files = sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() == ".md")

    if not files:
        raise ValueError(f"没有找到 Markdown 文件: {path}")

    if offset:
        files = files[offset:]
    if limit is not None:
        files = files[:limit]

    if not files:
        raise ValueError("筛选后没有可执行的 Markdown 文件")

    return files


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
  if (text.includes('登录') || text.includes('验证码')) {
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
  if (text.includes('登录') || text.includes('验证码')) {{
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
  if (text.includes('登录') || text.includes('验证码')) {
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
  if (text.includes('登录') || text.includes('验证码')) {
    return JSON.stringify({ current_url: href, page_state: 'login_required', editor_ready: false, detail: '当前标签页仍处于登录或校验状态' });
  }
  if (!href.includes('jianshu.com')) {
    return JSON.stringify({ current_url: href, page_state: 'wrong_editor_page', editor_ready: false, detail: '当前标签页不在简书域名' });
  }
  return JSON.stringify({ current_url: href, page_state: 'editor_missing', editor_ready: false, detail: '已进入简书域名，但未检测到标题框或正文编辑器' });
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


def build_cover_assignments_for_articles(base_dir: Path, article_ids: tuple[str, ...], platforms: list[str]):
    ec = load_engine_config(base_dir)
    pool = ec.discover_cover_pool()
    if not pool["ok"]:
        return ()
    need = [p for p in platforms if p in COVER_PLATFORMS_SET]
    if not need:
        return ()
    return assign_covers(
        article_ids,
        platforms,
        cover_dir=ec.resolve_cover_dir(),
        recent_cover_paths=(),
        repeat_window=ec.get_cover_repeat_window(),
    )


def normalize_publish_option_mode(value, *, field_name: str):
    mode = str(value or "auto")
    if mode not in PUBLISH_OPTION_MODES:
        raise ValueError(f"{field_name} 仅支持: auto / force_on / force_off")
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
    return root / ".tiandidistribute" / "browser-session" / "state.json"


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
):
    blockers = []
    warnings = []
    root = Path(base_dir).resolve() if base_dir is not None else BASE_DIR
    override = Path(cover_dir_override).resolve() if cover_dir_override is not None else None
    normalized_cover_mode = normalize_publish_option_mode(cover_mode, field_name="cover_mode")

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
        if not wechat["covers_ready"] and not wechat["ai_cover_ready"]:
            blockers.append("微信公众号缺少可用封面：请配置 AI 封面所需的 `config.json`，或准备 `covers/cover_*.png`")
        elif not wechat["covers_ready"] and wechat["ai_cover_ready"]:
            warnings.append("未检测到本地默认封面，当前将默认优先使用 AI 封面生成能力")
        if wechat.get("config_warning"):
            warnings.append(str(wechat["config_warning"]))
    else:
        config = load_engine_config(root, environ=os.environ)
        if config.project_config_warning:
            warnings.append(config.project_config_warning)

    if mode == "publish" and "jianshu" in platforms:
        jianshu_target = workbench.get("jianshu")
        if jianshu_target:
            try:
                body = get_page_text_snippet(jianshu_target, limit=3000)
                if "每天只能发布 2 篇公开文章" in body:
                    blockers.append("简书今天已达到公开文章发布上限（每天最多 2 篇）")
            except subprocess.CalledProcessError:
                warnings.append("简书预检读取失败，发布时再做实际判断")

    missing_browser_targets = []
    for platform in platforms:
        if platform in BROWSER_PLATFORMS and not workbench.get(platform):
            missing_browser_targets.append(platform)
            blockers.append(f"未找到 `{platform}` 的可用标签页，请先在当前远程调试 Chrome 中打开并登录")

    for platform in platforms:
        if platform in BROWSER_PLATFORMS and workbench.get(platform):
            label = BROWSER_PLATFORM_LABELS.get(platform, platform)
            try:
                state = inspect_browser_platform_state(platform, workbench[platform])
            except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
                warnings.append(f"{label}预检读取失败，发布时再做实际判断")
            else:
                persist_browser_session_health(root, platform, state, cdp_connection=cdp_connection)
                if not state.get("editor_ready"):
                    detail = state.get("detail") or "页面未进入可写编辑器态"
                    current_url = state.get("current_url") or ""
                    location_detail = f" 当前页面：{current_url}" if current_url else ""
                    blockers.append(f"{label}预检未通过：{detail}.{location_detail}".strip())

    if cdp_connection and any(platform in BROWSER_PLATFORMS for platform in platforms):
        detail = cdp_connection.get("detail")
        if detail:
            warnings.append(detail)

    non_wechat_cover_platforms = [p for p in platforms if p in COVER_PLATFORMS_SET]
    if non_wechat_cover_platforms:
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


def run_platform(platform, markdown_file, mode, theme_name=None):
    registry = build_platform_registry(BASE_DIR)
    result = run_platform_task(
        base_dir=BASE_DIR,
        platform=platform,
        markdown_file=markdown_file,
        mode=mode,
        theme_name=theme_name,
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
        return "console"
    if args.wechat_theme_mode == "random":
        return "random"
    if args.wechat_theme_mode == "fixed":
        return "fixed"
    if args.wechat_theme_mode == "prompt":
        return "prompt"
    if not sys.stdin.isatty() or not available_themes:
        return "fixed"
    return "prompt"


def resolve_wechat_theme_for_article(article_path, theme_mode, available_themes, fixed_theme):
    if theme_mode == "random" and available_themes:
        theme_name = random.choice(available_themes)
        print(f"  [INFO] 随机分配微信排版主题: {theme_name}")
        return theme_name
    if theme_mode == "prompt":
        ans = input(
            f"  请输入并为文章《{article_path.name}》指定微信排版主题 (直接回车默认 '{fixed_theme}'): "
        ).strip()
        return ans if ans else fixed_theme
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
    session = build_session(
        article_paths=[str(path) for path in article_paths],
        platforms=platforms,
        mode=args.mode,
        available_themes=available_themes,
        default_theme=args.wechat_theme,
    )
    session["phase"] = "reviewing"
    session["notice"] = {
        "id": int(time.time() * 1000),
        "level": "info",
        "message": "请选择当前文章的微信模板，然后点击确认发布。",
    }
    save_session(PUBLISH_CONSOLE_SESSION, session)

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

        console_target = ensure_console_target(auto_launch=not args.no_auto_launch)
        wait_for_console_ready(console_target)
        sync_console_state(console_target, session)

        action = wait_for_console_confirmation(console_target, index)
        chosen_theme = action.get("theme") or args.wechat_theme
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

        for platform in platforms:
            theme_name = chosen_theme if platform == "wechat" else None
            result = run_platform(platform, str(article_path), args.mode, theme_name=theme_name)
            result["article"] = str(article_path)
            result["mode"] = args.mode
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


def main():
    parser = argparse.ArgumentParser(description="Publish one or many Markdown articles to multiple platforms.")
    parser.add_argument("markdown_path", help="Markdown 文件或目录路径")
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
        default="chinese",
        help="微信默认主题名，非交互模式下直接使用；默认 chinese",
    )
    parser.add_argument(
        "--wechat-theme-mode",
        choices=["auto", "prompt", "random", "fixed", "console"],
        default="auto",
        help="微信主题分配方式：auto 自动判断；prompt 每篇手动输入；random 随机；fixed 固定使用 --wechat-theme；console 浏览器逐篇预览确认",
    )
    parser.add_argument(
        "--cover-mode",
        choices=list(PUBLISH_OPTION_MODES),
        default="auto",
        help="任务级封面策略：auto 使用默认逻辑；force_on 强制要求封面；force_off 跳过封面设置",
    )
    parser.add_argument(
        "--ai-declaration-mode",
        choices=list(PUBLISH_OPTION_MODES),
        default="auto",
        help="任务级 AI 声明策略：auto 使用默认逻辑；force_on 强制要求声明；force_off 跳过声明设置",
    )
    args = parser.parse_args()

    platforms = parse_platforms(args.platform)
    article_paths = collect_markdown_files(args.markdown_path, offset=args.offset, limit=args.limit)
    results = []

    print(f"[INFO] 准备执行平台: {', '.join(platforms)}")
    print(f"[INFO] 模式: {args.mode}")
    print(f"[INFO] 本次文章数量: {len(article_paths)}")

    browser_platforms = [platform for platform in platforms if platform in BROWSER_PLATFORMS]
    tabs = []
    cdp_connection = None
    if browser_platforms:
        if args.no_auto_launch:
            tabs = list_tabs_or_none()
        else:
            tabs, launched_app = ensure_chrome_ready(browser_platforms)
            if launched_app:
                print(f"[INFO] 已自动启动浏览器: {launched_app}")
        cdp_connection = get_cdp_connection_metadata()

        if not tabs:
            raise RuntimeError("没有检测到可用的 Chrome 标签页，请先打开 Chrome 并启用远程调试")

    if not args.no_auto_open:
        opened = open_missing_platform_tabs(platforms, auto_launch=not args.no_auto_launch)
        if opened:
            print(f"[INFO] 已自动打开平台标签页: {', '.join(opened)}")
        if browser_platforms:
            tabs = list_tabs()

    if args.rebind_workbench and WORKBENCH_FILE.exists():
        WORKBENCH_FILE.unlink()

    workbench = bind_workbench(platforms, tabs)
    bound_platforms = [platform for platform in platforms if workbench.get(platform)]
    if bound_platforms:
        print(f"[INFO] 已绑定固定工作台标签页: {', '.join(bound_platforms)}")

    if not args.no_warmup:
        warmed = warm_platforms(platforms)
        if warmed:
            print(f"[INFO] 已自动预热平台标签页: {', '.join(warmed)}")

    blockers, warnings = run_preflight_checks(
        platforms,
        args.mode,
        workbench,
        cdp_connection=cdp_connection,
        cover_mode=args.cover_mode,
    )
    for warning in warnings:
        print(f"[WARN] {warning}")
    for blocker in blockers:
        print(f"[BLOCK] {blocker}")
    if blockers:
        raise SystemExit(1)

    theme_mode = "fixed"
    available_themes = []
    if "wechat" in platforms:
        theme_dir = BASE_DIR / "themes"
        if theme_dir.exists():
            available_themes = sorted(f.stem for f in theme_dir.glob("*.json"))

        theme_mode = resolve_wechat_theme_mode(args, available_themes)
        if theme_mode == "prompt":
            while True:
                ans = input(
                    "\n请选择微信排版主题分配方式:\n1. 为每篇文章自定义主题 (手动输入)\n2. 为每篇文章随机分配主题\n3. 全部使用固定主题\n请选择 [1/2/3]: "
                ).strip()
                if ans == "1":
                    theme_mode = "prompt"
                    break
                if ans == "2":
                    theme_mode = "random"
                    break
                if ans == "3":
                    theme_mode = "fixed"
                    break

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
        cover_assignments = build_cover_assignments_for_articles(BASE_DIR, article_ids, platforms)
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

    if failed or exit_code:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
