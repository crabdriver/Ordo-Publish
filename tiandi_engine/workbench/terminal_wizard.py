from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional, Sequence

from tiandi_engine.config import load_engine_config
from tiandi_engine.workbench.terminal_service import (
    MODE_OPTIONS,
    PLATFORM_ORDER,
    PUBLISH_OPTION_MODES,
    TerminalWizardSettings,
    execute_publish_flow as _execute_publish_flow,
    read_terminal_defaults,
    save_terminal_defaults,
)


def _prompt_text(
    label: str,
    *,
    default: str,
    input_func: Callable[[str], str],
    allow_empty: bool = False,
    clear_marker: Optional[str] = None,
) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input_func(f"{label}{suffix}: ").strip()
        if clear_marker and raw == clear_marker:
            return ""
        if raw:
            return raw
        if default:
            return default
        if allow_empty:
            return ""


def _prompt_yes_no(
    label: str,
    *,
    default: bool,
    input_func: Callable[[str], str],
) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input_func(f"{label} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "1"}:
            return True
        if raw in {"n", "no", "0"}:
            return False


def _prompt_choice(
    label: str,
    *,
    options: Sequence[str],
    default: str,
    input_func: Callable[[str], str],
) -> str:
    options_text = ", ".join(f"{index + 1}:{name}" for index, name in enumerate(options))
    default_index = options.index(default) + 1 if default in options else 1
    while True:
        raw = input_func(f"{label} ({options_text}) [{default_index}]: ").strip()
        if not raw:
            return default
        if raw.isdigit():
            index = int(raw) - 1
            if 0 <= index < len(options):
                return options[index]
        if raw in options:
            return raw


def _prompt_platforms(
    *,
    default: Sequence[str],
    input_func: Callable[[str], str],
) -> tuple[str, ...]:
    options_text = ", ".join(f"{index + 1}:{name}" for index, name in enumerate(PLATFORM_ORDER))
    default_text = ",".join(default)
    while True:
        raw = input_func(f"选择平台（可输编号或名称，逗号分隔；{options_text}） [{default_text}]: ").strip()
        if not raw:
            return tuple(default)
        values = []
        for token in (item.strip() for item in raw.split(",")):
            if not token:
                continue
            if token.isdigit():
                index = int(token) - 1
                if 0 <= index < len(PLATFORM_ORDER):
                    values.append(PLATFORM_ORDER[index])
                    continue
            if token in PLATFORM_ORDER:
                values.append(token)
        if values:
            deduped = []
            for item in values:
                if item not in deduped:
                    deduped.append(item)
            return tuple(deduped)


def _render_settings_summary(settings: TerminalWizardSettings) -> list[str]:
    return [
        "",
        "===== 本次终端发布配置 =====",
        f"文章路径: {settings.source_path}",
        f"平台: {', '.join(settings.platforms)}",
        f"模式: {settings.mode}",
        f"封面策略: {settings.cover_mode}",
        f"AI 声明: {settings.ai_declaration_mode}",
        f"封面目录: {settings.cover_dir_override or '沿用 config.json/环境变量默认值'}",
        f"失败后继续: {'是' if settings.continue_on_error else '否'}",
        f"保存为默认配置: {'是' if settings.save_as_default else '否'}",
    ]


def collect_terminal_settings(
    *,
    defaults: Optional[TerminalWizardSettings] = None,
    input_func: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> Optional[TerminalWizardSettings]:
    seed = defaults or TerminalWizardSettings()
    while True:
        settings = TerminalWizardSettings(
            source_path=_prompt_text("文章来源目录或文件", default=seed.source_path, input_func=input_func),
            platforms=_prompt_platforms(default=seed.platforms, input_func=input_func),
            mode=_prompt_choice("发布模式", options=MODE_OPTIONS, default=seed.mode, input_func=input_func),
            cover_mode=_prompt_choice(
                "封面策略",
                options=PUBLISH_OPTION_MODES,
                default=seed.cover_mode,
                input_func=input_func,
            ),
            ai_declaration_mode=_prompt_choice(
                "AI 声明策略",
                options=PUBLISH_OPTION_MODES,
                default=seed.ai_declaration_mode,
                input_func=input_func,
            ),
            cover_dir_override=_prompt_text(
                "封面目录（可为空，留空表示沿用当前默认）",
                default=seed.cover_dir_override,
                input_func=input_func,
                allow_empty=True,
                clear_marker="-",
            ),
            wechat_theme_mode=seed.wechat_theme_mode,
            wechat_theme=seed.wechat_theme,
            continue_on_error=_prompt_yes_no(
                "单个平台失败后继续后续任务",
                default=seed.continue_on_error,
                input_func=input_func,
            ),
            save_as_default=_prompt_yes_no(
                "将本次配置保存为下次默认值",
                default=seed.save_as_default,
                input_func=input_func,
            ),
        )
        for line in _render_settings_summary(settings):
            output(line)
        action = _prompt_choice(
            "确认操作",
            options=("start", "edit", "quit"),
            default="start",
            input_func=input_func,
        )
        if action == "start":
            return settings
        if action == "quit":
            return None
        seed = settings


def run_terminal_wizard(
    base_dir,
    *,
    input_func: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
    registry=None,
) -> int:
    root = Path(base_dir).expanduser().resolve()
    defaults = read_terminal_defaults(root)
    runtime_defaults = defaults
    if not runtime_defaults.cover_dir_override:
        runtime_defaults = replace(runtime_defaults, cover_dir_override=str(load_engine_config(root).resolve_cover_dir()))
    output("终端版发布向导已启动。直接回车即可沿用方括号中的默认值。")
    settings = collect_terminal_settings(defaults=runtime_defaults, input_func=input_func, output=output)
    if settings is None:
        output("已取消本次终端发布任务。")
        return 0
    if settings.save_as_default:
        config_path = save_terminal_defaults(root, settings)
        output(f"[INFO] 已保存默认配置: {config_path}")
    result = execute_publish_flow(root, settings, registry=registry, output=output)
    return 0 if result["status"] == "completed" else 1


def execute_publish_flow(
    base_dir,
    settings: TerminalWizardSettings,
    *,
    registry=None,
    output: Callable[[str], None] = print,
) -> dict[str, object]:
    return _execute_publish_flow(
        base_dir,
        settings,
        registry=registry,
        output=output,
        command_name="python3 scripts/terminal_wizard.py",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    del argv
    return run_terminal_wizard(Path(__file__).resolve().parents[2])

