#!/usr/bin/env python3
"""本地无人值守发表编排；结果只信 publish_records.csv typed outcome。"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ordo_engine.assignment.cover_contract import resolve_publication_cover
from ordo_engine.run_lock import RunAlreadyActive, run_lock

DEFAULT_WATCH_DIR = Path("/Users/wizard/tiandidadao/润色")
STATE_FILE = BASE_DIR / ".ordo" / "auto_publish_state.json"
PUBLISH_RECORDS_FILE = BASE_DIR / "publish_records.csv"
PUBLISH_LOCK_FILE = BASE_DIR / ".ordo" / "publish.lock"
PUBLISH_TIMEOUT_SECONDS = 900
WECHAT_PLATFORM = "wechat"
BROWSER_PLATFORMS = ("zhihu", "toutiao", "jianshu", "yidian", "bilibili")
ALL_PLATFORMS = (WECHAT_PLATFORM, *BROWSER_PLATFORMS)
MODE_BY_PLATFORM = {WECHAT_PLATFORM: "draft", **{item: "publish" for item in BROWSER_PLATFORMS}}
TERMINAL_BY_MODE = {
    "draft": {"draft_only", "draft_saved", "skipped_existing"},
    "publish": {"published", "scheduled", "skipped_existing"},
}
RATE_LIMIT_STATUSES = {"limit_reached", "rate_limited"}
UNVERIFIED_STATUSES = {"submitted_unverified"}
REQUIRED_RECORD_FIELDS = {
    "article", "article_id", "platform", "mode", "status", "error_type", "returncode",
}

# 兼容旧调用者；新命令会合并全部浏览器平台。
WECHAT_PLATFORMS = WECHAT_PLATFORM
PUBLISH_PLATFORMS = ",".join(BROWSER_PLATFORMS)
LOCAL_PUBLISH_PLATFORMS = "jianshu"


class AutoPublishStateError(RuntimeError):
    pass


@dataclass
class PublishSummary:
    succeeded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    rate_limited: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def add(self, platform: str, status: str, error_type: str = "", returncode: int = 1) -> None:
        if status == "skipped_existing":
            (self.skipped if returncode == 0 else self.failed).append(platform)
        elif status in TERMINAL_BY_MODE[MODE_BY_PLATFORM[platform]] and returncode == 0:
            self.succeeded.append(platform)
        elif status in RATE_LIMIT_STATUSES or error_type == "rate_limited":
            self.rate_limited.append(platform)
        elif status in UNVERIFIED_STATUSES:
            self.unverified.append(platform)
        else:
            self.failed.append(platform)

    def print(self) -> None:
        for label, values in (
            ("成功", self.succeeded),
            ("跳过", self.skipped),
            ("限流", self.rate_limited),
            ("待核验", self.unverified),
            ("失败", self.failed),
        ):
            print(f"[SUMMARY] {label}: {', '.join(values) or '无'}")


def load_state(path=None):
    path = Path(path or STATE_FILE)
    if not path.exists():
        return {"articles": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoPublishStateError(f"自动发表状态 JSON 损坏或不可读: {path}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("articles", {}), dict):
        raise AutoPublishStateError(f"自动发表状态 JSON 根结构无效: {path}")
    state.setdefault("articles", {})
    return state


def save_state(state, path=None):
    path = Path(path or STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fp:
            temp_path = Path(fp.name)
            json.dump(state, fp, ensure_ascii=False, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def article_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def parse_frontmatter(article: Path):
    text = article.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")
    return meta


def find_sidecar_cover(article: Path, meta: dict):
    if not meta.get("cover") and not meta.get("article_id"):
        return None
    return resolve_publication_cover(article)


def list_articles(watch_dir: Path):
    root = watch_dir.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"监听目录不存在: {root}")
    return sorted(item for item in root.glob("*.md") if item.is_file() and not item.name.startswith("."))


def record_matches_article(row, article: Path, *, article_id=None):
    raw = (row.get("article") or "").strip()
    if raw:
        try:
            if Path(raw).expanduser().resolve() == article.resolve():
                return True
        except OSError:
            pass
    row_article_id = (row.get("article_id") or "").strip()
    return bool(row_article_id and article_id and row_article_id == article_id)


def read_record_rows(records_path=None):
    path = Path(records_path or PUBLISH_RECORDS_FILE)
    if not path.exists():
        return []
    if path.stat().st_size == 0:
        raise RuntimeError(f"发布记录 CSV 为空: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            fields = set(reader.fieldnames or ())
            missing = REQUIRED_RECORD_FIELDS - fields
            if missing:
                raise RuntimeError(f"发布记录 CSV 缺少字段 {sorted(missing)}: {path}")
            return list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"发布记录 CSV 不可读: {path}") from exc


def _is_terminal(platform, record):
    if not isinstance(record, dict):
        return False
    mode = MODE_BY_PLATFORM[platform]
    return (
        record.get("mode") == mode
        and record.get("status") in TERMINAL_BY_MODE[mode]
        and _parse_returncode(record.get("returncode")) == 0
    )


def _latest_records(article: Path, rows=None):
    rows = read_record_rows() if rows is None else rows
    article_id = parse_frontmatter(article).get("article_id")
    latest = {}
    for row in rows:
        platform = (row.get("platform") or "").strip()
        if platform not in ALL_PLATFORMS or not record_matches_article(row, article, article_id=article_id):
            continue
        if (row.get("mode") or "").strip() != MODE_BY_PLATFORM[platform]:
            continue
        candidate = dict(row)
        current = latest.get(platform)
        if current is None or (not _is_terminal(platform, current) and _is_terminal(platform, candidate)):
            latest[platform] = candidate
        elif not _is_terminal(platform, current):
            latest[platform] = candidate
    return latest


def _parse_returncode(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def successful_record_platforms(article: Path, records_path=None):
    rows = read_record_rows(records_path)
    return {platform for platform, row in _latest_records(article, rows).items() if _is_terminal(platform, row)}


def merge_record_successes(existing, article: Path):
    data = dict(existing or {})
    platform_state = {
        key: dict(value) for key, value in (data.get("platforms") or {}).items() if isinstance(value, dict)
    }
    for platform, row in _latest_records(article).items():
        if _is_terminal(platform, platform_state.get(platform)):
            continue
        platform_state[platform] = {
            "mode": row.get("mode") or MODE_BY_PLATFORM[platform],
            "status": row.get("status") or "unknown",
            "returncode": _parse_returncode(row.get("returncode")),
            "error_type": row.get("error_type") or "",
        }
    data["platforms"] = platform_state
    data["status"] = "success" if all(_is_terminal(p, platform_state.get(p)) for p in ALL_PLATFORMS) else "attempted"
    return data


def build_publish_cmd(
    article: Path, *, platforms: str, mode: str, remote: str = "local",
    cover=None, template_theme=None, wechat_theme=None, no_auto_launch=False,
    force_republish=False,
):
    cmd = [
        sys.executable, str(BASE_DIR / "publish.py"), str(article),
        "--platform", platforms, "--mode", mode, "--remote", remote,
        "--continue-on-error",
    ]
    if cover:
        cmd.extend(["--cover", str(cover), "--cover-mode", "force_on"])
    if template_theme and platforms != WECHAT_PLATFORM:
        cmd.extend(["--template-theme", template_theme])
    if wechat_theme and platforms == WECHAT_PLATFORM:
        cmd.extend(["--wechat-theme-mode", "fixed", "--wechat-theme", wechat_theme])
    if no_auto_launch:
        cmd.append("--no-auto-launch")
    if force_republish:
        cmd.append("--force-republish")
    return cmd


def run_cmd(cmd, *, dry_run=False, env=None, timeout=PUBLISH_TIMEOUT_SECONDS):
    print("[CMD] " + " ".join(cmd))
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=str(BASE_DIR), env=env, timeout=timeout).returncode


def _new_command_outcomes(article, platforms, mode, row_offset, command_returncode):
    rows = read_record_rows()[row_offset:]
    article_id = parse_frontmatter(article).get("article_id")
    latest = {}
    for row in rows:
        platform = (row.get("platform") or "").strip()
        if platform not in platforms or (row.get("mode") or "").strip() != mode:
            continue
        if record_matches_article(row, article, article_id=article_id):
            latest[platform] = dict(row)
    outcomes = {}
    for platform in platforms:
        row = latest.get(platform)
        if row is None:
            outcomes[platform] = {
                "mode": mode, "status": "unknown",
                "error_type": "missing_publish_record", "returncode": command_returncode,
            }
        else:
            outcomes[platform] = {
                "mode": mode, "status": row.get("status") or "unknown",
                "error_type": row.get("error_type") or "",
                "returncode": _parse_returncode(row.get("returncode")),
            }
    return outcomes


def _pending_platforms(existing):
    platform_state = (existing or {}).get("platforms") or {}
    return [platform for platform in ALL_PLATFORMS if not _is_terminal(platform, platform_state.get(platform))]


def _persist_article_progress(
    state, key, article, platform_state, *, started_at, cover, template_theme, wechat_theme,
):
    success = all(_is_terminal(platform, platform_state.get(platform)) for platform in ALL_PLATFORMS)
    old = dict(state["articles"].get(key) or {})
    state["articles"][key] = {
        **old, "path": key, "title": article.stem,
        "status": "success" if success else "attempted",
        "started_at": old.get("started_at") or started_at,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "cover": str(cover) if cover else None,
        "template_theme": template_theme, "wechat_theme": wechat_theme,
        "platforms": platform_state,
    }
    save_state(state)
    return success


def publish_article(
    article: Path, *, force=False, dry_run=False, state=None,
    default_template_theme=None, default_wechat_theme=None,
):
    article = article.expanduser().resolve()
    if not article.is_file():
        raise FileNotFoundError(f"文章不存在: {article}")
    if article.suffix.lower() != ".md":
        raise ValueError(f"只支持 Markdown: {article}")
    if state is None:
        state = load_state()
    key = article_key(article)
    existing = {"platforms": {}} if force else merge_record_successes(state["articles"].get(key), article)
    pending = _pending_platforms(existing)
    if not pending:
        print(f"[SKIP] 所有平台已有明确终态: {article}")
        return "skipped"

    meta = parse_frontmatter(article)
    cover = find_sidecar_cover(article, meta)
    template_theme = meta.get("template_theme") or meta.get("theme") or default_template_theme
    wechat_theme = meta.get("wechat_theme") or default_wechat_theme
    print(f"[INFO] 自动发表: {article}")

    groups = []
    if WECHAT_PLATFORM in pending:
        groups.append(([WECHAT_PLATFORM], "draft", build_publish_cmd(
            article, platforms=WECHAT_PLATFORM, mode="draft", cover=cover,
            wechat_theme=wechat_theme, force_republish=force,
        )))
    browser_pending = [p for p in BROWSER_PLATFORMS if p in pending]
    if browser_pending:
        groups.append((browser_pending, "publish", build_publish_cmd(
            article, platforms=",".join(browser_pending), mode="publish",
            cover=cover, template_theme=template_theme, force_republish=force,
        )))
    if dry_run:
        for _platforms, _mode, cmd in groups:
            run_cmd(cmd, dry_run=True)
        print("[DRY-RUN] 未执行发布，未写状态")
        return "dry_run"

    started_at = datetime.now().isoformat(timespec="seconds")
    platform_state = dict(existing.get("platforms") or {})
    summary = PublishSummary()
    for platform in ALL_PLATFORMS:
        if platform not in pending:
            summary.skipped.append(platform)
    for platforms, mode, cmd in groups:
        row_offset = len(read_record_rows())
        returncode = run_cmd(cmd)
        outcomes = _new_command_outcomes(article, platforms, mode, row_offset, returncode)
        for platform, outcome in outcomes.items():
            platform_state[platform] = {**outcome, "updated_at": datetime.now().isoformat(timespec="seconds")}
            summary.add(
                platform, outcome["status"], outcome["error_type"], outcome["returncode"]
            )
        success = _persist_article_progress(
            state, key, article, platform_state, started_at=started_at, cover=cover,
            template_theme=template_theme, wechat_theme=wechat_theme,
        )
    summary.print()
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
                if force or _pending_platforms(existing):
                    todo.append(article)
            if not todo:
                print("[INFO] 没有未处理文章")
                return []
            for article in todo:
                publish_article(
                    article, force=force, dry_run=dry_run, state=state,
                    default_template_theme=default_template_theme,
                    default_wechat_theme=default_wechat_theme,
                )
            return todo
    except RunAlreadyActive:
        print("[SKIP] 已有发表任务运行中，本轮跳过")
        return "skipped_overlap"


def run_daemon(watch_dir: Path, *, interval=300, **kwargs):
    while True:
        try:
            scan_once(watch_dir, **kwargs)
        except Exception as exc:
            print(f"[ERROR] 自动发表扫描失败: {exc}")
        time.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="监听润色目录并在本地自动发表未处理文章")
    parser.add_argument("--watch-dir", type=Path, default=DEFAULT_WATCH_DIR)
    parser.add_argument("--article", type=Path, help="指定单篇文章")
    parser.add_argument("--once", action="store_true", help="扫描一次")
    parser.add_argument("--daemon", action="store_true", help="循环扫描")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--force", action="store_true", help="忽略自动化终态，允许显式重发")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不执行")
    parser.add_argument("--template-theme", default=None, help="非微信平台默认模板，例如 sspai")
    parser.add_argument("--wechat-theme", default=None, help="微信公众号默认模板")
    args = parser.parse_args(argv)
    kwargs = {
        "force": args.force, "dry_run": args.dry_run,
        "default_template_theme": args.template_theme,
        "default_wechat_theme": args.wechat_theme,
    }
    if args.article:
        return publish_article_once(args.article, **kwargs)
    if args.daemon:
        return run_daemon(args.watch_dir, interval=args.interval, **kwargs)
    return scan_once(args.watch_dir, **kwargs)


if __name__ == "__main__":
    main()
