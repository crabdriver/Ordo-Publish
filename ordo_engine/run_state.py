"""发布幂等与运行状态持久化。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path


def state_file_for(base_dir) -> Path:
    return Path(base_dir) / ".ordo" / "publish-state.json"


STATE_FILE = state_file_for(Path(__file__).resolve().parents[1])

DONE_BY_MODE = {
    "draft": {"draft_saved", "draft_only", "skipped_existing"},
    "publish": {"published", "scheduled", "skipped_existing"},
}


class StateCorruptionError(RuntimeError):
    """状态文件存在，但不是有效 JSON 对象。"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _frontmatter_article_id(content: str) -> str | None:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() in {"---", "..."}:
            return None
        match = re.match(r"^\s*article_id\s*:\s*(.*?)\s*$", line)
        if match:
            value = match.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            return value or None
    return None


def article_key(markdown_path) -> str:
    """优先用发布包 article_id；旧 Markdown 回退正文哈希。"""
    path = Path(markdown_path)
    content = path.read_text(encoding="utf-8") if path.exists() else str(path)
    return _frontmatter_article_id(content) or _hash(content)


def load_state(state_file=STATE_FILE) -> dict:
    path = Path(state_file)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateCorruptionError(f"状态文件 JSON 损坏: {path}") from exc
    if not isinstance(data, dict):
        raise StateCorruptionError(f"状态文件根节点必须是对象: {path}")
    return data


def save_state(state_file, data: dict) -> None:
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
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def get_record(identity: str, platform: str, mode: str, *, state_file=STATE_FILE) -> dict | None:
    record = load_state(state_file).get(identity, {}).get(platform, {}).get(mode)
    return record if isinstance(record, dict) else None


def is_done(identity: str, platform: str, mode: str, *, state_file=STATE_FILE) -> bool:
    record = get_record(identity, platform, mode, state_file=state_file)
    return bool(record and record.get("status") in DONE_BY_MODE.get(mode, set()))


def mark_done(
    identity: str,
    platform: str,
    status: str,
    mode: str,
    url: str = "",
    *,
    state_file=STATE_FILE,
) -> None:
    data = load_state(state_file)
    data.setdefault(identity, {}).setdefault(platform, {})[mode] = {
        "status": status,
        "mode": mode,
        "url": url,
        "ts": int(time.time()),
    }
    save_state(state_file, data)


def record_step(
    identity: str,
    platform: str,
    mode: str,
    step: str,
    *,
    state_file=STATE_FILE,
) -> None:
    """记录当前状态机步骤。"""
    data = load_state(state_file)
    record = data.setdefault(identity, {}).setdefault(platform, {}).setdefault(mode, {})
    record.update({"last_step": step, "mode": mode, "ts": int(time.time())})
    save_state(state_file, data)


def reset(
    identity: str | None = None,
    platform: str | None = None,
    mode: str | None = None,
    *,
    state_file=STATE_FILE,
) -> None:
    """按全部、文章、平台或模式清空状态。"""
    path = Path(state_file)
    if identity is None:
        path.unlink(missing_ok=True)
        return
    data = load_state(path)
    if platform is None:
        data.pop(identity, None)
    elif mode is None:
        data.get(identity, {}).pop(platform, None)
    else:
        data.get(identity, {}).get(platform, {}).pop(mode, None)
    save_state(path, data)
