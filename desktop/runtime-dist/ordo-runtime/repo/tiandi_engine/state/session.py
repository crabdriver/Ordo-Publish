import json
import time
from pathlib import Path

from tiandi_engine.results.errors import ErrorType


SUCCESS_STATUSES = {"published", "scheduled", "draft_only", "success_unknown"}
SKIPPED_STATUSES = {"skipped_existing"}
FAILURE_STATUSES = {"failed", "limit_reached"}


def _normalize_error_type(error_type):
    if isinstance(error_type, ErrorType):
        return error_type.value
    if error_type:
        return str(error_type)
    return None


def _empty_platform_state(platforms):
    return {
        platform: {
            "status": "pending",
            "detail": "",
            "step": "pending",
            "error_type": None,
            "retryable": False,
        }
        for platform in platforms
    }


def _build_summary(platforms):
    return {
        "total_articles": 0,
        "completed_articles": 0,
        "success_articles": 0,
        "partial_failed_articles": 0,
        "failed_articles": 0,
        "platform_failures": {platform: 0 for platform in platforms},
    }


def _collect_unknown_article_ids(session, assignments):
    known_article_ids = {item["article_id"] for item in session["items"]}
    missing = []
    for assignment in assignments:
        if assignment.article_id not in known_article_ids:
            missing.append(assignment.article_id)
    return tuple(missing)


def merge_assignments_into_session(session, template_assignments, cover_assignments):
    missing_template_articles = _collect_unknown_article_ids(session, template_assignments)
    missing_cover_articles = _collect_unknown_article_ids(session, cover_assignments)
    missing_article_ids = missing_template_articles + missing_cover_articles
    if missing_article_ids:
        missing_text = ", ".join(sorted(set(missing_article_ids)))
        raise ValueError(f"session 中不存在这些 article_id: {missing_text}")

    by_article = {item["article_id"]: item for item in session["items"]}
    for assignment in template_assignments:
        by_article[assignment.article_id]["template_assignment"] = assignment.to_dict()
    for cover in cover_assignments:
        item = by_article[cover.article_id]
        bucket = item.setdefault("cover_assignments", {})
        bucket[cover.platform] = cover.to_dict()


def collect_recent_cover_paths(session, limit=None):
    cover_paths = []
    for item in session.get("items", []):
        for assignment in item.get("cover_assignments", {}).values():
            cover_path = assignment.get("cover_path")
            if cover_path:
                cover_paths.append(cover_path)
    if limit is None:
        return tuple(cover_paths)
    return tuple(cover_paths[-max(0, int(limit)) :])


def build_session(article_paths, platforms, mode, available_themes, default_theme):
    items = []
    for index, article_path in enumerate(article_paths):
        path = Path(article_path)
        items.append(
            {
                "index": index,
                "article_id": path.stem,
                "article_path": str(article_path),
                "article_name": path.name,
                "title": path.stem,
                "status": "pending",
                "selected_theme": default_theme,
                "message": "",
                "platforms": _empty_platform_state(platforms),
                "template_assignment": None,
                "cover_assignments": {},
            }
        )

    session = {
        "version": 3,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "platforms": list(platforms),
        "available_themes": list(available_themes),
        "current_index": 0,
        "current_theme": default_theme,
        "summary": _build_summary(platforms),
        "items": items,
        "notice": None,
    }
    session["summary"]["total_articles"] = len(items)
    if items:
        mark_reviewing(session, 0)
    return session


def mark_reviewing(session, index):
    session["current_index"] = index
    item = session["items"][index]
    item["status"] = "reviewing"
    session["current_theme"] = item.get("selected_theme") or session.get("current_theme")


def mark_publishing(session, index):
    session["items"][index]["status"] = "publishing"


def record_platform_result(session, index, result):
    platform = result["platform"]
    platform_state = session["items"][index]["platforms"][platform]
    raw_status = result.get("status", "")

    if raw_status in SUCCESS_STATUSES:
        platform_state["status"] = "success"
    elif raw_status in SKIPPED_STATUSES:
        platform_state["status"] = "skipped"
    else:
        platform_state["status"] = "failed"

    detail_parts = [part.strip() for part in (result.get("stdout", ""), result.get("stderr", "")) if part.strip()]
    platform_state["detail"] = "\n".join(detail_parts)
    platform_state["step"] = result.get("stage", "pending")
    platform_state["error_type"] = _normalize_error_type(result.get("error_type"))
    platform_state["retryable"] = bool(result.get("retryable", False))


def _rebuild_summary(session):
    summary = _build_summary(session["platforms"])
    summary["total_articles"] = len(session["items"])

    for item in session["items"]:
        if item["status"] in {"success", "partial_failed", "failed"}:
            summary["completed_articles"] += 1
        if item["status"] == "success":
            summary["success_articles"] += 1
        elif item["status"] == "partial_failed":
            summary["partial_failed_articles"] += 1
        elif item["status"] == "failed":
            summary["failed_articles"] += 1

        for platform, platform_state in item["platforms"].items():
            if platform_state["status"] == "failed":
                summary["platform_failures"][platform] += 1
    session["summary"] = summary


def finalize_article(session, index):
    item = session["items"][index]
    platform_statuses = [entry["status"] for entry in item["platforms"].values()]
    success_count = sum(status in {"success", "skipped"} for status in platform_statuses)
    failure_count = sum(status == "failed" for status in platform_statuses)

    if success_count and failure_count:
        item["status"] = "partial_failed"
        item["message"] = "部分平台发布失败，已记录失败项。"
    elif success_count:
        item["status"] = "success"
        item["message"] = "当前文章已完成发布。"
    else:
        item["status"] = "failed"
        item["message"] = "当前文章全部平台发布失败。"

    _rebuild_summary(session)
    return item["status"]


def advance_after_success(session, index):
    if session["items"][index]["status"] not in {"success", "partial_failed"}:
        return False

    next_index = index + 1
    if next_index >= len(session["items"]):
        return False

    mark_reviewing(session, next_index)
    return True


def save_session(path, session):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
