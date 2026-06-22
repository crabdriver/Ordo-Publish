from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Mapping

APP_SUPPORT_DIRNAME = "com.ordo.cli"
RUNTIME_REPO_RELATIVE = Path("runtime") / "repo"
REPO_IDENTITY_MARKERS = (
    Path("publish.py"),
    Path("ordo_engine") / "workbench" / "bridge.py",
)
RUNTIME_TEMPLATE_ITEMS = (
    "config.example.json",
    "publish.py",
    "publish_console_state.py",
    "markdown_utils.py",
    "wechat_publisher.py",
    "zhihu_publisher.py",
    "toutiao_publisher.py",
    "jianshu_publisher.py",
    "yidian_publisher.py",
    "live_cdp.mjs",
    "live_cdp_ws_resolver.mjs",
    "scripts",
    "themes",
    "templates",
    "ordo_engine",
)


def resolve_template_root(environ: Mapping[str, str] | None = None) -> Path:
    env = dict(os.environ if environ is None else environ)
    override = env.get("ORDO_REPO_TEMPLATE_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
        _validate_template_root(root)
        return root

    here = Path(__file__).resolve()
    for parent in here.parents:
        if _is_repo_root(parent):
            return parent
    raise RuntimeError("未找到可用的 Ordo 运行模板目录，请先设置 ORDO_REPO_TEMPLATE_ROOT")


def resolve_app_home(environ: Mapping[str, str] | None = None) -> Path:
    env = dict(os.environ if environ is None else environ)
    override = env.get("ORDO_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform.startswith("linux"):
        xdg_root = Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        return (xdg_root.expanduser().resolve() / "ordo").resolve()
    if sys.platform.startswith("win"):
        appdata = env.get("APPDATA")
        if appdata:
            return (Path(appdata).expanduser().resolve() / "Ordo").resolve()
    return (Path.home() / "Library" / "Application Support" / APP_SUPPORT_DIRNAME).resolve()


def runtime_repo_root(app_home: Path) -> Path:
    return Path(app_home).expanduser().resolve() / RUNTIME_REPO_RELATIVE


def seed_runtime_repo(*, template_root: Path, app_home: Path) -> Path:
    source_root = Path(template_root).expanduser().resolve()
    target_root = runtime_repo_root(app_home)
    _validate_template_root(source_root)
    target_root.mkdir(parents=True, exist_ok=True)

    for item_name in RUNTIME_TEMPLATE_ITEMS:
        source_path = source_root / item_name
        if not source_path.exists():
            continue
        target_path = target_root / item_name
        if source_path.is_dir():
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
    return target_root


def _is_repo_root(path: Path) -> bool:
    return all((path / marker).exists() for marker in REPO_IDENTITY_MARKERS)


def _validate_template_root(path: Path) -> None:
    if not _is_repo_root(path):
        missing = [str(path / marker) for marker in REPO_IDENTITY_MARKERS if not (path / marker).exists()]
        raise RuntimeError(f"无效的 Ordo 运行模板目录，缺少: {', '.join(missing)}")

