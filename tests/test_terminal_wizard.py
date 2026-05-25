import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DummyAdapter:
    def __init__(self, base_dir, platform, returncode=0, stdout="", stderr=""):
        self.base_dir = Path(base_dir)
        self.platform = platform
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def prepare(
        self,
        markdown_file,
        mode,
        theme_name=None,
        cover_path=None,
        template_mode=None,
        article_id=None,
        cover_mode=None,
        ai_declaration_mode=None,
        scheduled_publish_at=None,
    ):
        return {
            "platform": self.platform,
            "markdown_file": str(markdown_file),
            "mode": mode,
            "theme_name": theme_name,
            "cover_path": cover_path,
            "template_mode": template_mode,
            "article_id": article_id,
            "cover_mode": cover_mode,
            "ai_declaration_mode": ai_declaration_mode,
            "scheduled_publish_at": scheduled_publish_at,
        }

    def publish(self, prepared_context):
        return {
            "platform": self.platform,
            "command": f"dummy {self.platform} {prepared_context['markdown_file']}",
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    def verify(self, process_result, mode):
        if process_result["returncode"] != 0:
            return "failed"
        return "draft_only" if mode == "draft" else "published"

    def collect_result(self, process_result, mode):
        from tiandi_engine.results.record import ExecutionResult

        return ExecutionResult(
            platform=self.platform,
            stage="publish",
            status=self.verify(process_result, mode),
            summary=process_result["stderr"] or process_result["stdout"] or "ok",
            stdout=process_result["stdout"],
            stderr=process_result["stderr"],
            retryable=process_result["returncode"] != 0,
        )


class TerminalWizardTests(unittest.TestCase):
    def test_read_terminal_defaults_uses_saved_values_and_builtin_defaults(self):
        from tiandi_engine.workbench.terminal_wizard import read_terminal_defaults

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "config.json").write_text(
                json.dumps(
                    {
                        "terminal_wizard": {
                            "defaults": {
                                "source_path": "/tmp/articles",
                                "platforms": ["wechat", "zhihu"],
                                "mode": "publish",
                            }
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            settings = read_terminal_defaults(base)

        self.assertEqual(settings.source_path, "/tmp/articles")
        self.assertEqual(settings.platforms, ("wechat", "zhihu"))
        self.assertEqual(settings.mode, "publish")
        self.assertEqual(settings.cover_mode, "auto")
        self.assertEqual(settings.ai_declaration_mode, "auto")
        self.assertEqual(settings.wechat_theme, "chinese")
        self.assertTrue(settings.continue_on_error)

    def test_save_terminal_defaults_preserves_existing_config(self):
        from tiandi_engine.workbench.terminal_wizard import TerminalWizardSettings, save_terminal_defaults

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "config.json").write_text(
                json.dumps(
                    {
                        "wechat": {"author": "existing-author"},
                        "assignment": {"default_template_mode": "default"},
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            save_terminal_defaults(
                base,
                TerminalWizardSettings(
                    source_path="/tmp/articles",
                    platforms=("wechat", "toutiao"),
                    mode="draft",
                    cover_mode="force_on",
                    ai_declaration_mode="force_off",
                    cover_dir_override="/tmp/covers",
                    wechat_theme_mode="random",
                    wechat_theme="chinese",
                    continue_on_error=False,
                ),
            )

            payload = json.loads((base / "config.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["wechat"]["author"], "existing-author")
        self.assertEqual(payload["assignment"]["default_template_mode"], "default")
        self.assertEqual(payload["terminal_wizard"]["defaults"]["source_path"], "/tmp/articles")
        self.assertEqual(payload["terminal_wizard"]["defaults"]["platforms"], ["wechat", "toutiao"])
        self.assertFalse(payload["terminal_wizard"]["defaults"]["continue_on_error"])

    def test_collect_terminal_settings_keeps_defaults_on_blank_input(self):
        from tiandi_engine.workbench.terminal_wizard import TerminalWizardSettings, collect_terminal_settings

        answers = iter(
            [
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "1",
            ]
        )
        output = io.StringIO()
        settings = collect_terminal_settings(
            defaults=TerminalWizardSettings(
                source_path="/tmp/articles",
                platforms=("wechat", "zhihu"),
                mode="publish",
                cover_mode="force_on",
                ai_declaration_mode="force_off",
                cover_dir_override="/tmp/covers",
                wechat_theme_mode="fixed",
                wechat_theme="mint",
                continue_on_error=False,
            ),
            input_func=lambda _prompt: next(answers),
            output=lambda text="": print(text, file=output),
        )

        self.assertIsNotNone(settings)
        self.assertEqual(settings.source_path, "/tmp/articles")
        self.assertEqual(settings.platforms, ("wechat", "zhihu"))
        self.assertEqual(settings.cover_mode, "force_on")
        self.assertEqual(settings.ai_declaration_mode, "force_off")
        self.assertEqual(settings.wechat_theme, "mint")
        self.assertFalse(settings.continue_on_error)

    def test_collect_terminal_settings_allows_clearing_cover_dir_override(self):
        from tiandi_engine.workbench.terminal_wizard import TerminalWizardSettings, collect_terminal_settings

        answers = iter(
            [
                "",
                "",
                "",
                "",
                "",
                "-",
                "",
                "",
                "1",
            ]
        )
        settings = collect_terminal_settings(
            defaults=TerminalWizardSettings(
                source_path="/tmp/articles",
                platforms=("wechat",),
                cover_dir_override="/tmp/covers",
            ),
            input_func=lambda _prompt: next(answers),
            output=lambda _text="": None,
        )

        self.assertIsNotNone(settings)
        self.assertEqual(settings.cover_dir_override, "")

    def test_execute_publish_flow_writes_retry_queue_when_preflight_blocked(self):
        from tiandi_engine.workbench.terminal_wizard import TerminalWizardSettings, execute_publish_flow

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            article_dir = base / "articles"
            article_dir.mkdir()
            (article_dir / "a.md").write_text("# 标题\n\n正文", encoding="utf-8")
            settings = TerminalWizardSettings(
                source_path=str(article_dir),
                platforms=("zhihu",),
                mode="publish",
            )

            output = io.StringIO()
            with patch(
                "tiandi_engine.workbench.terminal_service.prepare_browser_context",
                return_value={"tabs": [], "workbench": {}, "cdp_connection": None},
            ), patch(
                "tiandi_engine.workbench.terminal_service.publish.run_preflight_checks",
                return_value=(["知乎预检未通过：请先登录"], ["当前 CDP 连接来源：test"]),
            ):
                result = execute_publish_flow(
                    base,
                    settings,
                    output=lambda text="": print(text, file=output),
                )
            self.assertEqual(result["status"], "blocked")
            self.assertTrue(Path(result["operations_path"]).is_file())
            self.assertIn("知乎预检未通过", output.getvalue())
            self.assertIn("python3 scripts/terminal_wizard.py", output.getvalue())

    def test_execute_publish_flow_runs_publish_job_and_reports_summary(self):
        from tiandi_engine.workbench.terminal_wizard import TerminalWizardSettings, execute_publish_flow

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            article_dir = base / "articles"
            article_dir.mkdir()
            (article_dir / "a.md").write_text("# 标题\n\n正文", encoding="utf-8")
            output = io.StringIO()

            with patch(
                "tiandi_engine.workbench.terminal_service.publish.run_preflight_checks",
                return_value=([], []),
            ):
                result = execute_publish_flow(
                    base,
                    TerminalWizardSettings(
                        source_path=str(article_dir),
                        platforms=("wechat",),
                        mode="draft",
                        cover_mode="force_off",
                    ),
                    registry={"wechat": DummyAdapter(base, "wechat", returncode=0, stdout="ok")},
                    output=lambda text="": print(text, file=output),
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["publish_result"]["publish_job"]["success_count"], 1)
            self.assertTrue(Path(result["operations_path"]).is_file())
            self.assertIn("成功: 1", output.getvalue())

    def test_run_terminal_wizard_smoke_saves_defaults_and_completes(self):
        from tiandi_engine.workbench.terminal_wizard import run_terminal_wizard

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            article_dir = base / "articles"
            article_dir.mkdir()
            (article_dir / "a.md").write_text("# 标题\n\n正文", encoding="utf-8")
            answers = iter(
                [
                    str(article_dir),
                    "1",
                    "1",
                    "3",
                    "",
                    "",
                    "",
                    "y",
                    "1",
                ]
            )
            output = io.StringIO()

            with patch(
                "tiandi_engine.workbench.terminal_service.publish.run_preflight_checks",
                return_value=([], []),
            ):
                exit_code = run_terminal_wizard(
                    base,
                    input_func=lambda _prompt: next(answers),
                    output=lambda text="": print(text, file=output),
                    registry={"wechat": DummyAdapter(base, "wechat", returncode=0, stdout="ok")},
                )

            saved = json.loads((base / "config.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(saved["terminal_wizard"]["defaults"]["source_path"], str(article_dir))
        self.assertEqual(saved["terminal_wizard"]["defaults"]["platforms"], ["wechat"])
        self.assertIn("终端版发布向导已启动", output.getvalue())
        self.assertIn("已保存默认配置", output.getvalue())


if __name__ == "__main__":
    unittest.main()
