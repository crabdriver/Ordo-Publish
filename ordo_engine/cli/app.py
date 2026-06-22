from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from ordo_engine.cli.runtime import resolve_app_home, resolve_template_root, seed_runtime_repo


def ensure_runtime_importable(runtime_repo_root: Path) -> None:
    runtime_path = str(Path(runtime_repo_root).expanduser().resolve())
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)


def run_repo_entrypoint(*, runtime_repo_root: Path, argv: Sequence[str]) -> int:
    ensure_runtime_importable(runtime_repo_root)
    from ordo_engine.workbench import terminal_tui

    del argv
    return terminal_tui.main(base_dir=runtime_repo_root)


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    env = dict(os.environ if environ is None else environ)
    template_root = resolve_template_root(env)
    app_home = resolve_app_home(env)
    runtime_root = seed_runtime_repo(template_root=template_root, app_home=app_home)
    os.environ.setdefault("ORDO_HOME", str(app_home))
    os.environ.setdefault("ORDO_RUNTIME_REPO_ROOT", str(runtime_root))
    return run_repo_entrypoint(runtime_repo_root=runtime_root, argv=list(argv or ()))

