from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ordo_engine.assignment.covers import COVER_PLATFORMS, assign_covers, list_cover_files
from ordo_engine.assignment.templates import assign_templates, scan_theme_pool
from ordo_engine.config import load_engine_config
from ordo_engine.models.workbench import ArticleDraft

WORKBENCH_ROOT = Path(".ordo") / "workbench"
MATRIX_ROOT = WORKBENCH_ROOT / "matrices"


def select_representative_drafts(drafts: Sequence[ArticleDraft], max_count: int = 6) -> tuple[ArticleDraft, ...]:
    seen_kinds: set[str] = set()
    selected: list[ArticleDraft] = []
    for draft in drafts:
        if draft.source_kind in seen_kinds:
            continue
        selected.append(draft)
        seen_kinds.add(draft.source_kind)
        if len(selected) >= max_count:
            return tuple(selected)
    for draft in drafts:
        if draft in selected:
            continue
        selected.append(draft)
        if len(selected) >= max_count:
            break
    return tuple(selected)


def _coerce_draft(raw) -> ArticleDraft:
    if isinstance(raw, ArticleDraft):
        return raw
    if not isinstance(raw, Mapping):
        raise TypeError(f"unsupported draft payload: {type(raw)!r}")
    return ArticleDraft(
        article_id=str(raw["article_id"]),
        title=str(raw.get("title") or "Untitled"),
        body_markdown=str(raw.get("body_markdown") or ""),
        source_path=Path(raw["source_path"]).expanduser().resolve() if raw.get("source_path") else None,
        source_kind=str(raw.get("source_kind") or "markdown"),
        image_paths=tuple(Path(item).expanduser().resolve() for item in raw.get("image_paths", []) if item),
        word_count=int(raw.get("word_count") or 0),
        template_mode=str(raw.get("template_mode") or "default"),
        theme_name=raw.get("theme_name"),
        is_config_complete=bool(raw.get("is_config_complete", False)),
    )


def _write_matrix(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _production_manual_theme_map(assignments: Iterable[object]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in assignments:
        theme_id = getattr(item, "theme_id", None)
        article_id = getattr(item, "article_id", None)
        if theme_id and article_id:
            out[str(article_id)] = str(theme_id)
    return out


def _production_manual_cover_map(assignments: Iterable[object]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in assignments:
        cover_path = getattr(item, "cover_path", None)
        article_id = getattr(item, "article_id", None)
        platform = getattr(item, "platform", None)
        if cover_path and article_id and platform:
            out[f"{article_id}:{platform}"] = str(Path(cover_path).resolve())
    return out


def build_publish_matrix(
    base_dir,
    *,
    drafts: Sequence[ArticleDraft],
    platforms: Sequence[str],
    seed: int = 20260329,
    matrix_id: str | None = None,
    cover_dir_override=None,
):
    root = Path(base_dir).expanduser().resolve()
    config = load_engine_config(root)
    draft_objects = tuple(_coerce_draft(item) for item in drafts)
    representative = select_representative_drafts(draft_objects)
    representative_ids = [draft.article_id for draft in representative]

    themes_dir = config.resolve_themes_dir()
    theme_entries = scan_theme_pool(themes_dir)
    theme_ids = [entry.theme_id for entry in theme_entries]
    custom_switch_sequences = {
        article_id: theme_ids[: min(3, len(theme_ids))] for article_id in representative_ids if theme_ids
    }

    production_assignments = ()
    random_preview_assignments = ()
    custom_preview_assignments = ()
    if theme_entries:
        production_assignments = assign_templates(
            [draft.article_id for draft in draft_objects],
            themes_dir=themes_dir,
            assignment_mode="default",
            seed=seed,
        )
        random_preview_assignments = assign_templates(
            representative_ids,
            themes_dir=themes_dir,
            assignment_mode="default",
            seed=seed,
        )
        custom_preview_assignments = assign_templates(
            representative_ids,
            themes_dir=themes_dir,
            assignment_mode="custom",
            manual_theme_by_article={
                article_id: theme_ids[index % len(theme_ids)] for index, article_id in enumerate(representative_ids)
            }
            if theme_ids
            else None,
            seed=seed,
        )

    cover_dir = Path(cover_dir_override).expanduser().resolve() if cover_dir_override is not None else config.resolve_cover_dir()
    cover_files = [str(path.resolve()) for path in list_cover_files(cover_dir)]
    production_cover_assignments = assign_covers(
        [draft.article_id for draft in draft_objects],
        platforms,
        cover_dir=cover_dir,
        recent_cover_paths=(),
        repeat_window=config.get_cover_repeat_window(),
        seed=seed,
    )
    missing_cover_case = {
        "article_id": representative_ids[0] if representative_ids else None,
        "platform_expectations": {
            platform: ("blocked" if platform in COVER_PLATFORMS else "allowed")
            for platform in platforms
        },
    }

    resolved_matrix_id = matrix_id or f"matrix-{uuid.uuid4().hex}"
    payload = {
        "matrix_id": resolved_matrix_id,
        "seed": seed,
        "representative_article_ids": representative_ids,
        "production_strategy": {
            "template_mode": "custom" if production_assignments else "default",
            "manual_theme_by_article": _production_manual_theme_map(production_assignments),
            "manual_cover_by_article_platform": _production_manual_cover_map(production_cover_assignments),
        },
        "template_matrix": {
            "theme_ids": theme_ids,
            "random_preview_assignments": [item.to_dict() for item in random_preview_assignments],
            "custom_preview_assignments": [item.to_dict() for item in custom_preview_assignments],
            "switch_sequences": custom_switch_sequences,
        },
        "cover_matrix": {
            "cover_dir": str(cover_dir),
            "cover_paths": cover_files,
            "production_assignments": [item.to_dict() for item in production_cover_assignments],
            "missing_cover_case": missing_cover_case,
        },
    }
    matrix_path = root / MATRIX_ROOT / f"{resolved_matrix_id}.json"
    _write_matrix(matrix_path, payload)
    payload["matrix_path"] = str(matrix_path)
    return payload
