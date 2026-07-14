import subprocess
import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from ordo_engine.platforms.base import BasePlatformAdapter, SubprocessPlatformAdapter
from ordo_engine.platforms.registry import build_platform_registry
from ordo_engine.platforms.wechat.publisher import WeChatPlatformAdapter
from ordo_engine.results.errors import ErrorType
from ordo_engine.runner.pipeline import run_platform_task


class FakeExecutor:
    def __init__(self, *, returncode=0, stdout="", stderr=""):
        self.result = {
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
            "timeout_seconds": 180,
        }
        self.calls = []

    def execute(self, command, cwd=None, env=None, timeout=180):
        self.calls.append(
            {"command": command, "cwd": cwd, "env": env, "timeout": timeout}
        )
        return dict(self.result)


class PlatformContractTests(unittest.TestCase):
    def test_registry_contains_all_current_platforms(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        self.assertEqual(
            sorted(registry.keys()),
            ["bilibili", "jianshu", "toutiao", "wechat", "yidian", "zhihu"],
        )


    def test_adapters_expose_required_methods(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        for adapter in registry.values():
            self.assertIsInstance(adapter, BasePlatformAdapter)
            self.assertTrue(callable(adapter.prepare))
            self.assertTrue(callable(adapter.publish))
            self.assertTrue(callable(adapter.verify))
            self.assertTrue(callable(adapter.collect_result))

    def test_wechat_prepare_includes_theme(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        prepared = registry["wechat"].prepare(
            markdown_file="/tmp/article.md",
            mode="draft",
            theme_name="chinese",
        )

        self.assertEqual(prepared["platform"], "wechat")
        self.assertIn("wechat_publisher.py", str(prepared["command"][1]))
        self.assertEqual(prepared["command"][-2:], ["--theme", "chinese"])

    def test_wechat_publish_runs_local_command_without_vps_config(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / "secrets.env").write_text("WECHAT_PROXY=http://127.0.0.1:7890\n", encoding="utf-8")
            executor = FakeExecutor(stdout="已写入微信公众号草稿")
            adapter = WeChatPlatformAdapter(repo, executor=executor)
            prepared = adapter.prepare(markdown_file=repo / "article.md", mode="publish")

            result = adapter.publish(prepared)

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(len(executor.calls), 1)
        call = executor.calls[0]
        self.assertEqual(call["cwd"], str(repo))
        self.assertEqual(call["command"], prepared["command"])
        self.assertNotIn("ssh", call["command"])
        self.assertNotIn("scp", call["command"])

    def test_wechat_force_republish_flag_reaches_local_publisher(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            executor = FakeExecutor(stdout="已写入微信公众号草稿")
            registry = {"wechat": WeChatPlatformAdapter(repo, executor=executor)}

            run_platform_task(
                repo, "wechat", repo / "article.md", "draft",
                force_republish=True, registry=registry,
            )

        self.assertIn("--force-republish", executor.calls[0]["command"])

    def test_wechat_draft_marker_is_terminal_success(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            executor = FakeExecutor(stdout="已写入微信公众号草稿")
            registry = {"wechat": WeChatPlatformAdapter(repo, executor=executor)}
            result = run_platform_task(
                repo, "wechat", repo / "article.md", "draft", registry=registry
            )

        self.assertEqual(result["status"], "draft_only")
        self.assertEqual(result["returncode"], 0)

    def test_wechat_unknown_zero_exit_fails_closed(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            executor = FakeExecutor(stdout="request completed")
            registry = {"wechat": WeChatPlatformAdapter(repo, executor=executor)}
            result = run_platform_task(
                repo, "wechat", repo / "article.md", "draft", registry=registry
            )

        self.assertEqual(result["status"], "success_unknown")
        self.assertNotEqual(result["returncode"], 0)

    def test_wechat_mode_mismatch_fails_closed(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            executor = FakeExecutor(stdout="已写入微信公众号草稿")
            registry = {"wechat": WeChatPlatformAdapter(repo, executor=executor)}
            result = run_platform_task(
                repo, "wechat", repo / "article.md", "publish", registry=registry
            )

        self.assertEqual(result["status"], "draft_only")
        self.assertNotEqual(result["returncode"], 0)

    def test_wechat_nonzero_exit_cannot_become_success_from_draft_log(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            executor = FakeExecutor(returncode=9, stdout="已写入微信公众号草稿")
            registry = {"wechat": WeChatPlatformAdapter(repo, executor=executor)}
            result = run_platform_task(
                repo, "wechat", repo / "article.md", "draft", registry=registry
            )

        self.assertEqual(result["status"], "draft_only")
        self.assertEqual(result["returncode"], 9)

    def test_zhihu_prepare_returns_in_process_context(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        prepared = registry["zhihu"].prepare(
            markdown_file="/tmp/article.md",
            mode="draft",
            theme_name="editorial",
            cover_path="/tmp/cover.png",
            template_mode="rich",
            article_id="rev-1",
            cover_mode="force_on",
            ai_declaration_mode="force_off",
        )

        self.assertEqual(prepared["platform"], "zhihu")
        self.assertNotIn("command", prepared)
        self.assertEqual(prepared["markdown_file"], "/tmp/article.md")
        self.assertEqual(prepared["mode"], "draft")
        self.assertEqual(prepared["theme_name"], "editorial")
        self.assertEqual(prepared["cover_path"], "/tmp/cover.png")
        self.assertEqual(prepared["template_mode"], "rich")
        self.assertEqual(prepared["article_id"], "rev-1")
        self.assertEqual(prepared["cover_mode"], "force_on")
        self.assertEqual(prepared["ai_declaration_mode"], "force_off")

    def test_jianshu_prepare_returns_in_process_context(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        prepared = registry["jianshu"].prepare(
            markdown_file="/tmp/article.md",
            mode="draft",
            cover_mode="auto",
            ai_declaration_mode="force_off",
        )

        self.assertNotIn("command", prepared)
        self.assertEqual(prepared["platform"], "jianshu")
        self.assertEqual(prepared["markdown_file"], "/tmp/article.md")
        self.assertEqual(prepared["mode"], "draft")
        self.assertEqual(prepared["cover_mode"], "auto")
        self.assertEqual(prepared["ai_declaration_mode"], "force_off")

    def test_toutiao_prepare_returns_in_process_scheduled_context(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        prepared = registry["toutiao"].prepare(
            markdown_file="/tmp/article.md",
            mode="publish",
            scheduled_publish_at="2026-03-30T09:30",
        )

        self.assertNotIn("command", prepared)
        self.assertEqual(prepared["platform"], "toutiao")
        self.assertEqual(prepared["markdown_file"], "/tmp/article.md")
        self.assertEqual(prepared["mode"], "publish")
        self.assertEqual(prepared["scheduled_publish_at"], "2026-03-30T09:30")

    def test_browser_publish_scripts_accept_theme_cover_in_help(self):
        repo_root = Path(__file__).resolve().parent.parent
        for script in (
            "zhihu_publisher.py",
            "toutiao_publisher.py",
            "jianshu_publisher.py",
            "yidian_publisher.py",
        ):
            completed = subprocess.run(
                [sys.executable, str(repo_root / script), "--help"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, msg=completed.stderr)
            helptext = completed.stdout + completed.stderr
            self.assertIn("--theme", helptext, msg=script)
            self.assertIn("--cover", helptext, msg=script)
            self.assertIn("--article-id", helptext, msg=script)
            self.assertIn("--cover-mode", helptext, msg=script)
            self.assertIn("--ai-declaration-mode", helptext, msg=script)
            if script == "toutiao_publisher.py":
                self.assertIn("--scheduled-publish-at", helptext, msg=script)

    def test_collect_result_builds_structured_failure(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        result = registry["zhihu"].collect_result(
            {
                "platform": "zhihu",
                "returncode": 1,
                "stdout": "",
                "stderr": "编辑器未就绪",
            },
            mode="publish",
        )

        self.assertEqual(result.platform, "zhihu")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.summary, "编辑器未就绪")

    def test_collect_result_marks_timeout_as_retryable_transient_error(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        result = registry["zhihu"].collect_result(
            {
                "platform": "zhihu",
                "returncode": 124,
                "stdout": "partial stdout",
                "stderr": "Process timed out after 180 seconds",
                "timed_out": True,
            },
            mode="publish",
        )

        self.assertEqual(result.platform, "zhihu")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_type, ErrorType.TRANSIENT_ERROR)
        self.assertTrue(result.retryable)

    def test_collect_result_marks_login_required_from_login_message(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        result = registry["zhihu"].collect_result(
            {
                "platform": "zhihu",
                "returncode": 1,
                "stdout": "",
                "stderr": "请先登录知乎后继续",
            },
            mode="publish",
        )

        self.assertEqual(result.error_type, ErrorType.LOGIN_REQUIRED)
        self.assertFalse(result.retryable)

    def test_collect_result_marks_daily_limit_as_rate_limited(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        result = registry["toutiao"].collect_result(
            {
                "platform": "toutiao",
                "returncode": 1,
                "stdout": "今日发文已达 50 篇上限，请明天再来",
                "stderr": "",
            },
            mode="publish",
        )

        self.assertEqual(result.status, "limit_reached")
        self.assertEqual(result.error_type, ErrorType.RATE_LIMITED)
        self.assertTrue(result.retryable)

    def test_collect_result_does_not_treat_review_lock_as_daily_limit(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        result = registry["yidian"].collect_result(
            {
                "platform": "yidian",
                "returncode": 1,
                "stdout": "审核通过前你将无法继续编辑",
                "stderr": "",
            },
            mode="publish",
        )

        self.assertNotEqual(result.status, "limit_reached")
        self.assertNotEqual(result.error_type, ErrorType.RATE_LIMITED)

    def test_collect_result_marks_environment_error_when_cdp_not_ready(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        result = registry["toutiao"].collect_result(
            {
                "platform": "toutiao",
                "returncode": 1,
                "stdout": "",
                "stderr": "无法连接 CDP，请先开启远程调试 Chrome",
            },
            mode="publish",
        )

        self.assertEqual(result.error_type, ErrorType.ENVIRONMENT_ERROR)
        self.assertFalse(result.retryable)

    def test_collect_result_marks_missing_platform_control_as_platform_changed(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        result = registry["zhihu"].collect_result(
            {
                "platform": "zhihu",
                "returncode": 1,
                "stdout": "",
                "stderr": "创作声明未找到",
                "smoke_step": "declare_ai_creation",
            },
            mode="publish",
        )

        self.assertEqual(result.error_type, ErrorType.PLATFORM_CHANGED)
        self.assertFalse(result.retryable)

    def test_collect_result_preserves_current_url_and_page_state(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        result = registry["zhihu"].collect_result(
            {
                "platform": "zhihu",
                "returncode": 1,
                "stdout": "",
                "stderr": "创作声明未找到",
                "current_url": "https://zhuanlan.zhihu.com/write",
                "page_state": "editor_ready",
                "smoke_step": "declare_ai_creation",
            },
            mode="publish",
        )

        self.assertEqual(result.current_url, "https://zhuanlan.zhihu.com/write")
        self.assertEqual(result.page_state, "editor_ready")
        self.assertEqual(result.smoke_step, "declare_ai_creation")

    def test_subprocess_adapter_extracts_structured_smoke_state_from_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "fake_browser.py"
            script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print('script-started')",
                        "print('[SMOKE_STATE] ' + json.dumps({",
                        "    'current_url': 'https://example.com/write',",
                        "    'page_state': 'editor_ready',",
                        "    'smoke_step': 'inject_article'",
                        "}, ensure_ascii=False))",
                    ]
                ),
                encoding="utf-8",
            )
            adapter = SubprocessPlatformAdapter(root, "zhihu", "fake_browser.py")
            prepared = adapter.prepare(markdown_file="/tmp/post.md", mode="draft")

            process_result = adapter.publish(prepared)

        self.assertEqual(process_result["stdout"], "script-started")
        self.assertEqual(process_result["current_url"], "https://example.com/write")
        self.assertEqual(process_result["page_state"], "editor_ready")
        self.assertEqual(process_result["smoke_step"], "inject_article")


if __name__ == "__main__":
    unittest.main()
