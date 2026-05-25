import random
from pathlib import Path
from typing import Optional, Sequence, Tuple

from PIL import Image

from tiandi_engine.models.workbench import CoverAssignment

COVER_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
COVER_PLATFORMS = ("toutiao", "yidian", "zhihu")
MIN_COVER_DIMENSION = 64


class CoverPoolError(RuntimeError):
    pass


def _is_usable_cover_image(candidate: Path) -> bool:
    try:
        with Image.open(candidate) as image:
            width, height = image.size
    except Exception:
        return False
    return width >= MIN_COVER_DIMENSION and height >= MIN_COVER_DIMENSION


def list_cover_files(cover_dir: Path) -> Tuple[Path, ...]:
    if not cover_dir.exists():
        raise CoverPoolError(f"封面目录不存在: {cover_dir}")
    if not cover_dir.is_dir():
        raise CoverPoolError(f"封面路径不是目录: {cover_dir}")
    files = []
    for candidate in cover_dir.iterdir():
        if (
            candidate.is_file()
            and candidate.suffix.lower() in COVER_IMAGE_SUFFIXES
            and _is_usable_cover_image(candidate)
        ):
            files.append(candidate)
    if not files:
        raise CoverPoolError(f"封面目录为空（无可用图片文件）: {cover_dir}")
    return tuple(sorted(files))


def _resolved_path_strings(paths: Sequence[str]) -> set:
    resolved = set()
    for raw in paths:
        if not raw:
            continue
        try:
            resolved.add(str(Path(raw).resolve()))
        except OSError:
            resolved.add(str(raw))
    return resolved


def assign_covers(
    article_ids: Sequence[str],
    platforms: Sequence[str],
    *,
    cover_dir: Path,
    recent_cover_paths: Sequence[str] = (),
    repeat_window: int = 0,
    seed: Optional[int] = None,
) -> Tuple[CoverAssignment, ...]:
    pool_files = list(list_cover_files(cover_dir))
    rng = random.Random(seed)
    tail = list(recent_cover_paths)[-repeat_window:] if repeat_window > 0 else []
    blocked_resolved = _resolved_path_strings(tail)

    platforms_cover = sorted(p for p in platforms if p in COVER_PLATFORMS)
    articles_sorted = sorted(article_ids)
    used_per_article = {a: set() for a in articles_sorted}
    out = []

    for article_id in articles_sorted:
        for platform in platforms_cover:
            candidates = [p for p in pool_files if str(p.resolve()) not in blocked_resolved]
            if not candidates:
                candidates = list(pool_files)
            prefer = [p for p in candidates if p not in used_per_article[article_id]]
            pick_pool = prefer if prefer else candidates
            choice = pick_pool[rng.randrange(len(pick_pool))]
            used_per_article[article_id].add(choice)
            out.append(
                CoverAssignment(
                    article_id=article_id,
                    platform=platform,
                    cover_path=choice,
                    cover_source="pool",
                    is_random=True,
                    is_manual_override=False,
                )
            )
    return tuple(out)
