from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

import publish
from tiandi_engine.config import load_engine_config, load_json_config
from tiandi_engine.platforms import build_platform_registry
from tiandi_engine.workbench.bridge import BROWSER_PLATFORMS, import_sources, plan_publish_job, run_publish_job
from tiandi_engine.workbench.operations_matrix import write_operations_matrix

PLATFORM_ORDER = ("wechat", "toutiao", "zhihu", "jianshu", "yidian")
MODE_OPTIONS = ("draft", "publish")
PUBLISH_OPTION_MODES = ("auto", "force_on", "force_off")
CONFIG_SECTION = ("terminal_wizard", "defaults")


@dataclass(frozen=True)
class TerminalWizardSettings:
    source_path: str = ""
    platforms: tuple[str, ...] = ("wechat",)
    mode: str = "draft"
    cover_mode: str = "auto"
    ai_declaration_mode: str = "auto"
    cover_dir_override: str = ""
    wechat_theme_mode: str = "fixed"
    wechat_theme: str = "chinese"
    continue_on_error: bool = True
    save_as_default: bool = False

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, object]]) -> "TerminalWizardSettings":
        payload = dict(raw or {})
        platforms = payload.get("platforms") or cls().platforms
        if isinstance(platforms, str):
            platforms = tuple(item.strip() for item in platforms.split(",") if item.strip())
        return cls(
            source_path=str(payload.get("source_path") or ""),
            platforms=tuple(str(item) for item in (platforms or cls().platforms)),
            mode=_normalize_choice(payload.get("mode"), MODE_OPTIONS, default=cls().mode),
            cover_mode=_normalize_choice(payload.get("cover_mode"), PUBLISH_OPTION_MODES, default=cls().cover_mode),
            ai_declaration_mode=_normalize_choice(
                payload.get("ai_declaration_mode"),
                PUBLISH_OPTION_MODES,
                default=cls().ai_declaration_mode,
            ),
            cover_dir_override=str(payload.get("cover_dir_override") or ""),
            wechat_theme_mode=str(payload.get("wechat_theme_mode") or cls().wechat_theme_mode),
            wechat_theme=str(payload.get("wechat_theme") or cls().wechat_theme),
            continue_on_error=bool(
                cls().continue_on_error if payload.get("continue_on_error") is None else payload.get("continue_on_error")
            ),
            save_as_default=bool(payload.get("save_as_default", False)),
        )

    def to_persisted_dict(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "platforms": list(self.platforms),
            "mode": self.mode,
            "cover_mode": self.cover_mode,
            "ai_declaration_mode": self.ai_declaration_mode,
            "cover_dir_override": self.cover_dir_override,
            "wechat_theme_mode": self.wechat_theme_mode,
            "wechat_theme": self.wechat_theme,
            "continue_on_error": self.continue_on_error,
        }


def _normalize_choice(value, options: Sequence[str], *, default: str) -> str:
    raw = str(value or "").strip()
    return raw if raw in options else default


def _nested_get(data: Mapping[str, object], keys: Sequence[str], default=None):
    current = data
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _write_atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def read_terminal_defaults(base_dir) -> TerminalWizardSettings:
    root = Path(base_dir).expanduser().resolve()
    config = load_engine_config(root)
    return TerminalWizardSettings.from_mapping(_nested_get(config.project_config, CONFIG_SECTION, {}))


def save_terminal_defaults(base_dir, settings: TerminalWizardSettings) -> Path:
    root = Path(base_dir).expanduser().resolve()
    config_data, config_warning = load_json_config(root)
    config_path = root / "config.json"
    if config_warning and config_path.exists():
        raise RuntimeError(f"无法写入终端默认配置：{config_warning}")
    payload = dict(config_data or {})
    payload.setdefault("terminal_wizard", {})
    payload["terminal_wizard"]["defaults"] = settings.to_persisted_dict()
    _write_atomic_json(config_path, payload)
    return config_path


@contextmanager
def temporary_cover_env(cover_dir_override: str):
    if not cover_dir_override:
        yield
        return
    previous = os.environ.get("ORDO_COVER_DIR")
    os.environ["ORDO_COVER_DIR"] = cover_dir_override
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("ORDO_COVER_DIR", None)
        else:
            os.environ["ORDO_COVER_DIR"] = previous


def detect_import_mode(source_path: Path) -> str:
    if source_path.is_dir():
        return "folder"
    if source_path.is_file():
        return "file"
    raise FileNotFoundError(f"未找到文章路径：{source_path}")


def prepare_browser_context(base_dir, platforms: Sequence[str], *, output: Callable[[str], None]) -> dict[str, object]:
    browser_platforms = [platform for platform in platforms if platform in BROWSER_PLATFORMS]
    if not browser_platforms:
        return {"tabs": [], "workbench": {}, "cdp_connection": None}

    tabs, launched_app = publish.ensure_chrome_ready(browser_platforms, base_dir=base_dir)
    if launched_app:
        output(f"[INFO] 已自动启动浏览器: {launched_app}")
    opened = publish.open_missing_platform_tabs(browser_platforms)
    if opened:
        output(f"[INFO] 已自动补开平台标签页: {', '.join(opened)}")
    tabs = publish.list_tabs(base_dir=base_dir)
    workbench = publish.bind_workbench(browser_platforms, tabs)
    warmed = publish.warm_platforms(browser_platforms)
    if warmed:
        output(f"[INFO] 已自动预热平台标签页: {', '.join(warmed)}")
    return {
        "tabs": tabs,
        "workbench": workbench,
        "cdp_connection": publish.get_cdp_connection_metadata(base_dir=base_dir),
    }


def print_import_summary(import_payload: Mapping[str, object], output: Callable[[str], None]) -> None:
    job = dict(import_payload.get("job") or {})
    output(f"[INFO] 已导入文章: {job.get('article_count', 0)} / 源文件: {job.get('source_count', 0)}")
    for failure in job.get("failures", []) or []:
        path = failure.get("source_path") or "unknown"
        output(f"[WARN] 导入失败: {path} -> {failure.get('message')}")


def event_printer(event: Mapping[str, object], output: Callable[[str], None]) -> None:
    event_type = event.get("type")
    if event_type == "article_started":
        output(f"\n[ARTICLE] {event.get('title') or event.get('article_id')}")
        return
    if event_type == "platform_started":
        output(f"[RUN] 开始执行平台: {event.get('platform')}")
        return
    if event_type == "platform_finished":
        result = dict(event.get("result") or {})
        summary = result.get("summary") or result.get("stderr") or result.get("stdout") or ""
        output(
            f"[DONE] {result.get('platform')} -> {result.get('status')} "
            f"(exit={result.get('returncode')}, retryable={bool(result.get('retryable'))}) {summary}".strip()
        )
        return
    if event_type == "job_finished":
        job = dict(event.get("publish_job") or {})
        output(
            f"[SUMMARY] success={job.get('success_count', 0)} "
            f"failed={job.get('failure_count', 0)} skipped={job.get('skip_count', 0)}"
        )


def print_retry_guidance(operations_path: Path, output: Callable[[str], None], *, command_name: str = "ordo") -> None:
    output(f"[INFO] 已生成续跑队列: {operations_path}")
    output(
        f"[INFO] 如需纯终端续跑，请重新执行 `{command_name}`，"
        "沿用默认配置并参考上面的续跑队列缩小文章或平台范围。"
    )


def execute_publish_flow(
    base_dir,
    settings: TerminalWizardSettings,
    *,
    registry=None,
    output: Callable[[str], None] = print,
    command_name: str = "ordo",
) -> dict[str, object]:
    root = Path(base_dir).expanduser().resolve()
    source = Path(settings.source_path).expanduser().resolve()
    import_payload = import_sources(root, import_mode=detect_import_mode(source), source_path=str(source))
    print_import_summary(import_payload, output)
    drafts = list((import_payload.get("job") or {}).get("drafts") or [])
    if not drafts:
        operations_path = write_operations_matrix(
            root,
            bundle_id="ops-terminal-empty-import",
            preflight_report={"blockers": ["未导入到任何可发布文章"], "warnings": []},
        )
        output("[BLOCK] 未导入到任何可发布文章")
        print_retry_guidance(operations_path, output, command_name=command_name)
        return {
            "status": "blocked",
            "import_payload": import_payload,
            "publish_result": None,
            "operations_path": str(operations_path),
        }

    browser_context = prepare_browser_context(root, settings.platforms, output=output)
    blockers, warnings = publish.run_preflight_checks(
        list(settings.platforms),
        settings.mode,
        browser_context.get("workbench") or {},
        base_dir=root,
        cover_dir_override=(settings.cover_dir_override or None),
        cdp_connection=browser_context.get("cdp_connection"),
        cover_mode=settings.cover_mode,
    )
    for warning in warnings:
        output(f"[WARN] {warning}")
    if blockers:
        for blocker in blockers:
            output(f"[BLOCK] {blocker}")
        operations_path = write_operations_matrix(
            root,
            bundle_id="ops-terminal-preflight",
            preflight_report={"blockers": blockers, "warnings": warnings},
        )
        print_retry_guidance(operations_path, output, command_name=command_name)
        return {
            "status": "blocked",
            "import_payload": import_payload,
            "publish_result": None,
            "operations_path": str(operations_path),
        }

    with temporary_cover_env(settings.cover_dir_override):
        plan_payload = plan_publish_job(
            root,
            drafts=drafts,
            platforms=settings.platforms,
            mode=settings.mode,
            continue_on_error=settings.continue_on_error,
            cover_mode=settings.cover_mode,
            ai_declaration_mode=settings.ai_declaration_mode,
            clear_last_result=True,
        )
        publish_result = run_publish_job(
            root,
            plan_payload,
            registry=registry or build_platform_registry(root),
            event_sink=lambda event: event_printer(event, output),
        )

    operations_path = write_operations_matrix(
        root,
        bundle_id=f"ops-terminal-{plan_payload['publish_job']['job_id']}",
        preflight_report={"blockers": blockers, "warnings": warnings},
        publish_result=publish_result,
    )
    job = publish_result["publish_job"]
    output("===== summary =====")
    output(f"成功: {job.get('success_count', 0)}")
    output(f"失败: {job.get('failure_count', 0)}")
    output(f"跳过: {job.get('skip_count', 0)}")
    output(f"续跑队列: {operations_path}")
    if job.get("failure_count", 0):
        print_retry_guidance(Path(operations_path), output, command_name=command_name)
    return {
        "status": "completed" if job.get("failure_count", 0) == 0 else "failed",
        "import_payload": import_payload,
        "publish_result": publish_result,
        "operations_path": str(operations_path),
    }

