import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import publish


class PublishConsoleQueueTests(unittest.TestCase):
    def _make_args(self):
        return Namespace(
            mode="publish",
            wechat_theme="chinese",
            no_auto_launch=True,
            cover_mode="force_off",
            cover=None,
        )

    def _make_bundle(self, path):
        return {
            "title": Path(path).stem,
            "word_count": 100,
            "theme_ids": ["chinese"],
            "theme_map": {"chinese": {"name": "Chinese", "colors": {"accent": "#07c160"}}},
            "rendered_map": {"chinese": "<p>preview</p>"},
        }

    def test_console_queue_advances_on_partial_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            article_a = tmp_path / "a.md"
            article_b = tmp_path / "b.md"
            article_a.write_text("# A\n\nbody", encoding="utf-8")
            article_b.write_text("# B\n\nbody", encoding="utf-8")
            session_path = tmp_path / "session.json"
            html_path = tmp_path / "console.html"

            responses = [
                {"platform": "wechat", "returncode": 0, "stdout": "已发布到微信公众号", "stderr": ""},
                {"platform": "zhihu", "returncode": 1, "stdout": "", "stderr": "知乎失败"},
                {"platform": "wechat", "returncode": 0, "stdout": "已发布到微信公众号", "stderr": ""},
                {"platform": "zhihu", "returncode": 0, "stdout": "已发布到知乎", "stderr": ""},
            ]

            with patch.object(publish, "PUBLISH_CONSOLE_SESSION", session_path), \
                 patch.object(publish, "PUBLISH_CONSOLE_HTML", html_path), \
                 patch.object(publish, "build_gallery_bundle", side_effect=lambda **kwargs: self._make_bundle(kwargs["input_path"])), \
                 patch.object(publish, "render_publish_console_page"), \
                 patch.object(publish, "ensure_console_target", return_value="console-target"), \
                 patch.object(publish, "wait_for_console_ready"), \
                 patch.object(publish, "sync_console_state"), \
                 patch.object(publish, "wait_for_console_confirmation", side_effect=[
                     {"type": "confirm", "article_index": 0, "theme": "chinese"},
                     {"type": "confirm", "article_index": 1, "theme": "chinese"},
                 ]), \
                 patch.object(publish, "run_platform", side_effect=responses), \
                 patch.object(publish, "append_publish_record"), \
                 patch.object(publish, "print_result"), \
                 patch.object(publish, "time") as mock_time:
                mock_time.time.side_effect = iter(range(1000, 1100))
                mock_time.sleep.return_value = None

                results = publish.run_console_queue(
                    self._make_args(),
                    platforms=["wechat", "zhihu"],
                    article_paths=[article_a, article_b],
                    available_themes=["chinese"],
                )

            self.assertEqual(len(results), 4)
            session = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertEqual(session["summary"]["partial_failed_articles"], 1)
            self.assertEqual(session["summary"]["success_articles"], 1)
            self.assertEqual(session["summary"]["failed_articles"], 0)
            self.assertEqual(session["phase"], "complete")

    def test_console_queue_stops_when_all_platforms_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            article_a = tmp_path / "a.md"
            article_b = tmp_path / "b.md"
            article_a.write_text("# A\n\nbody", encoding="utf-8")
            article_b.write_text("# B\n\nbody", encoding="utf-8")
            session_path = tmp_path / "session.json"
            html_path = tmp_path / "console.html"

            responses = [
                {"platform": "wechat", "returncode": 1, "stdout": "", "stderr": "微信失败"},
                {"platform": "zhihu", "returncode": 1, "stdout": "", "stderr": "知乎失败"},
            ]

            with patch.object(publish, "PUBLISH_CONSOLE_SESSION", session_path), \
                 patch.object(publish, "PUBLISH_CONSOLE_HTML", html_path), \
                 patch.object(publish, "build_gallery_bundle", side_effect=lambda **kwargs: self._make_bundle(kwargs["input_path"])), \
                 patch.object(publish, "render_publish_console_page"), \
                 patch.object(publish, "ensure_console_target", return_value="console-target"), \
                 patch.object(publish, "wait_for_console_ready"), \
                 patch.object(publish, "sync_console_state"), \
                 patch.object(publish, "wait_for_console_confirmation", return_value={"type": "confirm", "article_index": 0, "theme": "chinese"}), \
                 patch.object(publish, "run_platform", side_effect=responses), \
                 patch.object(publish, "append_publish_record"), \
                 patch.object(publish, "print_result"), \
                 patch.object(publish, "time") as mock_time:
                mock_time.time.side_effect = iter(range(2000, 2100))
                mock_time.sleep.return_value = None

                results = publish.run_console_queue(
                    self._make_args(),
                    platforms=["wechat", "zhihu"],
                    article_paths=[article_a, article_b],
                    available_themes=["chinese"],
                )

            self.assertEqual(len(results), 2)
            session = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertEqual(session["summary"]["failed_articles"], 1)
            self.assertEqual(session["items"][0]["status"], "failed")
            self.assertEqual(session["items"][1]["status"], "pending")

    def test_ensure_console_target_respects_no_auto_launch_without_starting_new_browser(self):
        with patch.object(publish, "list_tabs_or_none", return_value=[{"target": "abc", "url": "https://example.com"}]), \
             patch.object(publish, "run_cdp") as mock_run_cdp, \
             patch.object(publish, "launch_chrome") as mock_launch:
            target = publish.ensure_console_target(auto_launch=False)
        self.assertEqual(target, "abc")
        mock_run_cdp.assert_called_once()
        mock_launch.assert_not_called()

    def test_ensure_console_target_reuses_existing_debug_tab_before_launching_new_browser(self):
        tabs = [{"target": "abc123", "url": "about:blank"}]
        with patch.object(publish, "list_tabs_or_none", return_value=tabs), \
             patch.object(publish, "find_console_target", return_value=None), \
             patch.object(publish, "run_cdp") as mock_run_cdp, \
             patch.object(publish, "launch_chrome") as mock_launch:
            target = publish.ensure_console_target(auto_launch=True)

        self.assertEqual(target, "abc123")
        mock_run_cdp.assert_called_once()
        mock_launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
