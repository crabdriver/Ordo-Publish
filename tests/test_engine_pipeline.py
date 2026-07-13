import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from ordo_engine.platforms.base import BasePlatformAdapter, SubprocessPlatformAdapter
from ordo_engine.platforms.playwright.adapters import PlaywrightPlatformAdapter
from ordo_engine.platforms.playwright.base_publisher import PublishResult
from ordo_engine.run_state import article_key, mark_done, state_file_for
from ordo_engine.runner.pipeline import run_platform_task, run_publish_pipeline


class DummyAdapter(BasePlatformAdapter):
    def __init__(self, base_dir, platform, returncode=0, stdout="", stderr=""):
        super().__init__(base_dir=base_dir, platform=platform)
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
            "command": [
                "dummy",
                self.platform,
                str(markdown_file),
                mode,
                theme_name or "",
                cover_path or "",
                template_mode or "",
                article_id or "",
            ],
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
            "command": " ".join(prepared_context["command"]),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    def verify(self, process_result, mode):
        if process_result["returncode"] != 0:
            return "failed"
        return "draft_only" if mode == "draft" else "published"

    def collect_result(self, process_result, mode):
        from ordo_engine.results.record import ExecutionResult

        return ExecutionResult(
            platform=self.platform,
            stage="publish",
            status=self.verify(process_result, mode),
            summary=process_result["stderr"] or process_result["stdout"] or "ok",
            stdout=process_result["stdout"],
            stderr=process_result["stderr"],
            retryable=False,
        )


class EnginePipelineTests(unittest.TestCase):
    def test_run_platform_task_uses_adapter_result(self):
        registry = {
            "wechat": DummyAdapter(
                base_dir=Path("/tmp"),
                platform="wechat",
                returncode=0,
                stdout="ok",
            )
        }

        result = run_platform_task(
            base_dir=Path("/tmp"),
            platform="wechat",
            markdown_file="/tmp/article.md",
            mode="draft",
            theme_name="chinese",
            registry=registry,
        )

        self.assertEqual(result["status"], "draft_only")
        self.assertEqual(result["summary"], "ok")
        self.assertEqual(result["platform"], "wechat")

    def test_wechat_only_pipeline_never_builds_engine(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            article = base_dir / "a.md"
            article.write_text("# A", encoding="utf-8")
            registry = {"wechat": DummyAdapter(base_dir, "wechat", stdout="ok")}
            args = Namespace(mode="draft", continue_on_error=False)

            results, exit_code = run_publish_pipeline(
                base_dir=base_dir,
                args=args,
                article_paths=[article],
                platforms=["wechat"],
                registry=registry,
                engine_factory=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("browser started")
                ),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(results), 1)

    def test_browser_platforms_share_one_engine_and_close_each_page(self):
        class ResultPublisher:
            pages = []

            def __init__(self, engine):
                self.engine = engine
                self.page = MagicMock()
                self.__class__.pages.append(self.page)

            def publish(self, _article, mode):
                status = "published" if mode == "publish" else "draft_saved"
                return PublishResult(platform="stub", status=status, page_state=status)

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            article = base_dir / "a.md"
            article.write_text("# A", encoding="utf-8")
            adapters = {
                name: PlaywrightPlatformAdapter(base_dir, name, ResultPublisher)
                for name in ("zhihu", "jianshu")
            }
            engine = MagicMock(base_dir=base_dir)
            engine_factory = MagicMock(return_value=engine)

            results, exit_code = run_publish_pipeline(
                base_dir=base_dir,
                args=Namespace(mode="publish", continue_on_error=False, headed=False),
                article_paths=[article],
                platforms=["zhihu", "jianshu"],
                registry=adapters,
                engine_factory=engine_factory,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(results), 2)
        engine_factory.assert_called_once_with(
            mode="standalone", headless=True, base_dir=base_dir
        )
        engine.connect.assert_called_once_with()
        engine.close.assert_called_once_with()
        self.assertEqual(len(ResultPublisher.pages), 2)
        for page in ResultPublisher.pages:
            page.close.assert_called_once_with()

    def test_shared_engine_start_failure_aborts_browser_pipeline_without_fallback(self):
        class NeverPublisher:
            def __init__(self, _engine):
                raise AssertionError("publisher constructed after engine failure")

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            article = base_dir / "a.md"
            article.write_text("# A", encoding="utf-8")
            adapter = PlaywrightPlatformAdapter(base_dir, "zhihu", NeverPublisher)
            engine = MagicMock()
            engine.connect.side_effect = RuntimeError("独立浏览器启动失败")

            with self.assertRaisesRegex(RuntimeError, "独立浏览器启动失败"):
                run_publish_pipeline(
                    base_dir=base_dir,
                    args=Namespace(mode="publish", continue_on_error=False, headed=False),
                    article_paths=[article],
                    platforms=["zhihu"],
                    registry={"zhihu": adapter},
                    engine_factory=MagicMock(return_value=engine),
                )

        engine.close.assert_called_once_with()
        self.assertIsNone(adapter._shared_engine)

    def test_completed_browser_group_does_not_start_engine(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            article = base_dir / "a.md"
            article.write_text("# A", encoding="utf-8")
            mark_done(
                article_key(article),
                "zhihu",
                "published",
                "publish",
                state_file=state_file_for(base_dir),
            )
            adapter = PlaywrightPlatformAdapter(base_dir, "zhihu", MagicMock)

            results, exit_code = run_publish_pipeline(
                base_dir=base_dir,
                args=Namespace(mode="publish", continue_on_error=False, headed=False),
                article_paths=[article],
                platforms=["zhihu"],
                registry={"zhihu": adapter},
                engine_factory=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("browser started for completed task")
                ),
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(results[0]["status"], "skipped")

    def test_run_publish_pipeline_stops_on_first_error_when_continue_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            article_a = Path(tmpdir) / "a.md"
            article_b = Path(tmpdir) / "b.md"
            article_a.write_text("# A", encoding="utf-8")
            article_b.write_text("# B", encoding="utf-8")

            registry = {
                "wechat": DummyAdapter(Path(tmpdir), "wechat", returncode=0, stdout="ok"),
                "zhihu": DummyAdapter(Path(tmpdir), "zhihu", returncode=1, stderr="boom"),
            }
            args = Namespace(mode="draft", continue_on_error=False, wechat_theme="chinese")

            results, exit_code = run_publish_pipeline(
                base_dir=Path(tmpdir),
                args=args,
                article_paths=[article_a, article_b],
                platforms=["wechat", "zhihu"],
                registry=registry,
                theme_resolver=lambda _path: "chinese",
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(exit_code, 1)
        self.assertEqual(results[-1]["platform"], "zhihu")

    def test_run_publish_pipeline_continues_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            article_a = Path(tmpdir) / "a.md"
            article_b = Path(tmpdir) / "b.md"
            article_a.write_text("# A", encoding="utf-8")
            article_b.write_text("# B", encoding="utf-8")

            registry = {
                "wechat": DummyAdapter(Path(tmpdir), "wechat", returncode=0, stdout="ok"),
                "zhihu": DummyAdapter(Path(tmpdir), "zhihu", returncode=1, stderr="boom"),
            }
            args = Namespace(mode="draft", continue_on_error=True, wechat_theme="chinese")

            results, exit_code = run_publish_pipeline(
                base_dir=Path(tmpdir),
                args=args,
                article_paths=[article_a, article_b],
                platforms=["wechat", "zhihu"],
                registry=registry,
                theme_resolver=lambda _path: "chinese",
            )

        self.assertEqual(len(results), 4)
        self.assertEqual(exit_code, 1)

    def test_run_publish_pipeline_scopes_state_to_base_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            article = base_dir / "a.md"
            article.write_text("# A", encoding="utf-8")
            registry = {"wechat": DummyAdapter(base_dir, "wechat", returncode=0, stdout="ok")}
            args = Namespace(mode="draft", continue_on_error=False)

            run_publish_pipeline(
                base_dir=base_dir,
                args=args,
                article_paths=[article],
                platforms=["wechat"],
                registry=registry,
            )

            state_file = base_dir / ".ordo" / "publish-state.json"
            self.assertTrue(state_file.exists())

    def test_run_platform_task_passes_context_and_merges_into_payload(self):
        captured = {}

        class CaptureAdapter(DummyAdapter):
            def prepare(self, *args, **kwargs):
                captured.update(kwargs)
                return super().prepare(*args, **kwargs)

        registry = {
            "zhihu": CaptureAdapter(
                base_dir=Path("/tmp"),
                platform="zhihu",
                returncode=0,
                stdout="ok",
            )
        }

        result = run_platform_task(
            base_dir=Path("/tmp"),
            platform="zhihu",
            markdown_file="/tmp/article.md",
            mode="draft",
            theme_name="t1",
            cover_path="/tmp/c.png",
            template_mode="rich",
            article_id="aid-9",
            cover_mode="force_on",
            ai_declaration_mode="force_off",
            scheduled_publish_at="2026-03-30T09:30",
            registry=registry,
        )

        self.assertEqual(captured.get("theme_name"), "t1")
        self.assertEqual(captured.get("cover_path"), "/tmp/c.png")
        self.assertEqual(captured.get("template_mode"), "rich")
        self.assertEqual(captured.get("article_id"), "aid-9")
        self.assertEqual(captured.get("cover_mode"), "force_on")
        self.assertEqual(captured.get("ai_declaration_mode"), "force_off")
        self.assertEqual(captured.get("scheduled_publish_at"), "2026-03-30T09:30")
        self.assertEqual(result.get("theme_name"), "t1")
        self.assertEqual(result.get("cover_path"), "/tmp/c.png")
        self.assertEqual(result.get("template_mode"), "rich")
        self.assertEqual(result.get("article_id"), "aid-9")
        self.assertEqual(result.get("cover_mode"), "force_on")
        self.assertEqual(result.get("ai_declaration_mode"), "force_off")
        self.assertEqual(result.get("scheduled_publish_at"), "2026-03-30T09:30")

    def test_run_publish_pipeline_uses_context_resolver(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            article_a = Path(tmpdir) / "a.md"
            article_a.write_text("# A", encoding="utf-8")

            captured = {}

            class CaptureAdapter(DummyAdapter):
                def prepare(self, *args, **kwargs):
                    captured.update(kwargs)
                    return super().prepare(*args, **kwargs)

            registry = {"zhihu": CaptureAdapter(Path(tmpdir), "zhihu", returncode=0, stdout="ok")}
            args = Namespace(mode="draft", continue_on_error=False)

            def context_resolver(path, platform):
                self.assertEqual(platform, "zhihu")
                return {
                    "theme_name": "ctx-theme",
                    "cover_path": str(Path(tmpdir) / "cover.png"),
                    "template_mode": "plain",
                    "article_id": "x-1",
                    "cover_mode": "auto",
                    "ai_declaration_mode": "force_on",
                    "scheduled_publish_at": "2026-03-30T09:30",
                }

            results, exit_code = run_publish_pipeline(
                base_dir=Path(tmpdir),
                args=args,
                article_paths=[article_a],
                platforms=["zhihu"],
                registry=registry,
                context_resolver=context_resolver,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(results), 1)
        self.assertEqual(captured.get("cover_path"), str(Path(tmpdir) / "cover.png"))
        self.assertEqual(results[0].get("theme_name"), "ctx-theme")
        self.assertEqual(results[0].get("template_mode"), "plain")
        self.assertEqual(results[0].get("article_id"), "x-1")
        self.assertEqual(results[0].get("cover_mode"), "auto")
        self.assertEqual(results[0].get("ai_declaration_mode"), "force_on")
        self.assertEqual(results[0].get("scheduled_publish_at"), "2026-03-30T09:30")
        for key in (
            "article_id",
            "theme_name",
            "template_mode",
            "cover_path",
            "cover_mode",
            "ai_declaration_mode",
            "scheduled_publish_at",
            "platform",
            "status",
            "error_type",
        ):
            self.assertIn(key, results[0], msg=f"pipeline result missing {key} for GUI")

    def test_run_publish_pipeline_theme_resolver_fills_wechat_when_context_omits_theme(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            article_a = Path(tmpdir) / "a.md"
            article_a.write_text("# A", encoding="utf-8")

            captured = {}

            class CaptureAdapter(DummyAdapter):
                def prepare(self, *args, **kwargs):
                    captured.update(kwargs)
                    return super().prepare(*args, **kwargs)

            registry = {"wechat": CaptureAdapter(Path(tmpdir), "wechat", returncode=0, stdout="ok")}
            args = Namespace(mode="draft", continue_on_error=False)

            results, exit_code = run_publish_pipeline(
                base_dir=Path(tmpdir),
                args=args,
                article_paths=[article_a],
                platforms=["wechat"],
                registry=registry,
                theme_resolver=lambda _p: "legacy-theme",
                context_resolver=lambda _path, _platform: {"cover_path": "/c.png"},
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured.get("theme_name"), "legacy-theme")
        self.assertEqual(captured.get("cover_path"), "/c.png")
        self.assertEqual(results[0].get("theme_name"), "legacy-theme")

    def test_run_platform_task_returns_structured_payload_on_subprocess_timeout(self):
        registry = {
            "zhihu": SubprocessPlatformAdapter(
                base_dir=Path("/tmp"),
                platform="zhihu",
                script_name="zhihu_publisher.py",
            )
        }

        with patch(
            "ordo_engine.platforms.base.subprocess.run",
            side_effect=subprocess.TimeoutExpired(
                cmd=["python3", "zhihu_publisher.py"],
                timeout=180,
                output="partial stdout",
                stderr="partial stderr",
            ),
        ):
            result = run_platform_task(
                base_dir=Path("/tmp"),
                platform="zhihu",
                markdown_file="/tmp/article.md",
                mode="publish",
                registry=registry,
            )

        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["error_type"], "transient_error")
        self.assertTrue(result["retryable"])
        self.assertIn("180", result["summary"])

    def test_run_platform_task_payload_always_includes_gui_metadata_keys(self):
        """主入口 / GUI 消费方应能依赖这些键存在（值可为 None）。"""

        class MinimalPrepareAdapter(DummyAdapter):
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
                    "command": ["dummy", str(markdown_file), mode],
                    "mode": mode,
                }

        registry = {
            "zhihu": MinimalPrepareAdapter(
                base_dir=Path("/tmp"),
                platform="zhihu",
                returncode=0,
                stdout="ok",
            )
        }

        result = run_platform_task(
            base_dir=Path("/tmp"),
            platform="zhihu",
            markdown_file="/tmp/article.md",
            mode="draft",
            registry=registry,
        )

        gui_keys = (
            "article_id",
            "theme_name",
            "template_mode",
            "cover_path",
            "cover_mode",
            "ai_declaration_mode",
            "scheduled_publish_at",
            "platform",
            "status",
            "error_type",
        )
        for key in gui_keys:
            self.assertIn(key, result, msg=f"missing GUI key: {key}")


if __name__ == "__main__":
    unittest.main()
