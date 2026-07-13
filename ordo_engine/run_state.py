"""发布幂等与运行状态持久化

解决两个无人值守可靠性问题：
1. 重跑时在同一平台内堆积重复草稿（幂等/去重）
2. 失败可恢复：以状态文件记录每篇文章在各平台的最终状态，
   重跑时跳过已完成项（这是 PublishState 状态机的持久化落地）

状态文件：<base>/.ordo/publish-state.json
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

STATE_FILE = Path(__file__).resolve().parents[2] / ".ordo" / "publish-state.json"

# 视为「已完成」的状态（重跑时跳过，避免重复草稿）
_DONE_STATES = {"published", "draft_saved", "draft_only"}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def article_key(markdown_path) -> str:
    """用文章正文内容哈希作为幂等键（改名不影响去重）"""
    p = Path(markdown_path)
    content = p.read_text(encoding="utf-8") if p.exists() else str(p)
    return _hash(content)


def _load() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_done(article_key: str, platform: str, mode: str) -> bool:
    data = _load()
    rec = data.get(article_key, {}).get(platform)
    if not rec:
        return False
    return rec.get("status") in _DONE_STATES


def mark_done(article_key: str, platform: str, status: str, mode: str, url: str = ""):
    data = _load()
    data.setdefault(article_key, {})[platform] = {
        "status": status,
        "mode": mode,
        "url": url,
        "ts": int(time.time()),
    }
    _save(data)


def record_step(article_key: str, platform: str, step: str):
    """记录当前所处状态机步骤（断点续跑的持久化依据）"""
    data = _load()
    rec = data.setdefault(article_key, {}).setdefault(platform, {})
    rec["last_step"] = step
    rec["ts"] = int(time.time())
    _save(data)


def reset(article_key: str = None, platform: str = None):
    """清空状态（调试/重新发布用）"""
    if article_key is None:
        STATE_FILE.unlink(missing_ok=True)
        return
    data = _load()
    if platform:
        data.get(article_key, {}).pop(platform, None)
    else:
        data.pop(article_key, None)
    _save(data)
