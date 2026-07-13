import json
import tempfile
import unittest
from pathlib import Path

import ordo_engine.state.session as session_module
from ordo_engine.results.errors import ErrorType
from ordo_engine.models.workbench import CoverAssignment, TemplateAssignment
from ordo_engine.state.session import (
    advance_after_success,
    build_session,
    finalize_article,
    mark_publishing,
    mark_reviewing,
    merge_assignments_into_session,
    record_platform_result,
    save_session,
)


class EngineSessionTests(unittest.TestCase):
    def test_build_session_initializes_platform_metadata(self):
        session = build_session(
            article_paths=["/tmp/a.md", "/tmp/b.md"],
            platforms=["wechat", "zhihu"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        self.assertEqual(session["current_index"], 0)
        self.assertEqual(session["items"][0]["status"], "reviewing")
        self.assertEqual(session["items"][0]["platforms"]["wechat"]["step"], "pending")
        self.assertIsNone(session["items"][0]["platforms"]["wechat"]["error_type"])

    def test_record_platform_result_keeps_error_type_and_retryable(self):
        session = build_session(
            article_paths=["/tmp/a.md"],
            platforms=["wechat"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        record_platform_result(
            session,
            0,
            {
                "platform": "wechat",
                "status": "failed",
                "stage": "publish",
                "error_type": ErrorType.CONFIG_ERROR,
                "retryable": False,
                "stdout": "",
                "stderr": "missing credentials",
            },
        )

        platform_state = session["items"][0]["platforms"]["wechat"]
        self.assertEqual(platform_state["status"], "failed")
        self.assertEqual(platform_state["step"], "publish")
        self.assertEqual(platform_state["error_type"], "config_error")
        self.assertFalse(platform_state["retryable"])

    def test_record_platform_result_marks_success_unknown_failed(self):
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
            {"platform": "wechat", "status": "success_unknown", "returncode": 0},
        )

        self.assertEqual(session["items"][0]["platforms"]["wechat"]["status"], "failed")

    def test_record_platform_result_marks_terminal_nonzero_failed(self):
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
            {"platform": "wechat", "status": "draft_only", "returncode": 1},
        )

        self.assertEqual(session["items"][0]["platforms"]["wechat"]["status"], "failed")

    def test_record_platform_result_marks_mode_mismatch_failed(self):
        session = build_session(
            article_paths=["/tmp/a.md"],
            platforms=["wechat"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )

        record_platform_result(
            session,
            0,
            {"platform": "wechat", "status": "draft_only", "returncode": 0},
        )

        self.assertEqual(session["items"][0]["platforms"]["wechat"]["status"], "failed")

    def test_finalize_article_and_advance_match_existing_console_behavior(self):
        session = build_session(
            article_paths=["/tmp/a.md", "/tmp/b.md"],
            platforms=["wechat", "zhihu"],
            mode="draft",
            available_themes=["chinese", "newspaper"],
            default_theme="newspaper",
        )

        mark_publishing(session, 0)
        record_platform_result(session, 0, {"platform": "wechat", "status": "draft_only", "returncode": 0, "stdout": "", "stderr": ""})
        record_platform_result(session, 0, {"platform": "zhihu", "status": "failed", "stdout": "", "stderr": "boom"})
        article_status = finalize_article(session, 0)

        self.assertEqual(article_status, "partial_failed")
        self.assertTrue(advance_after_success(session, 0))
        self.assertEqual(session["items"][1]["status"], "reviewing")

    def test_save_session_persists_structured_state(self):
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
        self.assertIn('"step": "pending"', text)

    def test_save_session_preserves_template_and_cover_assignments(self):
        session = build_session(
            article_paths=["/tmp/post_one.md"],
            platforms=["zhihu", "wechat"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )
        ta = TemplateAssignment(
            article_id="post_one",
            template_mode="default",
            theme_id="chinese",
            theme_name="中国风",
            is_random=True,
            is_manual_override=False,
            is_confirmed=False,
        )
        ca = CoverAssignment(
            article_id="post_one",
            platform="zhihu",
            cover_path=Path("/tmp/covers/x.png"),
            cover_source="pool",
            is_random=True,
            is_manual_override=False,
        )
        merge_assignments_into_session(session, (ta,), (ca,))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session.json"
            save_session(path, session)
            data = json.loads(path.read_text(encoding="utf-8"))

        item0 = data["items"][0]
        self.assertEqual(item0["template_assignment"]["theme_id"], "chinese")
        self.assertEqual(item0["cover_assignments"]["zhihu"]["cover_path"], "/tmp/covers/x.png")

    def test_merge_assignments_into_session_raises_for_unknown_article_id(self):
        session = build_session(
            article_paths=["/tmp/post_one.md"],
            platforms=["zhihu"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )
        ta = TemplateAssignment(
            article_id="missing_post",
            template_mode="default",
            theme_id="chinese",
            theme_name="中国风",
            is_random=True,
            is_manual_override=False,
            is_confirmed=False,
        )

        with self.assertRaises(ValueError) as ctx:
            merge_assignments_into_session(session, (ta,), ())

        self.assertIn("missing_post", str(ctx.exception))

    def test_collect_recent_cover_paths_returns_recent_paths(self):
        session = build_session(
            article_paths=["/tmp/post_one.md", "/tmp/post_two.md"],
            platforms=["zhihu", "toutiao"],
            mode="publish",
            available_themes=["chinese"],
            default_theme="chinese",
        )
        merge_assignments_into_session(
            session,
            (),
            (
                CoverAssignment(
                    article_id="post_one",
                    platform="zhihu",
                    cover_path=Path("/tmp/covers/a.png"),
                    cover_source="pool",
                    is_random=True,
                    is_manual_override=False,
                ),
                CoverAssignment(
                    article_id="post_one",
                    platform="toutiao",
                    cover_path=Path("/tmp/covers/b.png"),
                    cover_source="pool",
                    is_random=True,
                    is_manual_override=False,
                ),
                CoverAssignment(
                    article_id="post_two",
                    platform="zhihu",
                    cover_path=Path("/tmp/covers/c.png"),
                    cover_source="pool",
                    is_random=True,
                    is_manual_override=False,
                ),
            ),
        )

        recent = session_module.collect_recent_cover_paths(session, limit=2)
        self.assertEqual(recent, ("/tmp/covers/b.png", "/tmp/covers/c.png"))


if __name__ == "__main__":
    unittest.main()
