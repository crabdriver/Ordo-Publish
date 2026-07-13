#!/usr/bin/env python3
import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ordo_engine.runner.version import verify_codebase_version
from ordo_engine.assignment.cover_contract import resolve_publication_cover
from ordo_engine.run_lock import RunAlreadyActive, run_lock

DEFAULT_WATCH_DIR = Path("/Users/wizard/tiandidadao/润色")
STATE_FILE = BASE_DIR / ".ordo" / "auto_publish_state.json"
PUBLISH_RECORDS_FILE = BASE_DIR / "publish_records.csv"
PUBLISH_LOCK_FILE = BASE_DIR / ".ordo" / "publish.lock"
DEFAULT_VPS_HOST = "209.54.106.236"
DEFAULT_VPS_USER = "root"
DEFAULT_VPS_PATH = "/root/ordo-publish"
WECHAT_PLATFORMS = "wechat"
PUBLISH_PLATFORMS = "zhihu,toutiao,yidian,bilibili"
LOCAL_PUBLISH_PLATFORMS = "jianshu"
SUCCESS_STATUSES = {"published", "scheduled", "draft_only", "success_unknown"}
JIANSHU_URL = "https://www.jianshu.com/writer#/"


def load_state(path=None):
    path = path or STATE_FILE
    if not path.exists():
        return {"articles": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"articles": {}}


def save_state(state, path=None):
    path = path or STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def article_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def record_matches_article(row, article: Path):
    raw = (row.get("article") or "").strip()
    if not raw:
        return False
    path = Path(raw).expanduser()
    try:
        if path.resolve() == article.resolve():
            return True
    except OSError:
        pass
    row_article_id = (row.get("article_id") or "").strip()
    article_id = parse_frontmatter(article).get("article_id")
    return bool(row_article_id and article_id and row_article_id == article_id)


def successful_record_platforms(article: Path, records_path=None):
    records_path = records_path or PUBLISH_RECORDS_FILE
    if not records_path.exists():
        return set()
    platforms = set()
    try:
        with records_path.open("r", encoding="utf-8", newline="") as fp:
            for row in csv.DictReader(fp):
                if not record_matches_article(row, article):
                    continue
                if str(row.get("returncode") or "") != "0":
                    continue
                if (row.get("status") or "") not in SUCCESS_STATUSES:
                    continue
                platform = row.get("platform")
                if platform:
                    platforms.add(platform)
    except (OSError, csv.Error, UnicodeDecodeError):
        return set()
    return platforms


def merge_record_successes(existing, article: Path):
    data = dict(existing or {})
    platform_state = dict(data.get("platforms") or {})
    if data.get("wechat_returncode") == 0:
        platform_state.setdefault("wechat", {"returncode": 0})
    if data.get("publish_returncode") == 0:
        for platform in PUBLISH_PLATFORMS.split(","):
            platform_state.setdefault(platform, {"returncode": 0})
    if data.get("local_publish_returncode") == 0:
        platform_state.setdefault("jianshu", {"returncode": 0})
    platforms = successful_record_platforms(article)
    for platform in platforms:
        platform_state[platform] = {"returncode": 0}
    data["platforms"] = platform_state
    if "wechat" in platforms:
        data["wechat_returncode"] = 0
    if all(platform in platforms for platform in PUBLISH_PLATFORMS.split(",")):
        data["publish_returncode"] = 0
    if "jianshu" in platforms:
        data["local_publish_returncode"] = 0
    if data.get("wechat_returncode") == 0 and data.get("publish_returncode") == 0 and data.get("local_publish_returncode") == 0:
        data["status"] = "success"
    elif data:
        data.setdefault("status", "attempted")
    return data


def list_articles(watch_dir: Path):
    root = watch_dir.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"监听目录不存在: {root}")
    return sorted(
        item
        for item in root.glob("*.md")
        if item.is_file() and not item.name.startswith(".")
    )


def parse_frontmatter(article: Path):
    text = article.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    meta = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return meta


def find_sidecar_cover(article: Path, meta: dict):
    if not meta.get("cover") and not meta.get("article_id"):
        return None
    return resolve_publication_cover(article)


def chrome_binary_candidates():
    if platform.system() == "Darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    if platform.system() == "Windows":
        return [Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe"]
    return [Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"), Path("/usr/bin/chromium-browser")]


def find_chrome_binary():
    for candidate in chrome_binary_candidates():
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 Chrome/Chromium，无法启动简书专用浏览器")


def wait_for_cdp_port(port: int, timeout_seconds=20):
    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{port}/json/version"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def require_vps_ready(*, ssh_host=DEFAULT_VPS_HOST, ssh_user=DEFAULT_VPS_USER, remote_path=DEFAULT_VPS_PATH):
    match, local_commit, remote_commit = verify_codebase_version(
        ssh_host=ssh_host,
        ssh_user=ssh_user,
        remote_path=remote_path,
        local_repo_path=str(BASE_DIR),
    )
    if not match:
        raise RuntimeError(
            "VPS 代码版本不一致，自动发表已阻断。"
            f" 本地={local_commit or 'unknown'} 远端={remote_commit or 'unknown'}"
        )

    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        f"{ssh_user}@{ssh_host}",
        f"cd {remote_path} && export LIVE_CDP_PORT=9334 && node live_cdp.mjs list >/dev/null",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"VPS 浏览器/CDP 预检失败（端口 9334）。{stderr}")


def build_publish_cmd(
    article: Path,
    *,
    platforms: str,
    mode: str,
    remote: str = "vps",
    cover=None,
    template_theme=None,
    wechat_theme=None,
    no_auto_launch=False,
):
    cmd = [
        sys.executable,
        str(BASE_DIR / "publish.py"),
        str(article),
        "--platform",
        platforms,
        "--mode",
        mode,
        "--remote",
        remote,
        "--skip-published",
    ]
    if cover:
        cmd.extend(["--cover", str(cover), "--cover-mode", "force_on"])
    if template_theme and platforms != WECHAT_PLATFORMS:
        cmd.extend(["--template-theme", template_theme])
    if wechat_theme and platforms == WECHAT_PLATFORMS:
        cmd.extend(["--wechat-theme-mode", "fixed", "--wechat-theme", wechat_theme])
    if no_auto_launch:
        cmd.append("--no-auto-launch")
    return cmd


def run_cmd(cmd, *, dry_run=False, env=None):
    print("[CMD] " + " ".join(cmd))
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=str(BASE_DIR), env=env).returncode


def dedicated_browser_env():
    env = dict(os.environ)
    env.setdefault("LIVE_CDP_PORT", "9333")
    env.setdefault("ORDO_BROWSER_SESSION_DEBUG_PORT", "9333")
    env.setdefault("ORDO_BROWSER_SESSION_PROFILE_DIR", str(BASE_DIR / ".ordo" / "browser-session" / "profile"))
    return env


def close_dedicated_browser(env):
    subprocess.run(
        ["node", "live_cdp.mjs", "closebrowser"],
        cwd=str(BASE_DIR),
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


@contextmanager
def jianshu_dedicated_browser(*, dry_run=False):
    env = dedicated_browser_env()
    if dry_run:
        yield env
        return

    port = int(env["LIVE_CDP_PORT"])
    profile_dir = Path(env["ORDO_BROWSER_SESSION_PROFILE_DIR"])
    profile_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome_binary()
    proc = subprocess.Popen(
        [
            str(chrome),
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            JIANSHU_URL,
        ],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        if not wait_for_cdp_port(port):
            raise RuntimeError(f"简书专用浏览器 CDP 未就绪: 127.0.0.1:{port}")
        yield env
    finally:
        try:
            close_dedicated_browser(env)
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def run_group(name, existing, returncode_key, cmd, *, dry_run=False, env=None):
    if existing and existing.get(returncode_key) == 0:
        print(f"[SKIP] {name} 已成功，跳过该组")
        return 0
    return run_cmd(cmd, dry_run=dry_run, env=env)


def run_platform(platform, existing, cmd, *, dry_run=False, env=None):
    previous = (existing or {}).get("platforms", {}).get(platform, {})
    if previous.get("returncode") == 0:
        print(f"[SKIP] {platform} 已成功，跳过")
        return 0
    return run_cmd(cmd, dry_run=dry_run, env=env)


def publish_article(
    article: Path,
    *,
    force=False,
    dry_run=False,
    state=None,
    default_template_theme=None,
    default_wechat_theme=None,
):
    article = article.expanduser().resolve()
    if not article.is_file():
        raise FileNotFoundError(f"文章不存在: {article}")
    if article.suffix.lower() != ".md":
        raise ValueError(f"只支持 Markdown: {article}")

    state = state or load_state()
    key = article_key(article)
    existing = merge_record_successes(state["articles"].get(key), article)
    if existing and existing.get("status") == "success" and not force:
        print(f"[SKIP] 已有自动化记录，避免重复发表: {article}")
        return "skipped"
    if force:
        existing = None

    started_at = datetime.now().isoformat(timespec="seconds")
    meta = parse_frontmatter(article)
    cover = find_sidecar_cover(article, meta)
    template_theme = meta.get("template_theme") or meta.get("theme") or default_template_theme
    wechat_theme = meta.get("wechat_theme") or default_wechat_theme

    print(f"[INFO] 自动发表: {article}")
    if cover:
        print(f"[INFO] 使用封面: {cover}")
    if template_theme:
        print(f"[INFO] 使用非微信模板: {template_theme}")
    if wechat_theme:
        print(f"[INFO] 使用微信模板: {wechat_theme}")

    platform_results = {}
    platform_results["wechat"] = run_platform(
        "wechat", existing,
        build_publish_cmd(
            article,
            platforms=WECHAT_PLATFORMS,
            mode="draft",
            cover=cover,
            wechat_theme=wechat_theme,
        ),
        dry_run=dry_run,
    )
    for platform_name in PUBLISH_PLATFORMS.split(","):
        platform_results[platform_name] = run_platform(
            platform_name, existing,
            build_publish_cmd(
                article, platforms=platform_name, mode="publish", cover=cover,
                template_theme=template_theme,
            ),
            dry_run=dry_run,
        )
    with jianshu_dedicated_browser(dry_run=dry_run) as jianshu_env:
        platform_results["jianshu"] = run_platform(
            "jianshu", existing,
            build_publish_cmd(
                article,
                platforms=LOCAL_PUBLISH_PLATFORMS,
                mode="publish",
                remote="local",
                cover=cover,
                template_theme=template_theme,
                no_auto_launch=True,
            ),
            dry_run=dry_run,
            env=jianshu_env,
        )
    success = all(returncode == 0 for returncode in platform_results.values())

    if not dry_run:
        state["articles"][key] = {
            "path": key,
            "title": article.stem,
            "status": "success" if success else "attempted",
            "started_at": started_at,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "cover": str(cover) if cover else None,
            "template_theme": template_theme,
            "wechat_theme": wechat_theme,
            "wechat_returncode": platform_results["wechat"],
            "publish_returncode": max(platform_results[p] for p in PUBLISH_PLATFORMS.split(",")),
            "local_publish_returncode": platform_results["jianshu"],
            "platforms": {p: {"returncode": rc} for p, rc in platform_results.items()},
        }
        save_state(state)
    if dry_run:
        print("[DRY-RUN] 未执行发布，未写状态")
        return "dry_run"
    succeeded = [p for p, rc in platform_results.items() if rc == 0]
    failed = [p for p, rc in platform_results.items() if rc != 0]
    print(f"[SUMMARY] 成功/跳过: {', '.join(succeeded) or '无'}")
    print(f"[SUMMARY] 失败: {', '.join(failed) or '无'}")
    print("[OK] 自动发表完成" if success else "[WARN] 部分平台失败；下次扫描只重试失败平台")
    return "success" if success else "attempted"


def publish_article_once(article: Path, **kwargs):
    try:
        with run_lock(PUBLISH_LOCK_FILE):
            return publish_article(article, **kwargs)
    except RunAlreadyActive:
        print("[SKIP] 已有发表任务运行中，本轮跳过")
        return "skipped_overlap"


def scan_once(watch_dir: Path, *, force=False, dry_run=False, default_template_theme=None, default_wechat_theme=None):
    try:
        with run_lock(PUBLISH_LOCK_FILE):
            state = load_state()
            todo = []
            for article in list_articles(watch_dir):
                existing = merge_record_successes(state["articles"].get(article_key(article)), article)
                if force or not existing or existing.get("status") != "success":
                    todo.append(article)
            if not todo:
                print("[INFO] 没有未处理文章")
                return []
            require_vps_ready()
            for article in todo:
                publish_article(
                    article,
                    force=force,
                    dry_run=dry_run,
                    state=state,
                    default_template_theme=default_template_theme,
                    default_wechat_theme=default_wechat_theme,
                )
            return todo
    except RunAlreadyActive:
        print("[SKIP] 已有发表任务运行中，本轮跳过")
        return "skipped_overlap"


def run_daemon(watch_dir: Path, *, interval=300, **kwargs):
    while True:
        scan_once(watch_dir, **kwargs)
        time.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="监听润色目录并自动发表未处理文章")
    parser.add_argument("--watch-dir", type=Path, default=DEFAULT_WATCH_DIR)
    parser.add_argument("--article", type=Path, help="指定单篇文章")
    parser.add_argument("--once", action="store_true", help="扫描一次")
    parser.add_argument("--daemon", action="store_true", help="循环扫描")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--force", action="store_true", help="忽略状态锁，允许重发")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不执行")
    parser.add_argument("--template-theme", default=None, help="非微信平台默认模板，例如 sspai")
    parser.add_argument("--wechat-theme", default=None, help="微信公众号默认模板")
    args = parser.parse_args(argv)

    if args.article:
        return publish_article_once(
            args.article,
            force=args.force,
            dry_run=args.dry_run,
            default_template_theme=args.template_theme,
            default_wechat_theme=args.wechat_theme,
        )
    if args.daemon:
        return run_daemon(
            args.watch_dir,
            interval=args.interval,
            force=args.force,
            dry_run=args.dry_run,
            default_template_theme=args.template_theme,
            default_wechat_theme=args.wechat_theme,
        )
    else:
        return scan_once(
            args.watch_dir,
            force=args.force,
            dry_run=args.dry_run,
            default_template_theme=args.template_theme,
            default_wechat_theme=args.wechat_theme,
        )


if __name__ == "__main__":
    main()
