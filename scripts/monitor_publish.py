#!/usr/bin/env python3
"""本地无人值守发表编排；唯一 coordinator + 单锁 + 进程内发布。"""
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from ordo_engine.assignment.cover_contract import resolve_publication_cover
from ordo_engine.results.publish_records import load_publish_records_at_path
from ordo_engine.run_lock import RunAlreadyActive, run_lock
from ordo_engine.run_state import (
    ArticleStage,
    StateCorruptionError,
    article_key as durable_article_key,
    load_v2_state,
    stable_article_id,
)
from ordo_engine.runner.pipeline import (
    BatchCoordinator, WECHAT_PLATFORM, BROWSER_PLATFORMS_TUPLE,
)

DEFAULT_WATCH_DIR = Path("/Users/wizard/tiandidadao/润色")
STATE_FILE = BASE_DIR / ".ordo" / "auto_publish_state.json"
PUBLISH_RECORDS_FILE = BASE_DIR / "publish_records.csv"
PUBLISH_LOCK_FILE = BASE_DIR / ".ordo" / "publish.lock"
PUBLISH_TIMEOUT_SECONDS = 900
WECHAT_PLATFORM = "wechat"
BROWSER_PLATFORMS = BROWSER_PLATFORMS_TUPLE
ALL_PLATFORMS = (WECHAT_PLATFORM, *BROWSER_PLATFORMS)
MODE_BY_PLATFORM = {WECHAT_PLATFORM: "draft", **{item: "publish" for item in BROWSER_PLATFORMS}}
TERMINAL_BY_MODE = {
    "draft": {"draft_only", "draft_saved", "skipped_existing"},
    "publish": {"published", "scheduled", "skipped_existing"},
}
RATE_LIMIT_STATUSES = {"limit_reached", "rate_limited"}
UNVERIFIED_STATUSES = {"submitted_unverified"}
REQUIRED_RECORD_FIELDS = {
    "run_id", "article", "article_id", "article_key", "platform", "mode", "status", "error_type", "returncode",
}

# 兼容旧调用者；新命令会合并全部浏览器平台。
WECHAT_PLATFORMS = WECHAT_PLATFORM
PUBLISH_PLATFORMS = ",".join(BROWSER_PLATFORMS)
LOCAL_PUBLISH_PLATFORMS = "jianshu"


class AutoPublishStateError(RuntimeError):
    pass


class AutoPublishRunError(RuntimeError):
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
    return durable_article_key(path)


def _legacy_article_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def _state_article(state, article: Path):
    articles = state.get("articles", {})
    return articles.get(article_key(article)) or articles.get(_legacy_article_key(article))


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


def record_matches_article(row, article: Path, *, article_id=None, identity=None):
    identity = identity or durable_article_key(article)
    row_article_id = (row.get("article_id") or "").strip()
    row_identity = (row.get("article_key") or "").strip()
    if article_id:
        return bool(
            (row_article_id and row_article_id == article_id)
            or (row_identity and row_identity == identity)
        )
    if row_article_id:
        return False
    if row_identity:
        return row_identity == identity
    # 旧 CSV 没有 article_key；仅为迁移兼容保留路径匹配。
    raw = (row.get("article") or "").strip()
    if raw:
        try:
            if Path(raw).expanduser().resolve() == article.resolve():
                return True
        except OSError:
            pass
    return False


def read_record_rows(records_path=None):
    path = Path(records_path or PUBLISH_RECORDS_FILE)
    if not path.exists():
        return []
    try:
        rows = load_publish_records_at_path(path, fail_on_empty=True)
        for row in rows:
            missing = REQUIRED_RECORD_FIELDS - set(row)
            if missing:
                raise RuntimeError(f"发布记录 CSV 缺少字段 {sorted(missing)}: {path}")
        return rows
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


def _is_protected_from_resubmission(platform, record):
    if _is_terminal(platform, record):
        return True
    if not isinstance(record, dict):
        return False
    return (
        record.get("mode") == MODE_BY_PLATFORM[platform]
        and record.get("status") in UNVERIFIED_STATUSES
    )


def _latest_records(article: Path, rows=None):
    rows = read_record_rows() if rows is None else rows
    article_id = parse_frontmatter(article).get("article_id")
    identity = durable_article_key(article)
    latest = {}
    for row in rows:
        platform = (row.get("platform") or "").strip()
        if platform not in ALL_PLATFORMS or not record_matches_article(
            row, article, article_id=article_id, identity=identity
        ):
            continue
        if (row.get("mode") or "").strip() != MODE_BY_PLATFORM[platform]:
            continue
        candidate = dict(row)
        current = latest.get(platform)
        candidate_unverified = candidate.get("status") in UNVERIFIED_STATUSES
        current_protected = bool(
            current and (
                _is_terminal(platform, current)
                or current.get("status") in UNVERIFIED_STATUSES
            )
        )
        if current is None or candidate_unverified or _is_terminal(platform, candidate):
            latest[platform] = candidate
        elif not current_protected:
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
        current = platform_state.get(platform)
        row_is_protected = _is_terminal(platform, row) or row.get("status") in UNVERIFIED_STATUSES
        current_is_protected = _is_terminal(platform, current) or (
            isinstance(current, dict) and current.get("status") in UNVERIFIED_STATUSES
        )
        if current_is_protected and not row_is_protected:
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
    run_id=None,
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
    if run_id:
        cmd.extend(["--run-id", str(run_id)])
    return cmd


def run_cmd(
    cmd, *, dry_run=False, env=None, timeout=PUBLISH_TIMEOUT_SECONDS, lock_fd=None
):
    print("[CMD] " + " ".join(cmd))
    if dry_run:
        return 0
    popen_kwargs = {
        "cwd": str(BASE_DIR),
        "env": env,
        "start_new_session": True,
    }
    if lock_fd is not None:
        child_env = dict(os.environ if env is None else env)
        child_env["ORDO_PUBLISH_LOCK_FD"] = str(lock_fd)
        popen_kwargs.update(env=child_env, pass_fds=(lock_fd,))
    process = subprocess.Popen(cmd, **popen_kwargs)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        return 124


def _signal_process_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except (AttributeError, OSError):
        if sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()


def _terminate_process_group(process, *, grace_seconds=5):
    _signal_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass


def _new_command_outcomes(article, platforms, mode, run_id, command_returncode):
    rows = read_record_rows()
    article_id = parse_frontmatter(article).get("article_id")
    latest = {}
    for row in rows:
        platform = (row.get("platform") or "").strip()
        if platform not in platforms or (row.get("mode") or "").strip() != mode:
            continue
        if (row.get("run_id") or "").strip() != run_id:
            continue
        if record_matches_article(row, article, article_id=article_id):
            latest[platform] = dict(row)
    outcomes = {}
    for platform in platforms:
        row = latest.get(platform)
        if row is None:
            outcomes[platform] = {
                "mode": mode, "status": "unknown",
                "error_type": "timeout" if command_returncode == 124 else "missing_publish_record",
                "returncode": command_returncode,
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
    return [
        platform for platform in ALL_PLATFORMS
        if not _is_protected_from_resubmission(platform, platform_state.get(platform))
    ]


def _persist_article_progress(
    state, key, article, platform_state, *, started_at, cover, template_theme, wechat_theme,
):
    success = all(_is_terminal(platform, platform_state.get(platform)) for platform in ALL_PLATFORMS)
    old = dict(state["articles"].get(key) or {})
    state["articles"][key] = {
        **old, "path": str(article), "title": article.stem,
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
    default_template_theme=None, default_wechat_theme=None, lock_fd=None,
):
    """发布单篇文章（兼容旧接口，内部使用 BatchCoordinator）。"""
    article = article.expanduser().resolve()
    if not article.is_file():
        raise FileNotFoundError(f"文章不存在: {article}")
    if article.suffix.lower() != ".md":
        raise ValueError(f"只支持 Markdown: {article}")

    if dry_run:
        print(f"[DRY-RUN] {article}")
        return "dry_run"

    coordinator = BatchCoordinator(base_dir=BASE_DIR)
    summary = coordinator.run_batch([article])
    _print_summary(summary)

    # 判断成功/attempted
    for identity, article_summary in summary.get("articles", {}).items():
        platforms = article_summary.get("platforms", {})
        if not platforms:
            return "attempted"
        all_ok = all(v.get("stage") in ("published", "draft_saved")
                     for v in platforms.values())
        return "success" if all_ok else "attempted"

    return "attempted"


def publish_article_once(article: Path, **kwargs):
    """发布单篇文章（外部入口，获取后释放锁）。"""
    try:
        with run_lock(PUBLISH_LOCK_FILE):
            return publish_article(article, **kwargs)
    except RunAlreadyActive:
        print("[SKIP] 已有发表任务运行中，本轮跳过")
        return "skipped_overlap"


def scan_once(watch_dir: Path, *, force=False, dry_run=False, default_template_theme=None, default_wechat_theme=None):
    """扫描一次，使用 BatchCoordinator 进程内执行。"""
    if force:
        raise AutoPublishRunError("自动发布禁用 --force，避免重复真实发表")
    try:
        with run_lock(PUBLISH_LOCK_FILE) as _lock_fd:
            try:
                state = load_v2_state(STATE_FILE)
            except StateCorruptionError as exc:
                raise AutoPublishStateError(str(exc)) from exc
            todo = []
            for article in list_articles(watch_dir):
                identity = stable_article_id(article, watch_dir=watch_dir)
                existing = state.get(identity)
                if existing is None or existing.article_stage != ArticleStage.completed:
                    todo.append(article)
            if not todo:
                print("[INFO] 没有未处理文章")
                return []

            coordinator = BatchCoordinator(
                base_dir=BASE_DIR,
                watch_dir=watch_dir,
            )
            if dry_run:
                print("[DRY-RUN] 以下文章将处理:", [a.name for a in todo])
                return todo

            summary = coordinator.run_batch(todo)
            _print_summary(summary)

            # 检查是否有未完成
            failed = []
            for identity, article_summary in summary.get("articles", {}).items():
                platforms = article_summary.get("platforms", {})
                if any(v.get("stage") not in ("published", "draft_saved")
                       for v in platforms.values()):
                    failed.append(article_summary.get("article_id", identity))

            if failed:
                raise AutoPublishRunError(f"自动发表存在非终态结果: {', '.join(failed[:3])}")

            return todo
    except RunAlreadyActive:
        print("[SKIP] 已有发表任务运行中，本轮跳过")
        return "skipped_overlap"


def _print_summary(summary):
    """打印批次摘要。"""
    for identity, article in summary.get("articles", {}).items():
        title = article.get("title") or identity
        stages = [f"{k}={v.get('stage')}" for k, v in article.get("platforms", {}).items()]
        print(f"[SUMMARY] {title}: {', '.join(stages)}")


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
        result = publish_article_once(args.article, **kwargs)
        return 1 if result in {"attempted", "skipped_overlap"} else 0
    if args.daemon:
        return run_daemon(args.watch_dir, interval=args.interval, **kwargs)
    try:
        result = scan_once(args.watch_dir, **kwargs)
    except AutoPublishRunError as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 1 if result == "skipped_overlap" else 0


if __name__ == "__main__":
    raise SystemExit(main())
