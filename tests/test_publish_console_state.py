import tempfile
import unittest
from pathlib import Path

from publish_console_state import (
    advance_after_success,
    build_session,
    finalize_article,
    mark_publishing,
    mark_reviewing,
    record_platform_result,
    save_session,
)


class PublishConsoleStateTests(unittest.TestCase):
    def test_build_session_marks_first_article_reviewing(self):
        session = build_session(
            article_paths=["/tmp/a.md", "/tmp/b.md"],
            platforms=["wechat", "zhihu"],
            mode="publish",
            available_themes=["chinese", "newspaper"],
            default_theme="chinese",
        )

        self.assertEqual(session["current_index"], 0)
        self.assertEqual(session["current_theme"], "chinese")
        self.assertEqual(session["items"][0]["status"], "reviewing")
        self.assertEqual(session["items"][1]["status"], "pending")
        self.assertEqual(session["items"][0]["platforms"]["wechat"]["status"], "pending")

    def test_finalize_article_marks_partial_failed_when_some_platforms_fail(self):
        session = build_session(
            article_paths=["/tmp/a.md"],
            platforms=["wechat", "zhihu", "toutiao"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        mark_publishing(session, 0)
        record_platform_result(session, 0, {"platform": "wechat", "status": "published", "returncode": 0, "stdout": "", "stderr": ""})
        record_platform_result(session, 0, {"platform": "zhihu", "status": "failed", "stdout": "", "stderr": "boom"})
        record_platform_result(session, 0, {"platform": "toutiao", "status": "draft_only", "returncode": 0, "stdout": "", "stderr": ""})

        article_status = finalize_article(session, 0)

        self.assertEqual(article_status, "partial_failed")
        self.assertEqual(session["items"][0]["status"], "partial_failed")
        self.assertEqual(session["summary"]["partial_failed_articles"], 1)
        self.assertEqual(session["summary"]["platform_failures"]["zhihu"], 1)

    def test_finalize_article_marks_failed_when_all_platforms_fail(self):
        session = build_session(
            article_paths=["/tmp/a.md"],
            platforms=["wechat", "zhihu"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        mark_publishing(session, 0)
        record_platform_result(session, 0, {"platform": "wechat", "status": "failed", "stdout": "", "stderr": "x"})
        record_platform_result(session, 0, {"platform": "zhihu", "status": "failed", "stdout": "", "stderr": "y"})

        article_status = finalize_article(session, 0)

        self.assertEqual(article_status, "failed")
        self.assertEqual(session["summary"]["failed_articles"], 1)
        self.assertFalse(advance_after_success(session, 0))

    def test_advance_after_success_moves_to_next_article(self):
        session = build_session(
            article_paths=["/tmp/a.md", "/tmp/b.md"],
            platforms=["wechat"],
            mode="draft",
            available_themes=["chinese", "newspaper"],
            default_theme="newspaper",
        )

        mark_publishing(session, 0)
        record_platform_result(session, 0, {"platform": "wechat", "status": "draft_only", "returncode": 0, "stdout": "", "stderr": ""})
        finalize_article(session, 0)

        moved = advance_after_success(session, 0)

        self.assertTrue(moved)
        self.assertEqual(session["current_index"], 1)
        self.assertEqual(session["items"][1]["status"], "reviewing")
        self.assertEqual(session["current_theme"], "newspaper")

    def test_save_session_writes_json_payload(self):
        session = build_session(
            article_paths=["/tmp/a.md"],
            platforms=["wechat"],
            mode="draft",
            available_themes=["chinese"],
            default_theme="chinese",
        )
        mark_reviewing(session, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.json"
            save_session(path, session)
            text = path.read_text(encoding="utf-8")

        self.assertIn('"current_index": 0', text)
        self.assertIn('"status": "reviewing"', text)

    def test_record_platform_result_keeps_stdout_and_stderr_details(self):
        session = build_session(
            article_paths=["/tmp/a.md"],
            platforms=["wechat"],
            mode="draft",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        record_platform_result(
            session,
            0,
            {
                "platform": "wechat",
                "status": "failed",
                "stdout": "[ERROR] invalid appid",
                "stderr": "urllib warning",
            },
        )

        detail = session["items"][0]["platforms"]["wechat"]["detail"]
        self.assertIn("invalid appid", detail)
        self.assertIn("urllib warning", detail)

    def test_record_platform_result_marks_skipped_existing_as_skipped(self):
        session = build_session(
            article_paths=["/tmp/a.md"],
            platforms=["wechat"],
            mode="draft",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        record_platform_result(
            session,
            0,
            {
                "platform": "wechat",
                "status": "skipped_existing",
                "returncode": 0,
                "stdout": "已存在同标题文章",
                "stderr": "",
            },
        )

        detail = session["items"][0]["platforms"]["wechat"]
        self.assertEqual(detail["status"], "skipped")
        self.assertIn("已存在同标题文章", detail["detail"])

    def test_finalize_article_treats_skipped_platform_as_success(self):
        session = build_session(
            article_paths=["/tmp/a.md"],
            platforms=["wechat", "zhihu"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        mark_publishing(session, 0)
        record_platform_result(session, 0, {"platform": "wechat", "status": "skipped_existing", "returncode": 0, "stdout": "", "stderr": ""})
        record_platform_result(session, 0, {"platform": "zhihu", "status": "published", "returncode": 0, "stdout": "", "stderr": ""})

        article_status = finalize_article(session, 0)

        self.assertEqual(article_status, "success")
        self.assertEqual(session["summary"]["success_articles"], 1)
        self.assertEqual(session["summary"]["platform_failures"]["wechat"], 0)

    def test_finalize_article_counts_limit_reached_as_failure(self):
        session = build_session(
            article_paths=["/tmp/a.md", "/tmp/b.md"],
            platforms=["wechat", "zhihu"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        mark_publishing(session, 0)
        record_platform_result(session, 0, {"platform": "wechat", "status": "published", "returncode": 0, "stdout": "", "stderr": ""})
        record_platform_result(session, 0, {"platform": "zhihu", "status": "limit_reached", "stdout": "", "stderr": "今天已达上限"})
        finalize_article(session, 0)

        mark_publishing(session, 1)
        record_platform_result(session, 1, {"platform": "wechat", "status": "failed", "stdout": "", "stderr": "x"})
        record_platform_result(session, 1, {"platform": "zhihu", "status": "failed", "stdout": "", "stderr": "y"})
        finalize_article(session, 1)

        self.assertEqual(session["summary"]["partial_failed_articles"], 1)
        self.assertEqual(session["summary"]["failed_articles"], 1)
        self.assertEqual(session["summary"]["platform_failures"]["zhihu"], 2)


if __name__ == "__main__":
    unittest.main()
