import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import publish


class PublishParseTests(unittest.TestCase):
    def test_parse_platforms_returns_all_by_default(self):
        self.assertEqual(
            publish.parse_platforms("all"),
            ["wechat", "zhihu", "toutiao", "jianshu", "yidian", "bilibili"],
        )


    def test_parse_platforms_deduplicates_and_preserves_order(self):
        self.assertEqual(
            publish.parse_platforms("zhihu,wechat,zhihu,toutiao"),
            ["zhihu", "wechat", "toutiao"],
        )

    def test_parse_platforms_rejects_unknown_platform(self):
        with self.assertRaises(ValueError):
            publish.parse_platforms("wechat,unknown")

    def test_collect_markdown_files_supports_file_offset_and_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            file_a = tmp_path / "a.md"
            file_b = tmp_path / "b.md"
            note = tmp_path / "note.txt"
            file_a.write_text("# A", encoding="utf-8")
            file_b.write_text("# B", encoding="utf-8")
            note.write_text("noop", encoding="utf-8")

            self.assertEqual(
                publish.collect_markdown_files(file_a),
                [file_a.resolve()],
            )
            self.assertEqual(
                publish.collect_markdown_files(tmp_path, offset=1, limit=1),
                [file_b.resolve()],
            )

    def test_collect_markdown_files_rejects_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                publish.collect_markdown_files(tmpdir)

    def test_article_id_for_path_uses_index_and_safe_stem(self):
        path = Path("/tmp/foo bar/hello world!.md")
        self.assertEqual(publish.article_id_for_path(path, 3), "0003-hello_world_")

    def test_wechat_theme_auto_resolves_to_random(self):
        class Args:
            wechat_theme_mode = "auto"

        self.assertEqual(publish.resolve_wechat_theme_mode(Args(), ["chinese", "ink"]), "random")

    def test_wechat_theme_console_mode_is_rejected(self):
        class Args:
            wechat_theme_mode = "console"

        with self.assertRaisesRegex(RuntimeError, "fixed/random"):
            publish.resolve_wechat_theme_mode(Args(), ["chinese", "ink"])

    def test_filter_published_articles_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            article = root / "AI终稿" / "done.md"
            article.parent.mkdir()
            article.write_text("# Done", encoding="utf-8")
            dashboard = root / "Ordo_Scribe_AI创作看板.md"
            dashboard.write_text(
                "| a | b | c | d | e | [done](AI终稿/done.md) | ✅ 已发表 |\n",
                encoding="utf-8",
            )

            self.assertEqual(
                publish.maybe_filter_already_published_articles(
                    [article],
                    str(article),
                    skip_published=False,
                ),
                [article],
            )
            self.assertEqual(
                publish.maybe_filter_already_published_articles(
                    [article],
                    str(article),
                    skip_published=True,
                ),
                [],
            )

    def test_parser_defaults_cover_mode_to_random(self):
        with patch("sys.argv", ["publish.py", "/tmp/post.md"]):
            args = publish.parse_args()
        self.assertEqual(args.cover_mode, "random")


class PublishClassifyResultTests(unittest.TestCase):
    def test_classify_result_marks_wechat_publish(self):
        result = {
            "platform": "wechat",
            "mode": "publish",
            "returncode": 0,
            "stdout": "已发布到微信公众号",
            "stderr": "",
        }
        self.assertEqual(publish.classify_result(result), "published")

    def test_classify_result_marks_wechat_duplicate_as_skipped(self):
        result = {
            "platform": "wechat",
            "mode": "publish",
            "returncode": 0,
            "stdout": "已存在同标题文章",
            "stderr": "",
        }
        self.assertEqual(publish.classify_result(result), "skipped_existing")

    def test_classify_result_marks_publish_limit(self):
        result = {
            "platform": "jianshu",
            "mode": "publish",
            "returncode": 1,
            "stdout": "",
            "stderr": "今天已达到发布上限，请明天再来",
        }
        self.assertEqual(publish.classify_result(result), "limit_reached")

    def test_classify_result_marks_browser_publish(self):
        result = {
            "platform": "zhihu",
            "mode": "publish",
            "returncode": 0,
            "stdout": "已发布到知乎",
            "stderr": "",
        }
        self.assertEqual(publish.classify_result(result), "published")

    def test_classify_result_marks_browser_publish_from_page_state(self):
        result = {
            "platform": "jianshu",
            "mode": "publish",
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "page_state": "published",
        }
        self.assertEqual(publish.classify_result(result), "published")

    def test_classify_result_marks_toutiao_scheduled_publish(self):
        result = {
            "platform": "toutiao",
            "mode": "publish",
            "returncode": 0,
            "stdout": "已设置定时发布，等待平台执行",
            "stderr": "",
        }
        self.assertEqual(publish.classify_result(result), "scheduled")

    def test_classify_result_marks_toutiao_scheduled_publish_from_actual_ok_log(self):
        result = {
            "platform": "toutiao",
            "mode": "publish",
            "returncode": 0,
            "stdout": "[OK] 已设置头条号定时发布: /tmp/article.md",
            "stderr": "",
            "page_state": "",
        }
        self.assertEqual(publish.classify_result(result), "scheduled")

    def test_classify_result_marks_browser_draft(self):
        result = {
            "platform": "toutiao",
            "mode": "draft",
            "returncode": 0,
            "stdout": "已写入头条草稿页",
            "stderr": "",
        }
        self.assertEqual(publish.classify_result(result), "draft_only")

    def test_classify_result_returns_success_unknown_when_marker_missing(self):
        result = {
            "platform": "yidian",
            "mode": "draft",
            "returncode": 0,
            "stdout": "执行完成",
            "stderr": "",
        }
        self.assertEqual(publish.classify_result(result), "success_unknown")

    def test_classify_result_treats_nonzero_with_draft_hint_as_draft(self):
        result = {
            "platform": "wechat",
            "mode": "draft",
            "returncode": 1,
            "stdout": "",
            "stderr": "草稿已保存，但封面上传失败",
        }
        self.assertEqual(publish.classify_result(result), "draft_only")


if __name__ == "__main__":
    unittest.main()
