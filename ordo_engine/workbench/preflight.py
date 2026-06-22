from __future__ import annotations

import json
import platform
import shutil
import ssl
import uuid
from pathlib import Path

import publish

from ordo_engine.workbench.bridge import import_sources
from ordo_engine.workbench.matrix import build_publish_matrix

WORKBENCH_ROOT = Path(".ordo") / "workbench"
PREFLIGHT_ROOT = WORKBENCH_ROOT / "preflight"


def _python_env_report() -> dict:
    try:
        import pypdf  # noqa: F401

        pypdf_installed = True
    except Exception:
        pypdf_installed = False
    return {
        "python_version": platform.python_version(),
        "python_executable": shutil.which("python3"),
        "ssl_backend": ssl.OPENSSL_VERSION,
        "pypdf_installed": pypdf_installed,
        "tesseract_path": shutil.which("tesseract"),
    }


def _write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_real_publish_preflight(
    base_dir,
    *,
    source_path: str,
    cover_dir: str | None,
    platforms,
    seed: int = 20260329,
    report_id: str | None = None,
):
    root = Path(base_dir).expanduser().resolve()
    imported = import_sources(root, import_mode="folder", source_path=source_path)
    matrix = build_publish_matrix(
        root,
        drafts=tuple(imported["job"]["drafts"]),
        platforms=tuple(platforms),
        seed=seed,
        matrix_id=f"matrix-{report_id}" if report_id else None,
        cover_dir_override=cover_dir,
    )
    browser_platforms = [platform for platform in platforms if platform in publish.BROWSER_PLATFORMS]
    tabs = []
    if browser_platforms:
        tabs, _launched_app = publish.ensure_chrome_ready(browser_platforms, base_dir=root)
        publish.open_missing_platform_tabs(platforms, auto_launch=True)
        tabs = publish.list_tabs(base_dir=root)
    else:
        tabs = publish.list_tabs_or_none(base_dir=root) or []
    workbench = publish.bind_workbench(platforms, tabs)
    blockers, warnings = publish.run_preflight_checks(
        platforms,
        "publish",
        workbench,
        base_dir=root,
        cover_dir_override=Path(cover_dir).expanduser().resolve() if cover_dir else None,
        cdp_connection=publish.get_cdp_connection_metadata(),
        cover_mode="force_on",
    )
    resolved_report_id = report_id or f"preflight-{uuid.uuid4().hex}"
    payload = {
        "report_id": resolved_report_id,
        "source_path": str(Path(source_path).expanduser().resolve()),
        "cover_dir": str(Path(cover_dir).expanduser().resolve()) if cover_dir else None,
        "platforms": list(platforms),
        "environment": _python_env_report(),
        "import_job": imported["job"],
        "matrix": {
            "matrix_path": matrix["matrix_path"],
            "representative_article_ids": matrix["representative_article_ids"],
            "production_strategy": matrix["production_strategy"],
        },
        "tabs": tabs,
        "workbench": workbench,
        "blockers": list(blockers),
        "warnings": list(warnings),
        "ready": not blockers,
    }
    report_path = root / PREFLIGHT_ROOT / f"{resolved_report_id}.json"
    _write_report(report_path, payload)
    payload["report_path"] = str(report_path)
    return payload
