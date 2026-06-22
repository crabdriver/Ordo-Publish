from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


def _path_str(p: Optional[Path]) -> Optional[str]:
    return str(p) if p is not None else None


@dataclass(frozen=True)
class ArticleDraft:
    article_id: str
    title: str
    body_markdown: str
    source_path: Optional[Path]
    source_kind: str
    image_paths: Tuple[Path, ...] = ()
    word_count: int = 0
    template_mode: str = "default"
    theme_name: Optional[str] = None
    is_config_complete: bool = False

    def to_dict(self):
        return {
            "article_id": self.article_id,
            "title": self.title,
            "body_markdown": self.body_markdown,
            "source_path": _path_str(self.source_path),
            "source_kind": self.source_kind,
            "image_paths": [str(x) for x in self.image_paths],
            "word_count": self.word_count,
            "template_mode": self.template_mode,
            "theme_name": self.theme_name,
            "is_config_complete": self.is_config_complete,
        }


@dataclass(frozen=True)
class ImportFailure:
    source_path: Optional[Path]
    source_kind: str
    error_type: str
    message: str

    def to_dict(self):
        return {
            "source_path": _path_str(self.source_path),
            "source_kind": self.source_kind,
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass(frozen=True)
class ImportJob:
    job_id: str
    import_mode: str
    source_path: Optional[Path] = None
    pasted_preview: Optional[str] = None
    imported_at: str = ""
    source_count: int = 0
    manifest_path: Optional[Path] = None
    drafts: Tuple[ArticleDraft, ...] = ()
    failures: Tuple[ImportFailure, ...] = ()

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "import_mode": self.import_mode,
            "source_path": _path_str(self.source_path),
            "pasted_preview": self.pasted_preview,
            "imported_at": self.imported_at,
            "source_count": self.source_count or len(self.drafts),
            "manifest_path": _path_str(self.manifest_path),
            "article_count": len(self.drafts),
            "failure_count": len(self.failures),
            "drafts": [d.to_dict() for d in self.drafts],
            "failures": [item.to_dict() for item in self.failures],
        }


@dataclass(frozen=True)
class TemplateAssignment:
    article_id: str
    template_mode: str
    theme_id: Optional[str] = None
    theme_name: Optional[str] = None
    is_random: bool = False
    is_manual_override: bool = False
    is_confirmed: bool = False

    def to_dict(self):
        return {
            "article_id": self.article_id,
            "template_mode": self.template_mode,
            "theme_id": self.theme_id,
            "theme_name": self.theme_name,
            "is_random": self.is_random,
            "is_manual_override": self.is_manual_override,
            "is_confirmed": self.is_confirmed,
        }


@dataclass(frozen=True)
class CoverAssignment:
    article_id: str
    platform: str
    cover_path: Optional[Path] = None
    cover_source: str = ""
    is_random: bool = False
    is_manual_override: bool = False

    def to_dict(self):
        return {
            "article_id": self.article_id,
            "platform": self.platform,
            "cover_path": _path_str(self.cover_path),
            "cover_source": self.cover_source,
            "is_random": self.is_random,
            "is_manual_override": self.is_manual_override,
        }


@dataclass(frozen=True)
class PublishJob:
    job_id: str
    article_ids: Tuple[str, ...]
    platforms: Tuple[str, ...]
    status: str = "pending"
    current_step: str = ""
    success_count: int = 0
    failure_count: int = 0
    skip_count: int = 0
    recoverable: bool = True
    error_summary: str = ""
    scheduled_publish_at: Optional[str] = None

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "article_ids": list(self.article_ids),
            "platforms": list(self.platforms),
            "status": self.status,
            "current_step": self.current_step,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "skip_count": self.skip_count,
            "recoverable": self.recoverable,
            "error_summary": self.error_summary,
            "scheduled_publish_at": self.scheduled_publish_at,
        }
