import csv
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Mapping


PUBLISH_RECORD_FIELDNAMES = [
    "timestamp",
    "article",
    "article_id",
    "platform",
    "mode",
    "theme_name",
    "template_mode",
    "cover_path",
    "status",
    "error_type",
    "current_url",
    "page_state",
    "smoke_step",
    "returncode",
    "stdout",
    "stderr",
]
MAX_RECORD_LOG_LENGTH = 4096


def maybe_migrate_publish_records_csv(path: Path):
    return _load_publish_record_rows(path)


def _backup_publish_records(path: Path) -> Path:
    backup = path.with_name(f"{path.name}.bak")
    if backup.exists():
        backup.unlink()
    shutil.copyfile(path, backup)
    return backup


def _write_publish_records_rows(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=PUBLISH_RECORD_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            for existing in rows:
                writer.writerow({k: (existing.get(k) or "") for k in PUBLISH_RECORD_FIELDNAMES})
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _load_publish_record_rows(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            old_fn = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        _backup_publish_records(path)
        path.unlink(missing_ok=True)
        return []
    if set(old_fn) == set(PUBLISH_RECORD_FIELDNAMES):
        return [{k: (row.get(k) or "") for k in PUBLISH_RECORD_FIELDNAMES} for row in rows]
    _backup_publish_records(path)
    normalized_rows = [{k: (row.get(k) or "") for k in PUBLISH_RECORD_FIELDNAMES} for row in rows]
    _write_publish_records_rows(path, normalized_rows)
    return normalized_rows


def append_publish_record_at_path(path: Path, result: Mapping[str, object]):
    rows = maybe_migrate_publish_records_csv(path)
    error_type = result.get("error_type")
    error_type_cell = error_type if error_type is not None and error_type != "" else ""

    def _cell(val):
        if val is None:
            return ""
        return str(val)

    def _sanitize_record_log(value):
        text = _cell(value).replace("\n", "\\n")
        if len(text) <= MAX_RECORD_LOG_LENGTH:
            return text
        return text[: MAX_RECORD_LOG_LENGTH - len("...[truncated]")] + "...[truncated]"

    rows.append(
        {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "article": result.get("article", ""),
            "article_id": _cell(result.get("article_id")),
            "platform": _cell(result.get("platform")),
            "mode": result.get("mode", ""),
            "theme_name": _cell(result.get("theme_name")),
            "template_mode": _cell(result.get("template_mode")),
            "cover_path": _cell(result.get("cover_path")),
            "status": result.get("status", ""),
            "error_type": error_type_cell,
            "current_url": _cell(result.get("current_url")),
            "page_state": _cell(result.get("page_state")),
            "smoke_step": _cell(result.get("smoke_step")),
            "returncode": _cell(result.get("returncode", "")),
            "stdout": _sanitize_record_log(result.get("stdout", "")),
            "stderr": _sanitize_record_log(result.get("stderr", "")),
        }
    )
    _write_publish_records_rows(path, rows)
