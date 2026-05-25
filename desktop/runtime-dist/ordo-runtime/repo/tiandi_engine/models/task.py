from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class PlatformRequest:
    platform: str
    mode: str
    theme_name: Optional[str] = None
    cover_path: Optional[str] = None
    template_mode: Optional[str] = None
    article_id: Optional[str] = None


@dataclass(frozen=True)
class ArticleTask:
    index: int
    article_path: Path
    title: str
    platforms: Tuple[PlatformRequest, ...]


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    source_path: Path
    mode: str
    articles: Tuple[ArticleTask, ...]
    continue_from_index: Optional[int] = None


def _article_title(article_path: Path) -> str:
    return Path(article_path).stem


def build_task_spec(
    source_path: Path,
    article_paths: Iterable[Path],
    platforms: Iterable[str],
    mode: str,
    default_theme: Optional[str] = None,
    default_cover_path: Optional[str] = None,
    default_template_mode: Optional[str] = None,
    default_article_id: Optional[str] = None,
    task_id: str = "task",
    continue_from_index: Optional[int] = None,
) -> TaskSpec:
    platform_requests = tuple(
        PlatformRequest(
            platform=platform,
            mode=mode,
            theme_name=default_theme if platform == "wechat" else None,
            cover_path=default_cover_path,
            template_mode=default_template_mode,
            article_id=default_article_id,
        )
        for platform in platforms
    )
    articles = tuple(
        ArticleTask(
            index=index,
            article_path=Path(article_path),
            title=_article_title(Path(article_path)),
            platforms=platform_requests,
        )
        for index, article_path in enumerate(article_paths)
    )
    return TaskSpec(
        task_id=task_id,
        source_path=Path(source_path),
        mode=mode,
        articles=articles,
        continue_from_index=continue_from_index,
    )
