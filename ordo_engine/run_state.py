"""发布状态持久化 — v2 schema。

.ordo/auto_publish_state.json 是唯一恢复状态文件。
publish_records.csv 仅作审计日志。

v1 → v2 迁移仅在首次加载时执行一次。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time as _time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


# ── 枚举 ───────────────────────────────────────────────────────


class PlatformStage(str, Enum):
    pending = "pending"
    preflight_ok = "preflight_ok"
    draft_prepared = "draft_prepared"
    draft_saved = "draft_saved"
    publish_attempted = "publish_attempted"
    published = "published"
    limited_after_draft = "limited_after_draft"
    blocked_no_draft = "blocked_no_draft"
    manual_verify = "manual_verify"
    failed_before_draft = "failed_before_draft"
    not_executed = "not_executed"


class ArticleStage(str, Enum):
    pending = "pending"
    needs_review = "needs_review"
    completed = "completed"


# ── 旧 API 兼容：终态集合 ──────────────────────────────────────

# 旧 mark_done 可接受的终态（用于 is_done 判定）
TERMINAL_STATUSES_BY_MODE = {
    "draft": {"draft_saved", "draft_only", "skipped_existing"},
    "publish": {"published", "scheduled", "skipped_existing"},
}

# v2 中与旧终态对应的 stage
_V2_TERMINAL_MAP = {
    ("draft_saved", "draft"): PlatformStage.draft_saved,
    ("draft_only", "draft"): PlatformStage.draft_saved,
    ("skipped_existing", "draft"): PlatformStage.draft_saved,
    ("published", "publish"): PlatformStage.published,
    ("scheduled", "publish"): PlatformStage.published,
    ("skipped_existing", "publish"): PlatformStage.published,
}


def _find_dir() -> Path:
    return Path(__file__).resolve().parents[1]


# ── 状态文件路径 ───────────────────────────────────────────────


def state_file_for(base_dir) -> Path:
    """返回唯一恢复状态文件路径（v2）。"""
    return Path(base_dir) / ".ordo" / "auto_publish_state.json"


def _state_file_default() -> Path:
    return state_file_for(_find_dir())


STATE_FILE = _state_file_default()

# 旧状态文件路径（仅迁移时读取，之后停止读写）
_OLD_STATE_FILE_NAME = "publish-state.json"


# ── 错误类型 ───────────────────────────────────────────────────


class StateCorruptionError(RuntimeError):
    """状态文件存在，但不是有效 JSON 对象。"""


class StateMigrationError(RuntimeError):
    """v1 → v2 迁移失败。"""


# ── 内部工具 ───────────────────────────────────────────────────


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frontmatter_article_id(content: str) -> str | None:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped in {"---", "..."}:
            return None
        m = re.match(r"^\s*article_id\s*:\s*(.*?)\s*$", line)
        if m:
            value = m.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value or None
    return None


def _is_v2_state(data: dict) -> bool:
    return isinstance(data, dict) and data.get("schema_version") == 2


# ── v2 Dataclasses ─────────────────────────────────────────────


@dataclass
class PlatformRecord:
    """单平台单 mode 的状态记录。"""
    stage: PlatformStage = PlatformStage.pending
    draft_ref: Optional[str] = None
    draft_url: Optional[str] = None
    published_ref: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    retry_after: Optional[str] = None
    updated_at: Optional[str] = None
    verified_at: Optional[str] = None

    # 旧 API 兼容：存储 mark_done/record_step 的遗留字段
    _legacy_status: Optional[str] = None
    _legacy_url: Optional[str] = None
    _legacy_last_step: Optional[str] = None
    _legacy_ts: Optional[int] = None
    _legacy_mode: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"stage": self.stage.value}
        for key in ("draft_ref", "draft_url", "published_ref",
                     "error", "error_type", "retry_after",
                     "updated_at", "verified_at"):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        # 旧 API 兼容字段必须序列化（mark_done → save → get_record 的完整回路）
        for key in ("_legacy_status", "_legacy_url", "_legacy_last_step",
                     "_legacy_ts", "_legacy_mode"):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PlatformRecord":
        stage = PlatformStage(d.get("stage", PlatformStage.pending.value))
        return cls(
            stage=stage,
            draft_ref=d.get("draft_ref"),
            draft_url=d.get("draft_url"),
            published_ref=d.get("published_ref"),
            error=d.get("error"),
            error_type=d.get("error_type"),
            retry_after=d.get("retry_after"),
            updated_at=d.get("updated_at"),
            verified_at=d.get("verified_at"),
            _legacy_status=d.get("_legacy_status"),
            _legacy_url=d.get("_legacy_url"),
            _legacy_last_step=d.get("_legacy_last_step"),
            _legacy_ts=d.get("_legacy_ts"),
            _legacy_mode=d.get("_legacy_mode"),
        )


@dataclass
class ArticleRecord:
    """单篇文章的状态记录。"""
    article_id: str
    package_hash: Optional[str] = None
    source_path: Optional[str] = None
    article_stage: ArticleStage = ArticleStage.pending
    article_block_reason: Optional[str] = None
    title: Optional[str] = None
    cover_path: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    platforms: dict[str, dict[str, PlatformRecord]] = field(default_factory=dict)
    # platforms[platform][mode] -> PlatformRecord

    def to_dict(self) -> dict:
        platforms = {}
        for platform_name, modes in self.platforms.items():
            platforms[platform_name] = {}
            for mode, rec in modes.items():
                platforms[platform_name][mode] = rec.to_dict()
        d = {"article_id": self.article_id}
        for key in ("package_hash", "source_path", "article_block_reason",
                     "title", "cover_path", "started_at", "completed_at"):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        d["article_stage"] = self.article_stage.value
        if platforms:
            d["platforms"] = platforms
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ArticleRecord":
        platforms_raw = d.get("platforms", {}) or {}
        platforms: dict[str, dict[str, PlatformRecord]] = {}
        for platform_name, modes in platforms_raw.items():
            platforms[platform_name] = {}
            for mode, rec_d in (modes or {}).items():
                platforms[platform_name][mode] = PlatformRecord.from_dict(rec_d)
        return cls(
            article_id=d["article_id"],
            package_hash=d.get("package_hash"),
            source_path=d.get("source_path"),
            article_stage=ArticleStage(d.get("article_stage", ArticleStage.pending.value)),
            article_block_reason=d.get("article_block_reason"),
            title=d.get("title"),
            cover_path=d.get("cover_path"),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            platforms=platforms,
        )


# ── 读写 ───────────────────────────────────────────────────────


def load_state(state_file=None) -> dict:
    """加载原始状态 JSON（用于读写和迁移）。"""
    path = Path(state_file or STATE_FILE)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateCorruptionError(f"状态文件 JSON 损坏: {path}") from exc
    if not isinstance(data, dict):
        raise StateCorruptionError(f"状态文件根节点必须是对象: {path}")
    return data


def load_v2_state(state_file=None) -> dict[str, ArticleRecord]:
    """加载 v2 状态为 ArticleRecord 字典。"""
    data = load_state(state_file)
    if not data:
        return {}
    if not _is_v2_state(data):
        raise StateCorruptionError(
            f"状态文件不是 v2 schema: {state_file or STATE_FILE}"
        )
    articles: dict[str, ArticleRecord] = {}
    for article_id, article_d in (data.get("articles", {}) or {}).items():
        articles[article_id] = ArticleRecord.from_dict(article_d)
    return articles


def save_state(state_file, data: dict) -> None:
    """原子写入状态文件（tempfile + flush + fsync + os.replace）。"""
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp_path = Path(temp.name)
            json.dump(data, temp, ensure_ascii=False, indent=2)
            temp.flush()
            os.fsync(temp.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def save_v2_state(articles: dict[str, ArticleRecord], state_file=None) -> None:
    """将 ArticleRecord 字典原子写入 v2 状态文件。"""
    state = {
        "schema_version": 2,
        "articles": {},
    }
    for article_id, record in articles.items():
        state["articles"][article_id] = record.to_dict()
    save_state(state_file or STATE_FILE, state)


def _ensure_v2_article(state_file, identity: str) -> tuple[dict, Path]:
    """加载 v2 原始 dict，必要时初始化空文章。返回 (articles_dict, path)。"""
    path = Path(state_file)
    data = load_state(path)
    if not data:
        data = {"schema_version": 2, "articles": {}}
    if not _is_v2_state(data):
        data = {"schema_version": 2, "articles": {}}
    data.setdefault("articles", {})
    data["articles"].setdefault(identity, ArticleRecord(article_id=identity).to_dict())
    return data, path


# ── 旧 API 兼容层 ──────────────────────────────────────────────


def _legacy_stage_to_v2_stage(status: str, mode: str) -> PlatformStage:
    """将旧 status 值映射到 v2 PlatformStage。"""
    key = (status, mode)
    if key in _V2_TERMINAL_MAP:
        return _V2_TERMINAL_MAP[key]
    # 非终态映射
    if status in {"submitted_unverified"}:
        return PlatformStage.manual_verify
    if status in {"limit_reached", "rate_limited"}:
        return PlatformStage.limited_after_draft
    if status in {"unknown", "failed"}:
        return PlatformStage.failed_before_draft
    return PlatformStage.manual_verify


def _legacy_url_for_mode(mode: str, record: PlatformRecord) -> str:
    """从 v2 record 中提取旧 API 期望的 url 字段。"""
    if mode == "draft":
        return record.draft_ref or record._legacy_url or ""
    return record.published_ref or record._legacy_url or ""


def get_record(identity: str, platform: str, mode: str, *, state_file=None) -> dict | None:
    """旧兼容 API：返回 {status, mode, url, ts, last_step} 格式。

    同时支持 mark_done（完整状态）和 record_step（部分步骤）的记录。
    """
    data, _ = _ensure_v2_article(state_file or STATE_FILE, identity)
    article_d = data["articles"][identity]
    platforms = article_d.get("platforms", {}) or {}
    modes = platforms.get(platform, {}) or {}
    rec_d = modes.get(mode)
    if not isinstance(rec_d, dict):
        return None
    rec = PlatformRecord.from_dict(rec_d)

    # 只有 record_step 的部分记录（无 mark_done 状态）
    if rec._legacy_last_step and not rec._legacy_status:
        return {
            "last_step": rec._legacy_last_step,
            "mode": rec._legacy_mode or mode,
            "ts": rec._legacy_ts or 0,
        }

    # 完整 mark_done 记录
    status = rec._legacy_status
    if status is None and rec.stage != PlatformStage.pending:
        # v2 原生记录，没有 legacy_status 但有有效 stage
        status = rec.stage.value

    if status is None:
        return None

    result = {
        "status": status,
        "mode": rec._legacy_mode or mode,
        "url": _legacy_url_for_mode(mode, rec),
        "ts": rec._legacy_ts or 0,
    }
    if rec._legacy_last_step:
        result["last_step"] = rec._legacy_last_step
    return result


def is_done(identity: str, platform: str, mode: str, *, state_file=None) -> bool:
    """旧兼容 API：检查是否已达到终态。"""
    record = get_record(identity, platform, mode, state_file=state_file)
    if not record:
        return False
    return record.get("status") in TERMINAL_STATUSES_BY_MODE.get(mode, set())


def mark_done(
    identity: str,
    platform: str,
    status: str,
    mode: str,
    url: str = "",
    *,
    state_file=None,
) -> None:
    """旧兼容 API：标记平台+mode 为终态。

    内部映射到 v2 PlatformStage 并写回 v2 schema。
    """
    data, path = _ensure_v2_article(state_file or STATE_FILE, identity)
    article_d = data["articles"][identity]
    article_d.setdefault("platforms", {})
    article_d["platforms"].setdefault(platform, {})
    existing = article_d["platforms"][platform].get(mode, {})

    v2_stage = _legacy_stage_to_v2_stage(status, mode)
    now = _now_iso()

    new_rec = {
        "stage": v2_stage.value,
        "updated_at": now,
    }
    # 保留已有的 v2 字段
    for key in ("draft_ref", "draft_url", "published_ref",
                 "error", "error_type", "retry_after", "verified_at"):
        if key in existing and existing[key] is not None:
            new_rec[key] = existing[key]
    # 设置 url 到对应字段
    if url:
        if mode == "draft":
            new_rec["draft_ref"] = url
        else:
            new_rec["published_ref"] = url
    # 旧兼容字段
    new_rec["_legacy_status"] = status
    new_rec["_legacy_url"] = url
    new_rec["_legacy_mode"] = mode
    new_rec["_legacy_ts"] = existing.get("_legacy_ts") or int(_time.time())

    article_d["platforms"][platform][mode] = new_rec

    # 更新文章级时间戳
    article_d.setdefault("started_at", now)
    article_d["article_stage"] = ArticleStage.completed.value
    article_d["completed_at"] = now

    save_state(path, data)


def record_step(
    identity: str,
    platform: str,
    mode: str,
    step: str,
    *,
    state_file=None,
) -> None:
    """旧兼容 API：记录状态机步骤。

    内部更新 v2 PlatformRecord 的 _legacy_last_step，不影响 stage。
    """
    data, path = _ensure_v2_article(state_file or STATE_FILE, identity)
    article_d = data["articles"][identity]
    article_d.setdefault("platforms", {})
    article_d["platforms"].setdefault(platform, {})
    existing = article_d["platforms"][platform].get(mode, {})

    ts = int(_time.time())
    existing["_legacy_last_step"] = step
    existing["_legacy_mode"] = mode
    existing["_legacy_ts"] = ts
    # 保留已有 stage
    if "stage" not in existing or not existing["stage"]:
        existing["stage"] = PlatformStage.pending.value
    existing.setdefault("updated_at", _now_iso())

    article_d["platforms"][platform][mode] = existing
    save_state(path, data)


def reset(
    identity: str | None = None,
    platform: str | None = None,
    mode: str | None = None,
    *,
    state_file=None,
) -> None:
    """按全部、文章、平台或模式清空状态。"""
    path = Path(state_file or STATE_FILE)
    if identity is None:
        path.unlink(missing_ok=True)
        return
    data, _ = _ensure_v2_article(path, identity)
    articles = data.get("articles", {})
    if platform is None:
        articles.pop(identity, None)
    elif mode is None:
        article_d = articles.get(identity, {})
        article_d.get("platforms", {}).pop(platform, None)
    else:
        article_d = articles.get(identity, {})
        platforms = article_d.get("platforms", {})
        platform_d = platforms.get(platform, {})
        platform_d.pop(mode, None)
    save_state(path, data)


# ── 新 API ─────────────────────────────────────────────────────


# 保存 article_key 旧名以兼容现有 import
article_key = None  # 将会被 stable_article_id 替换，见下方 _init_article_key


def stable_article_id(
    markdown_path,
    *,
    watch_dir: str | Path | None = None,
) -> str:
    """生成稳定的文章 identity。

    - 有 frontmatter article_id → 直接使用。
    - 在 watch_dir 内 → "path:<relative-to-watch-dir>"
    - 在 watch_dir 外 → "path:<normalized-absolute-path>"

    路径规范：expanduser + resolve + Unicode NFC。
    """
    path = Path(markdown_path).expanduser().resolve()
    try:
        content = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        content = ""
    frontmatter_id = _frontmatter_article_id(content)
    if frontmatter_id:
        return frontmatter_id

    # 规范化路径
    try:
        normalized = str(path)
        import unicodedata
        normalized = unicodedata.normalize("NFC", normalized)
        if watch_dir:
            wd = Path(watch_dir).expanduser().resolve()
            wd_str = unicodedata.normalize("NFC", str(wd))
            if normalized.startswith(wd_str + os.sep):
                rel = normalized[len(wd_str + os.sep):]
                return f"path:{rel}"
    except (ValueError, OSError):
        pass

    return f"path:{normalized}"


def legacy_article_key(markdown_path) -> str:
    """旧 article_key 别名：优先 frontmatter article_id，回退正文哈希。"""
    path = Path(markdown_path)
    content = path.read_text(encoding="utf-8") if path.exists() else str(path)
    return _frontmatter_article_id(content) or _hash(content)


def compute_package_hash(
    article_path: str | Path,
    cover_path: str | Path | None = None,
) -> str:
    """计算文章内容 + 封面的联合哈希。

    正文 SHA256 + 封面文件 SHA256 → 取前 32 位 hex。
    仅用于版本检测，不充当 identity。
    """
    hasher = hashlib.sha256()
    article = Path(article_path)
    if article.exists():
        hasher.update(article.read_bytes())
    if cover_path:
        cover = Path(cover_path)
        if cover.exists():
            hasher.update(cover.read_bytes())
    return hasher.hexdigest()[:32]


def get_platform_record(
    articles: dict[str, ArticleRecord],
    identity: str,
    platform: str,
    mode: str,
) -> PlatformRecord | None:
    """从 ArticleRecord 字典中获取平台记录。"""
    article = articles.get(identity)
    if not article:
        return None
    platform_modes = article.platforms.get(platform)
    if not platform_modes:
        return None
    return platform_modes.get(mode)


def set_platform_record(
    articles: dict[str, ArticleRecord],
    identity: str,
    platform: str,
    mode: str,
    record: PlatformRecord,
) -> None:
    """设置平台记录，自动更新 updated_at。"""
    article = articles.setdefault(identity, ArticleRecord(article_id=identity))
    article.platforms.setdefault(platform, {})
    record.updated_at = _now_iso()
    article.platforms[platform][mode] = record


def is_article_completed(
    article: ArticleRecord,
    enabled_browser_platforms: list[str],
    *,
    wechat_required: bool = True,
) -> bool:
    """判断文章是否已完成。

    条件：
    - 微信：stage 必须为 draft_saved（如果 wechat_required=True）
    - 所有启用浏览器平台：stage 必须为 published
    - 以下 stage 不算完成：limited_after_draft, manual_verify, blocked_no_draft,
      failed_before_draft, not_executed
    """
    if wechat_required:
        wechat_rec = get_platform_record_by_article(article, "wechat", "draft")
        if not wechat_rec or wechat_rec.stage != PlatformStage.draft_saved:
            return False

    for platform in enabled_browser_platforms:
        rec = get_platform_record_by_article(article, platform, "publish")
        if not rec:
            return False
        if rec.stage != PlatformStage.published:
            return False

    return True


def get_platform_record_by_article(
    article: ArticleRecord,
    platform: str,
    mode: str,
) -> PlatformRecord | None:
    """直接从 ArticleRecord 中获取平台记录。"""
    modes = article.platforms.get(platform)
    if not modes:
        return None
    return modes.get(mode)


# ── v1 → v2 迁移 ──────────────────────────────────────────────


def migrate_v1_to_v2(
    state_file=None,
    *,
    watch_dir: str | Path | None = None,
    publish_records_csv: str | Path | None = None,
) -> dict[str, ArticleRecord]:
    """将 v1 状态文件迁移到 v2 schema。

    读取顺序：
    1. 旧 auto_publish_state.json（v1 格式）
    2. 旧 publish-state.json（如果存在）
    3. publish_records.csv（仅此一次，历史导入）

    规则：
    - 有 article_id 用 article_id
    - 没有 → 基于路径生成 stable identity
    - CSV 中的记录合并进迁移结果
    - 损坏/矛盾的记录 → manual_verify
    - 迁移前自动备份原文件
    - 迁移失败 → 保留原文件
    """
    path = Path(state_file or STATE_FILE)
    if not path.exists():
        return {}

    # 读取原始数据，检查是否已经是 v2
    raw = load_state(path)
    if _is_v2_state(raw):
        return load_v2_state(path)

    # 备份
    backup_path = path.with_suffix(f".json.bak.{datetime.now().strftime('%Y%m%dT%H%M%S')}")
    backup_path.write_text(path.read_text(encoding="utf-8"))

    articles: dict[str, ArticleRecord] = {}
    now = _now_iso()

    # 1. 从旧 auto_publish_state.json 迁移
    old_articles = raw.get("articles", {}) if isinstance(raw, dict) else {}
    for old_key, old_article in old_articles.items():
        if not isinstance(old_article, dict):
            continue
        identity = _resolve_migration_identity(old_key, old_article, watch_dir)
        record = articles.setdefault(identity, ArticleRecord(
            article_id=identity,
            source_path=old_article.get("path"),
            title=old_article.get("title"),
            cover_path=old_article.get("cover"),
        ))
        _migrate_old_platforms(record, old_article, now)

    # 2. 从旧 publish-state.json 合并
    _migrate_old_publish_state(path.parent / _OLD_STATE_FILE_NAME, articles, watch_dir, now)

    # 3. 从 publish_records.csv 导入（仅这一次）
    if publish_records_csv:
        csv_path = Path(publish_records_csv)
        if csv_path.exists():
            _migrate_from_csv(csv_path, articles, watch_dir, now)

    return articles


def _resolve_migration_identity(
    old_key: str,
    old_article: dict,
    watch_dir: str | Path | None,
) -> str:
    """从旧记录推导稳定 identity。"""
    # 优先用 source_path
    source = old_article.get("path")
    if source:
        try:
            return stable_article_id(source, watch_dir=watch_dir)
        except Exception:
            pass
    # 回退：如果旧 key 看起来已经是稳定格式
    if old_key and not old_key.startswith("/"):
        return old_key
    # 最后的回退：基于源路径
    return f"path:{old_key}"


def _migrate_old_platforms(
    record: ArticleRecord,
    old_article: dict,
    now: str,
) -> None:
    """从旧 auto_publish_state.json 的 platforms 字段迁移。"""
    old_platforms = old_article.get("platforms", {}) or {}
    for platform_name, platform_data in old_platforms.items():
        if not isinstance(platform_data, dict):
            continue
        mode = platform_data.get("mode", "publish")
        old_status = platform_data.get("status")
        if not old_status:
            continue
        stage = _legacy_stage_to_v2_stage(old_status, mode)
        prec = PlatformRecord(
            stage=stage,
            updated_at=platform_data.get("updated_at") or now,
            error_type=platform_data.get("error_type"),
            _legacy_status=old_status,
            _legacy_mode=mode,
            _legacy_ts=platform_data.get("returncode", 0),
        )
        record.platforms.setdefault(platform_name, {})[mode] = prec


def _migrate_old_publish_state(
    old_state_path: Path,
    articles: dict[str, ArticleRecord],
    watch_dir: str | Path | None,
    now: str,
) -> None:
    """从旧 publish-state.json 合并数据。"""
    if not old_state_path.exists():
        return
    try:
        old_data = json.loads(old_state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(old_data, dict):
        return

    for old_identity, platforms_data in old_data.items():
        if not isinstance(platforms_data, dict):
            continue
        identity = _resolve_migration_identity(old_identity, {}, watch_dir)
        record = articles.setdefault(identity, ArticleRecord(article_id=identity))
        for platform_name, modes in platforms_data.items():
            if not isinstance(modes, dict):
                continue
            for mode, mode_data in modes.items():
                if not isinstance(mode_data, dict):
                    continue
                old_status = mode_data.get("status")
                if not old_status:
                    continue
                stage = _legacy_stage_to_v2_stage(old_status, mode)
                prec = PlatformRecord(
                    stage=stage,
                    updated_at=now,
                    _legacy_status=old_status,
                    _legacy_mode=mode,
                    _legacy_url=mode_data.get("url", ""),
                    _legacy_ts=mode_data.get("ts"),
                )
                if old_status in {"published", "draft_saved"} and mode_data.get("url"):
                    if mode == "draft":
                        prec.draft_ref = mode_data["url"]
                    else:
                        prec.published_ref = mode_data["url"]
                record.platforms.setdefault(platform_name, {})[mode] = prec


def _migrate_from_csv(
    csv_path: Path,
    articles: dict[str, ArticleRecord],
    watch_dir: str | Path | None,
    now: str,
) -> None:
    """从 publish_records.csv 导入历史数据（仅首次迁移时调用）。"""
    from ordo_engine.results.publish_records import load_publish_records_at_path
    try:
        rows = load_publish_records_at_path(csv_path)
    except Exception:
        return

    for row in rows:
        platform_name = (row.get("platform") or "").strip()
        mode = (row.get("mode") or "").strip()
        old_status = (row.get("status") or "").strip()
        if not platform_name or not mode or not old_status:
            continue

        # 解析 identity
        source_path = row.get("article")
        identity = None
        if source_path:
            try:
                identity = stable_article_id(source_path, watch_dir=watch_dir)
            except Exception:
                pass
        if not identity:
            identity = (row.get("article_key") or "").strip()
        if not identity:
            continue

        record = articles.setdefault(identity, ArticleRecord(
            article_id=identity,
            source_path=str(source_path) if source_path else None,
        ))

        # 如果该平台已经有更可靠的状态（来自 auto_publish_state.json），不覆盖
        existing = record.platforms.get(platform_name, {}).get(mode)
        if existing and existing.stage not in {PlatformStage.pending, PlatformStage.not_executed}:
            continue

        stage = _legacy_stage_to_v2_stage(old_status, mode)
        # 损坏/矛盾记录 → manual_verify
        if old_status in {"unknown", "missing_publish_record", "timeout"}:
            stage = PlatformStage.manual_verify

        prec = PlatformRecord(
            stage=stage,
            updated_at=row.get("timestamp") or now,
            error_type=row.get("error_type"),
            _legacy_status=old_status,
            _legacy_mode=mode,
            _legacy_ts=row.get("returncode"),
        )
        record.platforms.setdefault(platform_name, {})[mode] = prec


# ── 兼容旧 article_key 模块级引用 ──────────────────────────────

article_key = legacy_article_key
