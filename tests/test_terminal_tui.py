import tempfile
import unittest
from pathlib import Path


class FakeTerminalService:
    def __init__(self, defaults):
        self.defaults = defaults
        self.saved = []
        self.executed = []

    def load_defaults(self):
        return self.defaults

    def save_defaults(self, settings):
        self.saved.append(settings)
        return Path("/tmp/config.json")

    def execute(self, settings, output):
        self.executed.append(settings)
        output("[RUN] publish")
        output("[DONE] success")
        return {
            "status": "completed",
            "operations_path": "/tmp/ops.json",
            "publish_result": {
                "publish_job": {
                    "success_count": 1,
                    "failure_count": 0,
                    "skip_count": 0,
                }
            },
        }


class TerminalTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_tui_loads_defaults_into_fullscreen_form(self):
        from textual.widgets import Input
        from ordo_engine.workbench.terminal_service import TerminalWizardSettings
        from ordo_engine.workbench.terminal_tui import OrdoTuiApp

        app = OrdoTuiApp(
            base_dir=Path("/tmp"),
            service=FakeTerminalService(
                TerminalWizardSettings(
                    source_path="/tmp/articles",
                    platforms=("wechat", "zhihu"),
                    mode="publish",
                    cover_mode="force_on",
                )
            ),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertEqual(app.title, "Ordo")
            self.assertEqual(app.query_one("#source-path", Input).value, "/tmp/articles")

    async def test_tui_save_defaults_uses_service(self):
        from textual.widgets import Input
        from ordo_engine.workbench.terminal_service import TerminalWizardSettings
        from ordo_engine.workbench.terminal_tui import OrdoTuiApp

        service = FakeTerminalService(TerminalWizardSettings(source_path="/tmp/articles"))
        app = OrdoTuiApp(base_dir=Path("/tmp"), service=service)

        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#source-path", Input).value = "/tmp/changed"
            app.collect_settings_from_form = app.collect_settings  # keep method reachable after direct await
            await app.save_defaults_now()

        self.assertEqual(service.saved[-1].source_path, "/tmp/changed")

    async def test_tui_save_defaults_preserves_wechat_theme_fields(self):
        from ordo_engine.workbench.terminal_service import TerminalWizardSettings
        from ordo_engine.workbench.terminal_tui import OrdoTuiApp

        service = FakeTerminalService(
            TerminalWizardSettings(
                source_path="/tmp/articles",
                wechat_theme_mode="random",
                wechat_theme="mint",
            )
        )
        app = OrdoTuiApp(base_dir=Path("/tmp"), service=service)

        async with app.run_test() as pilot:
            await pilot.pause()
            await app.save_defaults_now()

        self.assertEqual(service.saved[-1].wechat_theme_mode, "random")
        self.assertEqual(service.saved[-1].wechat_theme, "mint")

    async def test_tui_run_publish_uses_service_and_updates_logs(self):
        from textual.widgets import Log
        from ordo_engine.workbench.terminal_service import TerminalWizardSettings
        from ordo_engine.workbench.terminal_tui import OrdoTuiApp

        service = FakeTerminalService(TerminalWizardSettings(source_path="/tmp/articles"))
        app = OrdoTuiApp(base_dir=Path("/tmp"), service=service)

        async with app.run_test() as pilot:
            await pilot.pause()
            await app.run_publish_now()
            await pilot.pause()
            log = app.query_one("#event-log", Log)
            self.assertTrue(service.executed)
            self.assertIn("success", str(log.lines))


if __name__ == "__main__":
    unittest.main()
