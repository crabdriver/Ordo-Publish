import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordo_engine.config import EngineConfig, load_engine_config
from ordo_engine.models.task import build_task_spec
from ordo_engine.results.errors import ErrorType, is_blocking_error, is_retryable_error
from ordo_engine.results.record import ExecutionResult


class EngineTaskModelTests(unittest.TestCase):
    def test_build_task_spec_creates_article_and_platform_requests(self):
        spec = build_task_spec(
            source_path=Path("/tmp/articles"),
            article_paths=[Path("/tmp/articles/a.md"), Path("/tmp/articles/b.md")],
            platforms=["wechat", "zhihu"],
            mode="draft",
            default_theme="chinese",
            task_id="task-001",
        )

        self.assertEqual(spec.task_id, "task-001")
        self.assertEqual(spec.mode, "draft")
        self.assertEqual(len(spec.articles), 2)
        self.assertEqual(spec.articles[0].title, "a")
        self.assertEqual(spec.articles[0].platforms[0].platform, "wechat")
        self.assertEqual(spec.articles[0].platforms[0].theme_name, "chinese")

    def test_build_task_spec_optional_article_level_fields(self):
        spec = build_task_spec(
            source_path=Path("/tmp/articles"),
            article_paths=[Path("/tmp/articles/a.md")],
            platforms=["zhihu", "wechat"],
            mode="draft",
            default_theme="chinese",
            default_cover_path="/covers/1.png",
            default_template_mode="rich",
            default_article_id="art-7",
        )
        zh = spec.articles[0].platforms[0]
        wc = spec.articles[0].platforms[1]
        self.assertEqual(zh.platform, "zhihu")
        self.assertEqual(zh.cover_path, "/covers/1.png")
        self.assertEqual(zh.template_mode, "rich")
        self.assertEqual(zh.article_id, "art-7")
        self.assertEqual(wc.theme_name, "chinese")
        self.assertEqual(wc.cover_path, "/covers/1.png")


class EngineConfigTests(unittest.TestCase):
    def test_browser_session_uses_managed_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_engine_config(Path(tmpdir))

        browser_session = config.get_browser_session_settings()
        self.assertTrue(browser_session["enabled"])
        self.assertEqual(browser_session["debug_port"], 9333)
        self.assertEqual(browser_session["remind_after_days"], 5)
        self.assertIn(".ordo", browser_session["profile_dir"])

    def test_browser_session_allows_config_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "config.json").write_text(
                json_text := """
{
  "browser_session": {
    "enabled": false,
    "remind_after_days": 9,
    "profile_dir": ".ordo-browser-profile",
    "debug_port": 9555
  }
}
""".strip(),
                encoding="utf-8",
            )
            self.assertTrue(json_text)
            config = load_engine_config(base_dir)

        browser_session = config.get_browser_session_settings()
        self.assertFalse(browser_session["enabled"])
        self.assertEqual(browser_session["debug_port"], 9555)
        self.assertEqual(browser_session["remind_after_days"], 9)
        self.assertTrue(browser_session["profile_dir"].endswith(".ordo-browser-profile"))

    def test_load_engine_config_uses_process_env_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            "os.environ",
            {
                "WECHAT_APPID": "process-app",
                "WECHAT_SECRET": "process-secret",
                "WECHAT_AUTHOR": "process-author",
            },
            clear=False,
        ):
            config = load_engine_config(Path(tmpdir))

        self.assertEqual(config.resolve_wechat_credentials(), ("process-app", "process-secret", "process-author"))

    def test_load_engine_config_reports_invalid_config_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "config.json").write_text("{broken", encoding="utf-8")

            config = load_engine_config(base_dir)

        self.assertIn("config.json", config.project_config_warning or "")

    def test_load_engine_config_prefers_cli_over_env_and_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "config.json").write_text(
                """
{
  "wechat": {
    "app_id": "config-app",
    "app_secret": "config-secret",
    "author": "config-author"
  }
}
""".strip(),
                encoding="utf-8",
            )
            (base_dir / "secrets.env").write_text(
                "WECHAT_APPID=env-file-app\nWECHAT_SECRET=env-file-secret\nWECHAT_AUTHOR=env-file-author\n",
                encoding="utf-8",
            )

            config = load_engine_config(
                base_dir,
                cli_overrides={
                    "wechat_app_id": "cli-app",
                    "wechat_secret": "cli-secret",
                    "wechat_author": "cli-author",
                },
                environ={
                    "WECHAT_APPID": "env-app",
                    "WECHAT_SECRET": "env-secret",
                    "WECHAT_AUTHOR": "env-author",
                },
            )

        self.assertIsInstance(config, EngineConfig)
        self.assertEqual(config.resolve_wechat_credentials(), ("cli-app", "cli-secret", "cli-author"))

    def test_load_engine_config_falls_back_to_env_then_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "config.json").write_text(
                """
{
  "wechat": {
    "app_id": "config-app",
    "app_secret": "config-secret",
    "author": "config-author"
  }
}
""".strip(),
                encoding="utf-8",
            )

            config = load_engine_config(
                base_dir,
                cli_overrides={},
                environ={
                    "WECHAT_APPID": "env-app",
                    "WECHAT_SECRET": "env-secret",
                    "WECHAT_AUTHOR": "env-author",
                },
            )

        self.assertEqual(config.resolve_wechat_credentials(), ("env-app", "env-secret", "env-author"))

    def test_load_engine_config_falls_back_to_shared_runtime_repo_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_dir = root / "repo"
            shared_repo = root / "shared-runtime" / "repo"
            base_dir.mkdir(parents=True)
            (shared_repo / "scripts").mkdir(parents=True)
            (shared_repo / "scripts" / "workbench_bridge.py").write_text("print('ok')", encoding="utf-8")
            (shared_repo / "secrets.env").write_text(
                "WECHAT_APPID=shared-app\nWECHAT_SECRET=shared-secret\nWECHAT_AUTHOR=shared-author\n",
                encoding="utf-8",
            )

            config = load_engine_config(
                base_dir,
                environ={"ORDO_SHARED_REPO_ROOT": str(shared_repo)},
            )

        self.assertTrue(config.env_file_exists)
        self.assertEqual(config.resolve_wechat_credentials(), ("shared-app", "shared-secret", "shared-author"))

    def test_resolve_cover_dir_prefers_ordo_cover_dir_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cover_dir = Path(tmpdir) / "my-covers"
            cover_dir.mkdir()
            base_dir = Path(tmpdir) / "repo"
            base_dir.mkdir()
            (base_dir / "config.json").write_text(
                '{"assignment": {"cover_dir": "should_not_use"}}',
                encoding="utf-8",
            )
            config = load_engine_config(
                base_dir,
                environ={"ORDO_COVER_DIR": str(cover_dir)},
            )
        self.assertEqual(config.resolve_cover_dir(), cover_dir.resolve())


class EngineErrorAndResultTests(unittest.TestCase):
    def test_error_type_helpers(self):
        self.assertTrue(is_retryable_error(ErrorType.TRANSIENT_ERROR))
        self.assertFalse(is_retryable_error(ErrorType.LOGIN_REQUIRED))
        self.assertTrue(is_blocking_error(ErrorType.CONFIG_ERROR))
        self.assertFalse(is_blocking_error(ErrorType.DUPLICATE_OR_SKIPPED))

    def test_execution_result_to_dict_serializes_error_type(self):
        result = ExecutionResult(
            platform="wechat",
            stage="publish",
            status="failed",
            error_type=ErrorType.CONFIG_ERROR,
            summary="missing credentials",
            stdout="",
            stderr="missing appid",
            current_url="",
            retryable=False,
        )

        payload = result.to_dict()
        self.assertEqual(payload["platform"], "wechat")
        self.assertEqual(payload["error_type"], "config_error")
        self.assertEqual(payload["summary"], "missing credentials")
        self.assertFalse(payload["retryable"])


if __name__ == "__main__":
    unittest.main()
