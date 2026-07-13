import subprocess
import sys
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path

from ordo_engine.platforms.base import BasePlatformAdapter, SubprocessPlatformAdapter
from ordo_engine.platforms.registry import build_platform_registry
from ordo_engine.results.errors import ErrorType


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

    def test_wechat_publish_refuses_local_without_vps_ip(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / "secrets.env").write_text("WECHAT_PROXY=http://127.0.0.1:7890\n", encoding="utf-8")
            adapter = build_platform_registry(repo)["wechat"]
            prepared = adapter.prepare(markdown_file=repo / "article.md", mode="publish")

            result = adapter.publish(prepared)

        self.assertEqual(result["returncode"], 2)
        self.assertIn("必须走 VPS", result["stderr"])

    def test_wechat_publish_remote_unsets_proxy_and_marks_worker(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            article = repo / "article.md"
            article.write_text("# title\nbody\n", encoding="utf-8")
            (repo / "secrets.env").write_text("VPS_IP=203.0.113.10\nVPS_USER=root\n", encoding="utf-8")
            adapter = build_platform_registry(repo)["wechat"]
            prepared = adapter.prepare(markdown_file=article, mode="publish")

            with patch("ordo_engine.platforms.wechat.publisher.subprocess.run", side_effect=fake_run):
                result = adapter.publish(prepared)

        self.assertEqual(result["returncode"], 0)
        ssh_exec = [cmd for cmd in calls if cmd[0] == "ssh" and "cd ~/ordo-publish" in cmd[-1]][0]
        self.assertIn("unset WECHAT_PROXY HTTP_PROXY HTTPS_PROXY http_proxy https_proxy", ssh_exec[-1])
        self.assertIn("export ORDO_WORKER=1", ssh_exec[-1])

    def test_zhihu_prepare_includes_theme_cover_and_template_mode_in_command(self):
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
        cmd = prepared["command"]
        self.assertIn("zhihu_publisher.py", str(cmd[1]))
        self.assertIn("--theme", cmd)
        self.assertEqual(cmd[cmd.index("--theme") + 1], "editorial")
        self.assertIn("--cover", cmd)
        self.assertEqual(cmd[cmd.index("--cover") + 1], "/tmp/cover.png")
        self.assertIn("--template-mode", cmd)
        self.assertEqual(cmd[cmd.index("--template-mode") + 1], "rich")
        self.assertIn("--article-id", cmd)
        self.assertEqual(cmd[cmd.index("--article-id") + 1], "rev-1")
        self.assertIn("--cover-mode", cmd)
        self.assertEqual(cmd[cmd.index("--cover-mode") + 1], "force_on")
        self.assertIn("--ai-declaration-mode", cmd)
        self.assertEqual(cmd[cmd.index("--ai-declaration-mode") + 1], "force_off")
        self.assertEqual(prepared.get("article_id"), "rev-1")

    def test_jianshu_prepare_accepts_publish_option_modes(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        prepared = registry["jianshu"].prepare(
            markdown_file="/tmp/article.md",
            mode="draft",
            cover_mode="auto",
            ai_declaration_mode="force_off",
        )

        cmd = prepared["command"]
        self.assertIn("--cover-mode", cmd)
        self.assertEqual(cmd[cmd.index("--cover-mode") + 1], "auto")
        self.assertIn("--ai-declaration-mode", cmd)
        self.assertEqual(cmd[cmd.index("--ai-declaration-mode") + 1], "force_off")

    def test_toutiao_prepare_accepts_scheduled_publish_at(self):
        registry = build_platform_registry(Path("/tmp/repo"))
        prepared = registry["toutiao"].prepare(
            markdown_file="/tmp/article.md",
            mode="publish",
            scheduled_publish_at="2026-03-30T09:30",
        )

        cmd = prepared["command"]
        self.assertIn("--scheduled-publish-at", cmd)
        self.assertEqual(cmd[cmd.index("--scheduled-publish-at") + 1], "2026-03-30T09:30")
        self.assertEqual(prepared.get("scheduled_publish_at"), "2026-03-30T09:30")

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
