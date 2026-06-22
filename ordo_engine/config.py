import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from ordo_engine.assignment.covers import CoverPoolError, list_cover_files
from ordo_engine.assignment.templates import scan_theme_pool


PLACEHOLDER_MARKERS = ("CHANGE_ME", "your_", "你的", "example", "appid_here", "api_key_here")
DESKTOP_APP_IDENTIFIER = "com.ordo.workbench"


def load_simple_env_file(env_path: Path) -> Dict[str, str]:
    values = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_json_config(base_dir: Path) -> Tuple[Dict, Optional[str]]:
    path = Path(base_dir) / "config.json"
    if not path.exists():
        return {}, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return {}, f"config.json 解析失败：{exc}"


def _is_real_value(value: Optional[str]) -> bool:
    if not value:
        return False
    upper_value = str(value).upper()
    return not any(marker.upper() in upper_value for marker in PLACEHOLDER_MARKERS)


def _nested_get(data: Mapping, keys, default=None):
    current = data
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


@dataclass(frozen=True)
class EngineConfig:
    base_dir: Path
    project_config: Dict = field(default_factory=dict)
    project_config_warning: Optional[str] = None
    env_file_values: Dict[str, str] = field(default_factory=dict)
    env_file_exists: bool = False
    environ: Dict[str, str] = field(default_factory=dict)
    cli_overrides: Dict[str, str] = field(default_factory=dict)

    def _resolve(self, cli_key, env_keys, config_keys):
        cli_value = self.cli_overrides.get(cli_key)
        if cli_value:
            return cli_value
        for env_key in env_keys:
            env_value = self.environ.get(env_key)
            if env_value:
                return env_value
            file_value = self.env_file_values.get(env_key)
            if file_value:
                return file_value
        config_value = _nested_get(self.project_config, config_keys)
        return config_value

    def resolve_wechat_credentials(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        app_id = self._resolve("wechat_app_id", ("WECHAT_APPID",), ("wechat", "app_id"))
        secret = self._resolve("wechat_secret", ("WECHAT_SECRET",), ("wechat", "app_secret"))
        author = self._resolve("wechat_author", ("WECHAT_AUTHOR",), ("wechat", "author"))
        return app_id, secret, author

    def get_wechat_settings(self) -> Dict[str, str]:
        app_id, secret, author = self.resolve_wechat_credentials()
        return {
            "app_id": app_id or "",
            "secret": secret or "",
            "author": author or "",
        }

    def resolve_themes_dir(self) -> Path:
        return self.base_dir / "themes"

    def resolve_cover_dir(self) -> Path:
        env_raw = self.environ.get("ORDO_COVER_DIR") or self.environ.get("COVER_DIR")
        if env_raw:
            return Path(str(env_raw)).expanduser().resolve()
        raw = _nested_get(self.project_config, ("assignment", "cover_dir"))
        if raw:
            path = Path(str(raw))
            return path if path.is_absolute() else (self.base_dir / path)
        return self.base_dir / "covers"

    def get_cover_repeat_window(self) -> int:
        raw = _nested_get(self.project_config, ("assignment", "cover_repeat_window"), 8)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 8

    def get_default_template_mode(self) -> str:
        raw = _nested_get(self.project_config, ("assignment", "default_template_mode"), "default")
        return str(raw or "default")

    def get_browser_session_settings(self) -> Dict[str, object]:
        enabled = _nested_get(self.project_config, ("browser_session", "enabled"))
        remind_after_days = _nested_get(self.project_config, ("browser_session", "remind_after_days"), 5)
        debug_port = _nested_get(self.project_config, ("browser_session", "debug_port"), 9333)
        profile_dir = _nested_get(self.project_config, ("browser_session", "profile_dir"))
        if profile_dir:
            profile_path = Path(str(profile_dir))
            resolved_profile_dir = profile_path if profile_path.is_absolute() else (self.base_dir / profile_path)
        else:
            resolved_profile_dir = self.base_dir / ".ordo" / "browser-session" / "profile"
        try:
            remind_after_days = max(1, int(remind_after_days))
        except (TypeError, ValueError):
            remind_after_days = 5
        try:
            debug_port = max(1, int(debug_port))
        except (TypeError, ValueError):
            debug_port = 9333
        return {
            "enabled": True if enabled is None else bool(enabled),
            "remind_after_days": remind_after_days,
            "debug_port": debug_port,
            "profile_dir": str(resolved_profile_dir),
        }

    def discover_theme_pool(self) -> Dict[str, object]:
        themes_dir = self.resolve_themes_dir()
        pool = scan_theme_pool(themes_dir)
        return {
            "themes_dir": str(themes_dir),
            "theme_ids": [e.theme_id for e in pool],
            "count": len(pool),
        }

    def discover_cover_pool(self) -> Dict[str, object]:
        cover_dir = self.resolve_cover_dir()
        try:
            files = list_cover_files(cover_dir)
        except CoverPoolError as exc:
            return {
                "ok": False,
                "cover_dir": str(cover_dir),
                "paths": [],
                "count": 0,
                "error": str(exc),
            }
        paths: List[str] = [str(p) for p in files]
        return {
            "ok": True,
            "cover_dir": str(cover_dir),
            "paths": paths,
            "count": len(paths),
            "error": None,
        }

    def get_wechat_config_status(self):
        app_id, secret, _author = self.resolve_wechat_credentials()
        cover_dir = self.resolve_cover_dir()
        cover_files = sorted(cover_dir.glob("cover_*.png")) if cover_dir.is_dir() else []
        ai_key = (
            _nested_get(self.project_config, ("secrets", "api_key"))
            or _nested_get(self.project_config, ("ai", "api_key"))
            or self.environ.get("OPENROUTER_API_KEY")
        )
        ai_base_url = _nested_get(self.project_config, ("settings", "base_url"))
        ai_model = _nested_get(self.project_config, ("settings", "model"))
        prefer_ai_first = _nested_get(self.project_config, ("cover", "prefer_ai_first"), True)
        return {
            "env_file_exists": self.env_file_exists,
            "appid_ready": _is_real_value(app_id),
            "secret_ready": _is_real_value(secret),
            "covers_ready": len(cover_files) >= 1,
            "cover_count": len(cover_files),
            "ai_cover_ready": prefer_ai_first and _is_real_value(ai_key) and _is_real_value(ai_base_url) and _is_real_value(ai_model),
        }


def _discover_shared_repo_root(base_dir: Path, environ: Mapping[str, str]) -> Optional[Path]:
    raw_override = environ.get("ORDO_SHARED_REPO_ROOT")
    candidates: List[Path] = []
    if raw_override:
        candidates.append(Path(str(raw_override)).expanduser())
    elif sys.platform == "darwin":
        candidates.append(
            Path.home()
            / "Library"
            / "Application Support"
            / DESKTOP_APP_IDENTIFIER
            / "ordo-runtime"
            / "repo"
        )

    try:
        base_resolved = base_dir.resolve()
    except OSError:
        base_resolved = base_dir

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved == base_resolved:
            continue
        if (resolved / "scripts" / "workbench_bridge.py").is_file():
            return resolved
    return None


def _load_env_file_values(base_dir: Path, environ: Mapping[str, str]) -> Tuple[Dict[str, str], bool]:
    merged: Dict[str, str] = {}
    env_exists = False
    shared_root = _discover_shared_repo_root(base_dir, environ)
    if shared_root is not None:
        shared_env_path = shared_root / "secrets.env"
        env_exists = env_exists or shared_env_path.exists()
        merged.update(load_simple_env_file(shared_env_path))

    local_env_path = base_dir / "secrets.env"
    env_exists = env_exists or local_env_path.exists()
    merged.update(load_simple_env_file(local_env_path))
    return merged, env_exists


def load_engine_config(base_dir, cli_overrides=None, environ=None):
    base_path = Path(base_dir)
    env = dict(os.environ if environ is None else environ)
    project_config, project_config_warning = load_json_config(base_path)
    env_file_values, env_file_exists = _load_env_file_values(base_path, env)
    return EngineConfig(
        base_dir=base_path,
        project_config=project_config,
        project_config_warning=project_config_warning,
        env_file_values=env_file_values,
        env_file_exists=env_file_exists,
        environ=env,
        cli_overrides=dict(cli_overrides or {}),
    )
