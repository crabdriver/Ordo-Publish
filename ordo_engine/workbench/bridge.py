from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from ordo_engine.assignment.covers import COVER_PLATFORMS, assign_covers
from ordo_engine.assignment.templates import assign_templates, scan_theme_pool
from ordo_engine.config import load_engine_config
from ordo_engine.importers.sources import UnsupportedSourceError, import_file, import_pasted_text, list_import_candidates
from ordo_engine.models.workbench import ArticleDraft, ImportFailure, ImportJob, PublishJob
from ordo_engine.runner.pipeline import run_platform_task

WORKBENCH_ROOT = Path(".ordo") / "workbench"
IMPORT_MANIFESTS_ROOT = WORKBENCH_ROOT / "imports"
PUBLISH_LOCK_PATH = WORKBENCH_ROOT / "publish.lock"
LAST_PLAN_PATH = WORKBENCH_ROOT / "last-plan.json"
LAST_RESULT_PATH = WORKBENCH_ROOT / "last-result.json"
SESSION_PATH = Path(".ordo") / "publish-console" / "publish-console-session.json"
BROWSER_SESSION_STATE_PATH = Path(".ordo") / "browser-session" / "state.json"
RECORDS_PATH = Path("publish_records.csv")
SUCCESS_STATUSES = {"published", "scheduled", "draft_only", "success_unknown"}
SKIP_STATUSES = {"skipped_existing"}
BROWSER_PLATFORMS = ("zhihu", "toutiao", "jianshu", "yidian", "bilibili")
PUBLISH_OPTION_MODES = {"auto", "force_on", "force_off", "random"}
REPO_IDENTITY_MARKERS = (
    Path("scripts") / "workbench_bridge.py",
    Path("publish.py"),
    Path("ordo_engine") / "workbench" / "bridge.py",
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_job_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _ensure_base_dir(base_dir) -> Path:
    root = Path(base_dir).expanduser().resolve()
    if os.getenv("ORDO_ENFORCE_REPO_IDENTITY") == "1":
        missing = [str(root / marker) for marker in REPO_IDENTITY_MARKERS if not (root / marker).is_file()]
        if missing:
            raise ValueError(f"当前工作目录不是有效的 Ordo 仓库根目录，缺少: {', '.join(missing)}")
    return root


def _write_atomic_text(path: Path, text: str, *, encoding: str = "utf-8", newline: Optional[str] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as handle:
            handle.write(text)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _coerce_path(value) -> Optional[Path]:
    if value in (None, ""):
        return None
    return Path(value).expanduser().resolve()


def _write_json_snapshot(path: Path, payload) -> None:
    _write_atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_snapshot_state(path: Path):
    if not path.exists():
        return {"status": "missing", "payload": None, "error": None}
    try:
        return {
            "status": "ok",
            "payload": json.loads(path.read_text(encoding="utf-8")),
            "error": None,
        }
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "corrupt", "payload": None, "error": str(exc)}


def _read_json_snapshot(path: Path):
    return _read_json_snapshot_state(path)["payload"]


def _parse_iso_datetime(value) -> Optional[float]:
    if not value:
        return None
    raw = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M"):
        try:
            return time.mktime(time.strptime(raw, fmt))
        except (TypeError, ValueError):
            continue
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(raw)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _browser_session_state_payload(base_dir: Path, config) -> Mapping[str, object]:
    settings = config.get_browser_session_settings()
    payload = _read_json_snapshot(base_dir / BROWSER_SESSION_STATE_PATH)
    if not isinstance(payload, Mapping):
        payload = {}
    platforms = payload.get("platforms")
    if not isinstance(platforms, Mapping):
        platforms = {}
    projected_platforms = {}
    expiring_platforms = []
    relogin_required_platforms = []
    now_ts = time.time()
    now_iso = _now_iso()
    remind_after_seconds = int(settings["remind_after_days"]) * 24 * 60 * 60
    raw_platforms = dict(platforms)
    should_persist_reminder = False
    for platform, raw_entry in platforms.items():
        if not isinstance(raw_entry, Mapping):
            continue
        entry = dict(raw_entry)
        status = str(entry.get("status") or "")
        last_healthy_at = _parse_iso_datetime(entry.get("last_healthy_at"))
        if status == "healthy" and last_healthy_at and (now_ts - last_healthy_at) >= remind_after_seconds:
            status = "expiring_soon"
        entry["status"] = status or "healthy"
        if entry["status"] in {"expiring_soon", "expired_or_relogin_required"} and not entry.get("last_reminded_at"):
            entry["last_reminded_at"] = now_iso
            should_persist_reminder = True
        raw_platforms[str(platform)] = entry
        projected_platforms[str(platform)] = entry
        if entry["status"] == "expiring_soon":
            expiring_platforms.append(str(platform))
        if entry["status"] == "expired_or_relogin_required":
            relogin_required_platforms.append(str(platform))
    if should_persist_reminder:
        persisted = dict(payload)
        persisted["platforms"] = raw_platforms
        persisted["updated_at"] = persisted.get("updated_at") or now_iso
        _write_json_snapshot(base_dir / BROWSER_SESSION_STATE_PATH, persisted)
    return {
        "mode": str(payload.get("mode") or ("managed" if settings["enabled"] else "fallback_system_browser")),
        "last_checked_at": payload.get("last_checked_at"),
        "updated_at": payload.get("updated_at"),
        "platforms": projected_platforms,
        "expiring_platforms": expiring_platforms,
        "relogin_required_platforms": relogin_required_platforms,
    }


def _normalize_publish_option_mode(value, *, field_name: str) -> str:
    mode = str(value or "auto")
    if mode not in PUBLISH_OPTION_MODES:
        raise ValueError(f"{field_name} 仅支持: auto / force_on / force_off / random")
    return mode


def _normalize_scheduled_publish_at(value) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("scheduled_publish_at 必须是 ISO 本地时间，例如 2026-03-30T09:30") from exc
    return parsed.strftime("%Y-%m-%dT%H:%M")


def _read_csv_records_state(path: Path):
    if not path.exists():
        return {"status": "missing", "rows": [], "error": None}
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        return {"status": "ok", "rows": rows, "error": None}
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return {"status": "corrupt", "rows": [], "error": str(exc)}


def _coerce_draft(raw) -> ArticleDraft:
    if isinstance(raw, ArticleDraft):
        return raw
    if not isinstance(raw, Mapping):
        raise TypeError(f"unsupported draft payload: {type(raw)!r}")
    image_paths = tuple(_coerce_path(item) for item in raw.get("image_paths", []) if item)
    return ArticleDraft(
        article_id=str(raw["article_id"]),
        title=str(raw.get("title") or "Untitled"),
        body_markdown=str(raw.get("body_markdown") or ""),
        source_path=_coerce_path(raw.get("source_path")),
        source_kind=str(raw.get("source_kind") or "markdown"),
        image_paths=tuple(path for path in image_paths if path is not None),
        word_count=int(raw.get("word_count") or 0),
        template_mode=str(raw.get("template_mode") or "default"),
        theme_name=raw.get("theme_name"),
        is_config_complete=bool(raw.get("is_config_complete", False)),
    )


def _materialize_markdown(draft: ArticleDraft) -> str:
    body = draft.body_markdown.strip()
    if draft.source_kind == "markdown" and body.startswith("#"):
        return draft.body_markdown
    if body:
        return f"# {draft.title}\n\n{body}\n"
    return f"# {draft.title}\n"


def _write_staged_articles(base_dir: Path, drafts: Sequence[ArticleDraft], job_id: str):
    root = base_dir / WORKBENCH_ROOT / "articles" / job_id
    root.mkdir(parents=True, exist_ok=True)
    staged = []
    for draft in drafts:
        path = root / f"{draft.article_id}.md"
        path.write_text(_materialize_markdown(draft), encoding="utf-8")
        staged.append({"article_id": draft.article_id, "markdown_path": str(path)})
    return staged


def _write_import_manifest(base_dir: Path, job: ImportJob) -> Path:
    path = base_dir / IMPORT_MANIFESTS_ROOT / f"{job.job_id}.json"
    _write_json_snapshot(path, job.to_dict())
    return path


def _acquire_publish_lock(base_dir: Path, publish_job_id: str) -> Path:
    path = base_dir / PUBLISH_LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("已有发布任务正在执行，请等待当前任务结束后再试。") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"job_id": publish_job_id, "locked_at": _now_iso()}, ensure_ascii=False))
    return path


def _folder_import_failures_and_drafts(directory: Path) -> tuple[tuple[ArticleDraft, ...], tuple[ImportFailure, ...], int]:
    drafts: list[ArticleDraft] = []
    failures: list[ImportFailure] = []
    files = sorted(
        (path for path in directory.iterdir() if path.is_file() and not path.name.startswith(".")),
        key=lambda item: item.name.lower(),
    )
    for path in files:
        try:
            drafts.append(import_file(path))
        except UnsupportedSourceError as exc:
            failures.append(
                ImportFailure(
                    source_path=path.resolve(),
                    source_kind=(path.suffix.lower().lstrip(".") or "unknown"),
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
        except Exception as exc:
            failures.append(
                ImportFailure(
                    source_path=path.resolve(),
                    source_kind=(path.suffix.lower().lstrip(".") or "unknown"),
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
    return tuple(drafts), tuple(failures), len(files)


def _theme_pool_payload(base_dir: Path):
    config = load_engine_config(base_dir)
    entries = scan_theme_pool(config.resolve_themes_dir())
    return {
        **config.discover_theme_pool(),
        "entries": [
            {
                "theme_id": entry.theme_id,
                "display_name": entry.display_name,
            }
            for entry in entries
        ],
    }


def _wechat_settings_payload(base_dir: Path):
    config = load_engine_config(base_dir)
    return {
        "settings": config.get_wechat_settings(),
        "status": config.get_wechat_config_status(),
    }


def _write_simple_env_updates(env_path: Path, updates: Mapping[str, Optional[str]], clear_fields: Sequence[str] = ()):
    normalized = {str(key): (None if value is None else str(value)) for key, value in updates.items()}
    clear_keys = {str(item) for item in clear_fields}
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen = set()
    out = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            out.append(raw_line)
            continue
        key, _old_value = raw_line.split("=", 1)
        key = key.strip()
        if key in normalized:
            seen.add(key)
            if key in clear_keys:
                out.append(f"{key}=")
                continue
            value = normalized[key]
            if value not in (None, ""):
                out.append(f"{key}={value}")
            else:
                out.append(raw_line)
        else:
            out.append(raw_line)
    for key, value in normalized.items():
        if key not in seen:
            if key in clear_keys:
                out.append(f"{key}=")
            elif value not in (None, ""):
                out.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def read_wechat_settings(base_dir):
    root = _ensure_base_dir(base_dir)
    payload = _wechat_settings_payload(root)
    return {
        **payload["settings"],
        "status": payload["status"],
    }


def save_wechat_settings(base_dir, *, app_id=None, secret=None, author=None, clear_fields: Sequence[str] = ()):
    root = _ensure_base_dir(base_dir)
    env_path = root / "secrets.env"
    _write_simple_env_updates(
        env_path,
        {
            "WECHAT_APPID": app_id,
            "WECHAT_SECRET": secret,
            "WECHAT_AUTHOR": author,
        },
        clear_fields=clear_fields,
    )
    return read_wechat_settings(root)


def _apply_manual_cover_overrides(cover_assignments, manual_cover_by_article_platform):
    manual = dict(manual_cover_by_article_platform or {})
    if not manual:
        return tuple(cover_assignments)

    by_key = {(item.article_id, item.platform): item for item in cover_assignments}
    for raw_key, raw_path in manual.items():
        if isinstance(raw_key, str):
            article_id, _, platform = raw_key.partition(":")
        else:
            article_id, platform = raw_key
        cover_path = _coerce_path(raw_path)
        if not article_id or not platform or cover_path is None:
            continue
        if (article_id, platform) in by_key:
            original = by_key[(article_id, platform)]
            by_key[(article_id, platform)] = replace(
                original,
                cover_path=cover_path,
                cover_source="manual",
                is_random=False,
                is_manual_override=True,
            )
        else:
            from ordo_engine.models.workbench import CoverAssignment

            by_key[(article_id, platform)] = CoverAssignment(
                article_id=article_id,
                platform=platform,
                cover_path=cover_path,
                cover_source="manual",
                is_random=False,
                is_manual_override=True,
            )
    return tuple(by_key[key] for key in sorted(by_key))


def import_sources(
    base_dir,
    *,
    import_mode: str,
    source_path: Optional[str] = None,
    pasted_text: Optional[str] = None,
    job_id: Optional[str] = None,
    imported_at: Optional[str] = None,
):
    root = _ensure_base_dir(base_dir)
    drafts: tuple[ArticleDraft, ...]
    failures: tuple[ImportFailure, ...] = ()
    source_count = 0
    source = _coerce_path(source_path)
    mode = str(import_mode)
    if mode == "paste":
        drafts = (import_pasted_text(pasted_text or ""),)
        source_count = 1 if (pasted_text or "").strip() else 0
    elif mode == "file":
        if source is None:
            raise ValueError("file import requires source_path")
        drafts = (import_file(source),)
        source_count = 1
    elif mode == "folder":
        if source is None:
            raise ValueError("folder import requires source_path")
        drafts, failures, source_count = _folder_import_failures_and_drafts(source)
    else:
        raise ValueError(f"unsupported import_mode: {mode}")

    job = ImportJob(
        job_id=job_id or _new_job_id("import"),
        import_mode=mode,
        source_path=source,
        pasted_preview=(pasted_text or "")[:120] if mode == "paste" else None,
        imported_at=imported_at or _now_iso(),
        source_count=source_count or len(drafts),
        drafts=drafts,
        failures=failures,
    )
    manifest_path = _write_import_manifest(root, job)
    job = replace(job, manifest_path=manifest_path)
    _write_import_manifest(root, job)
    return {"job": job.to_dict(), "resources": discover_resources(root)}


def discover_resources(base_dir):
    root = _ensure_base_dir(base_dir)
    config = load_engine_config(root)
    return {
        "theme_pool": _theme_pool_payload(root),
        "cover_pool": config.discover_cover_pool(),
        "wechat": _wechat_settings_payload(root),
        "browser": {
            "browser_platforms": list(BROWSER_PLATFORMS),
            "remote_debugging_required": True,
            "login_required_platforms": list(BROWSER_PLATFORMS),
            "managed_session": config.get_browser_session_settings(),
            "session_state": _browser_session_state_payload(root, config),
        },
        "runtime": {
            "repo_root": str(root),
            "python_executable": sys.executable,
        },
        "config_warning": config.project_config_warning,
        "defaults": {
            "template_mode": config.get_default_template_mode(),
            "cover_repeat_window": config.get_cover_repeat_window(),
            "cover_mode": "auto",
            "ai_declaration_mode": "auto",
            "scheduled_publish_at": None,
        },
    }


def plan_publish_job(
    base_dir,
    *,
    drafts,
    platforms: Sequence[str],
    mode: str = "draft",
    continue_on_error: bool = False,
    template_mode: Optional[str] = None,
    manual_theme_by_article: Optional[Mapping[str, str]] = None,
    manual_cover_by_article_platform: Optional[Mapping[str, str]] = None,
    cover_mode: str = "auto",
    ai_declaration_mode: str = "auto",
    scheduled_publish_at: Optional[str] = None,
    seed: Optional[int] = None,
    recent_cover_paths: Optional[Sequence[str]] = None,
    job_id: Optional[str] = None,
    clear_last_result: bool = False,
):
    root = _ensure_base_dir(base_dir)
    config = load_engine_config(root)
    normalized_cover_mode = _normalize_publish_option_mode(cover_mode, field_name="cover_mode")
    normalized_ai_declaration_mode = _normalize_publish_option_mode(
        ai_declaration_mode, field_name="ai_declaration_mode"
    )
    normalized_scheduled_publish_at = _normalize_scheduled_publish_at(scheduled_publish_at)
    draft_objects = tuple(_coerce_draft(item) for item in drafts)
    article_ids = tuple(draft.article_id for draft in draft_objects)
    selected_platforms = tuple(str(platform) for platform in platforms)
    publish_job_id = job_id or _new_job_id("publish")
    assignment_mode = template_mode or config.get_default_template_mode()

    theme_entries = scan_theme_pool(config.resolve_themes_dir())
    template_assignments = ()
    if theme_entries:
        template_assignments = assign_templates(
            article_ids,
            themes_dir=config.resolve_themes_dir(),
            assignment_mode=assignment_mode,
            manual_theme_by_article=manual_theme_by_article,
            seed=seed,
        )

    cover_assignments = ()
    if normalized_cover_mode != "force_off" and any(platform in COVER_PLATFORMS for platform in selected_platforms):
        cover_pool = config.discover_cover_pool()
        if cover_pool.get("ok"):
            cover_assignments = assign_covers(
                article_ids,
                selected_platforms,
                cover_dir=config.resolve_cover_dir(),
                recent_cover_paths=tuple(recent_cover_paths or ()),
                repeat_window=config.get_cover_repeat_window(),
                seed=seed,
            )
    if normalized_cover_mode != "force_off":
        cover_assignments = _apply_manual_cover_overrides(cover_assignments, manual_cover_by_article_platform)
    required_cover_pairs = {
        (draft.article_id, platform) for draft in draft_objects for platform in selected_platforms if platform in COVER_PLATFORMS
    }
    assigned_cover_pairs = {
        (item.article_id, item.platform) for item in cover_assignments if item.cover_path is not None
    }
    if normalized_cover_mode == "force_on" and required_cover_pairs - assigned_cover_pairs:
        raise ValueError("当前已明确要求启用封面，但可用封面池或手动封面覆盖不足，请先补齐封面资源。")

    template_by_article = {item.article_id: item for item in template_assignments}
    enriched_drafts = []
    for draft in draft_objects:
        assignment = template_by_article.get(draft.article_id)
        enriched_drafts.append(
            replace(
                draft,
                template_mode=assignment.template_mode if assignment else assignment_mode,
                theme_name=assignment.theme_name if assignment else draft.theme_name,
                is_config_complete=True,
            )
        )

    staged_articles = _write_staged_articles(root, enriched_drafts, publish_job_id)
    staged_by_article = {item["article_id"]: item["markdown_path"] for item in staged_articles}
    cover_by_pair = {(item.article_id, item.platform): item for item in cover_assignments}
    context_map = []
    for draft in enriched_drafts:
        assignment = template_by_article.get(draft.article_id)
        for platform in selected_platforms:
            cover_assignment = cover_by_pair.get((draft.article_id, platform))
            context_map.append(
                {
                    "article_id": draft.article_id,
                    "platform": platform,
                    "markdown_path": staged_by_article[draft.article_id],
                    "theme_name": assignment.theme_id if assignment else None,
                    "template_mode": assignment.template_mode if assignment else assignment_mode,
                    "cover_path": str(cover_assignment.cover_path) if cover_assignment and cover_assignment.cover_path else None,
                    "cover_mode": normalized_cover_mode,
                    "ai_declaration_mode": normalized_ai_declaration_mode,
                    "scheduled_publish_at": (
                        normalized_scheduled_publish_at if mode == "publish" and platform == "toutiao" else None
                    ),
                }
            )

    publish_job = PublishJob(
        job_id=publish_job_id,
        article_ids=tuple(draft.article_id for draft in enriched_drafts),
        platforms=selected_platforms,
        status="pending",
        scheduled_publish_at=normalized_scheduled_publish_at if mode == "publish" else None,
    )
    plan_payload = {
        "publish_job": publish_job.to_dict(),
        "mode": mode,
        "continue_on_error": bool(continue_on_error),
        "cover_mode": normalized_cover_mode,
        "ai_declaration_mode": normalized_ai_declaration_mode,
        "scheduled_publish_at": normalized_scheduled_publish_at if mode == "publish" else None,
        "drafts": [draft.to_dict() for draft in enriched_drafts],
        "template_assignments": [item.to_dict() for item in template_assignments],
        "cover_assignments": [item.to_dict() for item in cover_assignments],
        "staged_articles": staged_articles,
        "context_map": context_map,
        "resources": discover_resources(root),
    }
    _write_json_snapshot(root / LAST_PLAN_PATH, plan_payload)
    last_result_path = root / LAST_RESULT_PATH
    if clear_last_result and last_result_path.exists():
        last_result_path.unlink()
    return plan_payload


def _build_context_lookup(plan_payload):
    mapping = {}
    for item in plan_payload.get("context_map", []):
        mapping[(item["article_id"], item["platform"])] = item
    return mapping


def _status_counts(results):
    success = failure = skipped = 0
    recoverable = True
    for result in results:
        status = result.get("status")
        if status in SUCCESS_STATUSES:
            success += 1
        elif status in SKIP_STATUSES:
            skipped += 1
        else:
            failure += 1
            recoverable = recoverable and bool(result.get("retryable", False))
    return success, failure, skipped, recoverable


def run_publish_job(base_dir, plan_payload, *, registry=None, append_record=None, event_sink=None):
    root = _ensure_base_dir(base_dir)
    staged_by_article = {item["article_id"]: item["markdown_path"] for item in plan_payload.get("staged_articles", [])}
    draft_by_article = {item["article_id"]: item for item in plan_payload.get("drafts", [])}
    publish_job = dict(plan_payload["publish_job"])
    mode = plan_payload.get("mode", "draft")
    continue_on_error = bool(plan_payload.get("continue_on_error", False))
    context_entries = list(plan_payload.get("context_map", []))
    events = []
    lock_path = _acquire_publish_lock(root, str(publish_job.get("job_id") or "publish"))

    def emit(event):
        events.append(event)
        if event_sink:
            event_sink(event)
    try:
        emit(
            {
                "type": "job_started",
                "job_id": publish_job["job_id"],
                "article_ids": list(publish_job["article_ids"]),
                "platforms": list(publish_job["platforms"]),
                "mode": mode,
            }
        )
        results = []
        current_article_id = None
        for context in context_entries:
            article_id = context["article_id"]
            platform = context["platform"]
            if article_id != current_article_id:
                current_article_id = article_id
                emit(
                    {
                        "type": "article_started",
                        "job_id": publish_job["job_id"],
                        "article_id": article_id,
                        "title": draft_by_article.get(article_id, {}).get("title", article_id),
                        "markdown_path": staged_by_article.get(article_id, context["markdown_path"]),
                    }
                )
            emit(
                {
                    "type": "platform_started",
                    "job_id": publish_job["job_id"],
                    "article_id": article_id,
                    "platform": platform,
                }
            )
            result = run_platform_task(
                base_dir=root,
                platform=platform,
                markdown_file=context["markdown_path"],
                mode=mode,
                theme_name=context.get("theme_name"),
                cover_path=context.get("cover_path"),
                template_mode=context.get("template_mode"),
                article_id=article_id,
                cover_mode=context.get("cover_mode"),
                ai_declaration_mode=context.get("ai_declaration_mode"),
                scheduled_publish_at=context.get("scheduled_publish_at"),
                registry=registry,
            )
            result["article"] = context["markdown_path"]
            results.append(result)
            if append_record:
                append_record(result)
            emit(
                {
                    "type": "platform_finished",
                    "job_id": publish_job["job_id"],
                    "article_id": article_id,
                    "platform": platform,
                    "result": result,
                }
            )
            if result["returncode"] != 0 and not continue_on_error:
                break

        success_count, failure_count, skip_count, recoverable = _status_counts(results)
        last_failed_result = next((item for item in reversed(results) if item.get("returncode") != 0), None)
        publish_job.update(
            {
                "status": "completed" if failure_count == 0 else "failed",
                "current_step": "done",
                "success_count": success_count,
                "failure_count": failure_count,
                "skip_count": skip_count,
                "recoverable": recoverable,
                "error_summary": last_failed_result["summary"] if last_failed_result else "",
            }
        )
        emit(
            {
                "type": "job_finished",
                "job_id": publish_job["job_id"],
                "publish_job": publish_job,
                "result_count": len(results),
            }
        )
        payload = {
            "publish_job": publish_job,
            "events": events,
            "results": results,
        }
        _write_json_snapshot(root / LAST_RESULT_PATH, payload)
        return payload
    finally:
        try:
            Path(lock_path).unlink()
        except OSError:
            pass


def read_recent_history(base_dir, *, limit: int = 20):
    root = _ensure_base_dir(base_dir)
    records_path = root / RECORDS_PATH
    session_path = root / SESSION_PATH
    records_state = _read_csv_records_state(records_path)
    rows = records_state["rows"]
    records = rows[-max(0, int(limit)) :] if limit is not None else rows
    session_state = _read_json_snapshot_state(session_path)
    last_plan_state = _read_json_snapshot_state(root / LAST_PLAN_PATH)
    last_result_state = _read_json_snapshot_state(root / LAST_RESULT_PATH)
    session = session_state["payload"]
    last_plan = last_plan_state["payload"]
    last_result = last_result_state["payload"]
    missing_staged_articles = []
    for item in (last_plan or {}).get("staged_articles", []):
        markdown_path = Path(str(item.get("markdown_path") or "")).expanduser()
        if not markdown_path.is_absolute():
            markdown_path = (root / markdown_path).resolve()
        if not markdown_path.exists():
            missing_staged_articles.append(
                {
                    "article_id": item.get("article_id"),
                    "markdown_path": str(markdown_path),
                }
            )
    issues = [
        name
        for name, state in (
            ("records", records_state),
            ("session", session_state),
            ("last_plan", last_plan_state),
            ("last_result", last_result_state),
        )
        if state["status"] == "corrupt"
    ]
    current_job_id = (last_plan or {}).get("publish_job", {}).get("job_id")
    result_job_id = (last_result or {}).get("publish_job", {}).get("job_id")
    if issues:
        recovery_status = "snapshot_corrupted"
    elif missing_staged_articles:
        recovery_status = "result_missing"
    elif last_plan and last_result and current_job_id and current_job_id == result_job_id:
        recovery_status = "recoverable"
    elif session and not last_plan and not last_result:
        recovery_status = "session_only"
    elif last_plan or last_result or session:
        recovery_status = "result_missing"
    else:
        recovery_status = "empty"
    can_restore_plan = bool(last_plan) and not issues and not missing_staged_articles
    failure_count = int((last_result or {}).get("publish_job", {}).get("failure_count") or 0)
    has_failed_results = failure_count > 0 or any(
        item.get("status") not in SUCCESS_STATUSES and item.get("status") not in SKIP_STATUSES
        for item in (last_result or {}).get("results", [])
    )
    return {
        "records": records,
        "session": session,
        "last_plan": last_plan,
        "last_result": last_result,
        "recovery": {
            "status": recovery_status,
            "issues": issues,
            "missing_staged_articles": missing_staged_articles,
            "can_restore_plan": can_restore_plan,
            "can_restore_failures": can_restore_plan and bool(last_result) and has_failed_results,
        },
    }


def handle_bridge_command(base_dir, payload, *, registry=None, append_record=None):
    if not isinstance(payload, Mapping):
        raise TypeError("bridge payload must be a mapping")
    command = payload.get("command")
    if command == "import_sources":
        return import_sources(
            base_dir,
            import_mode=payload.get("import_mode", "paste"),
            source_path=payload.get("source_path"),
            pasted_text=payload.get("pasted_text"),
            job_id=payload.get("job_id"),
            imported_at=payload.get("imported_at"),
        )
    if command == "discover_resources":
        return discover_resources(base_dir)
    if command == "read_wechat_settings":
        return read_wechat_settings(base_dir)
    if command == "save_wechat_settings":
        return save_wechat_settings(
            base_dir,
            app_id=str(payload["app_id"]) if "app_id" in payload and payload.get("app_id") is not None else None,
            secret=str(payload["secret"]) if "secret" in payload and payload.get("secret") is not None else None,
            author=str(payload["author"]) if "author" in payload and payload.get("author") is not None else None,
            clear_fields=tuple(payload.get("clear_fields", ()) or ()),
        )
    if command == "plan_publish_job":
        return plan_publish_job(
            base_dir,
            drafts=payload.get("drafts", ()),
            platforms=payload.get("platforms", ()),
            mode=payload.get("mode", "draft"),
            continue_on_error=payload.get("continue_on_error", False),
            template_mode=payload.get("template_mode"),
            manual_theme_by_article=payload.get("manual_theme_by_article"),
            manual_cover_by_article_platform=payload.get("manual_cover_by_article_platform"),
            cover_mode=payload.get("cover_mode", "auto"),
            ai_declaration_mode=payload.get("ai_declaration_mode", "auto"),
            scheduled_publish_at=payload.get("scheduled_publish_at"),
            seed=payload.get("seed"),
            recent_cover_paths=payload.get("recent_cover_paths"),
            job_id=payload.get("job_id"),
            clear_last_result=payload.get("clear_last_result", False),
        )
    if command == "run_publish_job":
        return run_publish_job(
            base_dir,
            payload["plan"],
            registry=registry,
            append_record=append_record,
        )
    if command == "run_publish_job_stream":
        return run_publish_job(
            base_dir,
            payload["plan"],
            registry=registry,
            append_record=append_record,
        )
    if command == "read_recent_history":
        return read_recent_history(base_dir, limit=int(payload.get("limit", 20)))
    raise ValueError(f"unsupported bridge command: {command}")
