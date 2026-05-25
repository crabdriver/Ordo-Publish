from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Log, RadioButton, RadioSet, Static

from tiandi_engine.workbench import terminal_service
from tiandi_engine.workbench.terminal_service import TerminalWizardSettings

ASCII_HERO = r"""
  ___          _
 / _ \ _ __ __| | ___
| | | | '__/ _` |/ _ \
| |_| | | | (_| | (_) |
 \___/|_|  \__,_|\___/
"""

PLATFORM_LABELS = {
    "wechat": "微信公众号",
    "toutiao": "头条号",
    "zhihu": "知乎",
    "jianshu": "简书",
    "yidian": "一点号",
}


class TerminalServiceAdapter:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir).expanduser().resolve()

    def load_defaults(self) -> TerminalWizardSettings:
        return terminal_service.read_terminal_defaults(self.base_dir)

    def save_defaults(self, settings: TerminalWizardSettings):
        return terminal_service.save_terminal_defaults(self.base_dir, settings)

    def execute(self, settings: TerminalWizardSettings, output):
        return terminal_service.execute_publish_flow(self.base_dir, settings, output=output, command_name="ordo")


class OrdoTuiApp(App):
    TITLE = "Ordo"
    SUB_TITLE = "Homebrew-style terminal publisher"
    CSS = """
    Screen {
        background: #111111;
        color: #e8e8e8;
    }
    #hero {
        color: #74f27b;
        padding: 1 2;
        border: round #2f6b37;
    }
    #subhero {
        color: #8fd5ff;
        padding: 0 2 1 2;
    }
    #body {
        height: 1fr;
    }
    .panel {
        border: round #3a3a3a;
        padding: 1 1;
        margin: 0 1 1 1;
        height: 1fr;
    }
    .panel-title {
        color: #74f27b;
        text-style: bold;
        padding-bottom: 1;
    }
    .field-label {
        color: #8fd5ff;
        padding-top: 1;
    }
    .action-button {
        width: 1fr;
        margin-top: 1;
    }
    #summary {
        color: #f2f2f2;
    }
    #event-log {
        height: 1fr;
    }
    """
    BINDINGS = [
        Binding("r", "run_publish", "Run"),
        Binding("s", "save_defaults", "Save"),
        Binding("l", "reload_defaults", "Reload"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, *, base_dir, service=None):
        super().__init__()
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.service = service or TerminalServiceAdapter(self.base_dir)
        self.loaded_defaults = TerminalWizardSettings()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(ASCII_HERO, id="hero")
        yield Static("Deep publish and recover in one terminal.", id="subhero")
        with Horizontal(id="body"):
            with Vertical(classes="panel"):
                yield Static("Current Config", classes="panel-title")
                yield Static("", id="summary")
            with VerticalScroll(classes="panel"):
                yield Static("Configure", classes="panel-title")
                yield Static("文章来源目录", classes="field-label")
                yield Input(placeholder="/path/to/articles", id="source-path")
                yield Static("平台", classes="field-label")
                for platform in terminal_service.PLATFORM_ORDER:
                    yield Checkbox(PLATFORM_LABELS[platform], id=f"platform-{platform}")
                yield Static("发布模式", classes="field-label")
                with RadioSet(id="mode-set"):
                    yield RadioButton("draft", id="mode-draft")
                    yield RadioButton("publish", id="mode-publish")
                yield Static("封面策略", classes="field-label")
                with RadioSet(id="cover-set"):
                    yield RadioButton("auto", id="cover-auto")
                    yield RadioButton("force_on", id="cover-force_on")
                    yield RadioButton("force_off", id="cover-force_off")
                yield Static("AI 声明", classes="field-label")
                with RadioSet(id="ai-set"):
                    yield RadioButton("auto", id="ai-auto")
                    yield RadioButton("force_on", id="ai-force_on")
                    yield RadioButton("force_off", id="ai-force_off")
                yield Static("封面目录覆盖（可空）", classes="field-label")
                yield Input(placeholder="/path/to/covers", id="cover-dir")
                yield Checkbox("单个平台失败后继续", id="continue-on-error")
                yield Checkbox("保存为默认配置", id="save-as-default")
                yield Button("Save Defaults", id="save-defaults", classes="action-button")
                yield Button("Run Publish", id="run-publish", variant="success", classes="action-button")
                yield Button("Reload Defaults", id="reload-defaults", classes="action-button")
            with Vertical(classes="panel"):
                yield Static("Execution Log", classes="panel-title")
                yield Log(id="event-log", auto_scroll=True, highlight=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.loaded_defaults = self.service.load_defaults()
        self.load_defaults_into_form(self.loaded_defaults)
        self.refresh_summary()
        self.write_log("[INFO] `ordo` 已启动。按 R 可直接执行。")

    def write_log(self, line: str) -> None:
        self.query_one("#event-log", Log).write_line(line)

    def load_defaults_into_form(self, settings: TerminalWizardSettings) -> None:
        self.query_one("#source-path", Input).value = settings.source_path
        self.query_one("#cover-dir", Input).value = settings.cover_dir_override
        self.query_one("#continue-on-error", Checkbox).value = settings.continue_on_error
        self.query_one("#save-as-default", Checkbox).value = settings.save_as_default
        for platform in terminal_service.PLATFORM_ORDER:
            self.query_one(f"#platform-{platform}", Checkbox).value = platform in settings.platforms
        self.query_one(f"#mode-{settings.mode}", RadioButton).value = True
        self.query_one(f"#cover-{settings.cover_mode}", RadioButton).value = True
        self.query_one(f"#ai-{settings.ai_declaration_mode}", RadioButton).value = True

    def collect_settings(self) -> TerminalWizardSettings:
        selected_platforms = tuple(
            platform
            for platform in terminal_service.PLATFORM_ORDER
            if self.query_one(f"#platform-{platform}", Checkbox).value
        ) or ("wechat",)
        mode = "publish" if self.query_one("#mode-publish", RadioButton).value else "draft"
        cover_mode = next(
            option
            for option in terminal_service.PUBLISH_OPTION_MODES
            if self.query_one(f"#cover-{option}", RadioButton).value
        )
        ai_mode = next(
            option
            for option in terminal_service.PUBLISH_OPTION_MODES
            if self.query_one(f"#ai-{option}", RadioButton).value
        )
        return TerminalWizardSettings(
            source_path=self.query_one("#source-path", Input).value.strip(),
            platforms=selected_platforms,
            mode=mode,
            cover_mode=cover_mode,
            ai_declaration_mode=ai_mode,
            cover_dir_override=self.query_one("#cover-dir", Input).value.strip(),
            wechat_theme_mode=self.loaded_defaults.wechat_theme_mode,
            wechat_theme=self.loaded_defaults.wechat_theme,
            continue_on_error=self.query_one("#continue-on-error", Checkbox).value,
            save_as_default=self.query_one("#save-as-default", Checkbox).value,
        )

    def refresh_summary(self) -> None:
        settings = self.collect_settings()
        summary = "\n".join(
            [
                f"Path: {settings.source_path or '(未设置)'}",
                f"Platforms: {', '.join(settings.platforms)}",
                f"Mode: {settings.mode}",
                f"Cover: {settings.cover_mode}",
                f"AI: {settings.ai_declaration_mode}",
                f"CoverDir: {settings.cover_dir_override or 'default'}",
                f"Continue: {'yes' if settings.continue_on_error else 'no'}",
                f"SaveDefault: {'yes' if settings.save_as_default else 'no'}",
            ]
        )
        self.query_one("#summary", Static).update(summary)

    async def save_defaults_now(self) -> None:
        settings = self.collect_settings()
        path = self.service.save_defaults(settings)
        self.loaded_defaults = settings
        self.write_log(f"[INFO] 已保存默认配置: {path}")
        self.refresh_summary()

    async def run_publish_now(self) -> None:
        settings = self.collect_settings()
        self.refresh_summary()
        if settings.save_as_default:
            await self.save_defaults_now()

        def emit(line: str) -> None:
            self.call_from_thread(self.write_log, line)

        result = await asyncio.to_thread(self.service.execute, settings, emit)
        self.write_log(f"[RESULT] 状态: {result['status']}")

    async def reload_defaults_now(self) -> None:
        self.loaded_defaults = self.service.load_defaults()
        self.load_defaults_into_form(self.loaded_defaults)
        self.refresh_summary()
        self.write_log("[INFO] 已重新载入默认配置。")

    async def action_run_publish(self) -> None:
        await self.run_publish_now()

    async def action_save_defaults(self) -> None:
        await self.save_defaults_now()

    async def action_reload_defaults(self) -> None:
        await self.reload_defaults_now()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "save-defaults":
            await self.save_defaults_now()
        elif button_id == "run-publish":
            await self.run_publish_now()
        elif button_id == "reload-defaults":
            await self.reload_defaults_now()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self.refresh_summary()

    def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
        self.refresh_summary()

    def on_radio_set_changed(self, _event) -> None:
        self.refresh_summary()


def main(*, base_dir) -> int:
    app = OrdoTuiApp(base_dir=base_dir)
    app.run()
    return 0

