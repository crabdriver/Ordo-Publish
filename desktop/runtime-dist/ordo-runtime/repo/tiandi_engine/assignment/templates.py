import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from tiandi_engine.models.workbench import TemplateAssignment


@dataclass(frozen=True)
class ThemeEntry:
    theme_id: str
    display_name: str


def scan_theme_pool(themes_dir: Path) -> Tuple[ThemeEntry, ...]:
    if not themes_dir.is_dir():
        return ()
    entries = []
    for path in sorted(themes_dir.glob("*.json")):
        stem = path.stem
        display = stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name") is not None:
                display = str(data["name"])
        except (json.JSONDecodeError, OSError):
            pass
        entries.append(ThemeEntry(theme_id=stem, display_name=display))
    return tuple(entries)


def assign_templates(
    article_ids: Sequence[str],
    *,
    themes_dir: Path,
    assignment_mode: str = "default",
    manual_theme_by_article: Optional[Mapping[str, str]] = None,
    seed: Optional[int] = None,
) -> Tuple[TemplateAssignment, ...]:
    pool = scan_theme_pool(themes_dir)
    if not pool:
        raise ValueError(f"主题池为空或目录无效: {themes_dir}")
    id_to_entry = {e.theme_id: e for e in pool}
    sorted_ids = sorted(id_to_entry.keys())
    default_theme_id = sorted_ids[0]
    rng = random.Random(seed)
    manual = dict(manual_theme_by_article or {})
    assignments = []

    if assignment_mode == "custom":
        for aid in article_ids:
            if aid in manual:
                tid = manual[aid]
                if tid not in id_to_entry:
                    raise ValueError(f"未知主题 id: {tid}")
                ent = id_to_entry[tid]
                assignments.append(
                    TemplateAssignment(
                        article_id=aid,
                        template_mode="custom",
                        theme_id=tid,
                        theme_name=ent.display_name,
                        is_random=False,
                        is_manual_override=True,
                        is_confirmed=False,
                    )
                )
            else:
                ent = id_to_entry[default_theme_id]
                assignments.append(
                    TemplateAssignment(
                        article_id=aid,
                        template_mode="default",
                        theme_id=default_theme_id,
                        theme_name=ent.display_name,
                        is_random=False,
                        is_manual_override=False,
                        is_confirmed=False,
                    )
                )
    else:
        pool_ids = sorted_ids[:]
        rng.shuffle(pool_ids)
        for i, aid in enumerate(article_ids):
            tid = pool_ids[i % len(pool_ids)]
            ent = id_to_entry[tid]
            assignments.append(
                TemplateAssignment(
                    article_id=aid,
                    template_mode="default",
                    theme_id=tid,
                    theme_name=ent.display_name,
                    is_random=True,
                    is_manual_override=False,
                    is_confirmed=False,
                )
            )
    return tuple(assignments)
