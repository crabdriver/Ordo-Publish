import sys
import tempfile
import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

import jianshu_publisher
import toutiao_publisher
import wechat_publisher
import yidian_publisher
import zhihu_publisher
from ordo_engine.assignment.cover_contract import validate_cover


class ZhihuApplyCoverTests(unittest.TestCase):
    def test_apply_cover_calls_setfile_with_known_selector(self):
        mock_run = MagicMock()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"x")
            tmp_path = handle.name
        try:
            zhihu_publisher.apply_cover("abc12345", Path(tmp_path), run_cdp_fn=mock_run)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        mock_run.assert_called_once_with(
            "setfile",
            "abc12345",
            zhihu_publisher.ZHIHU_COVER_FILE_INPUT,
            str(Path(tmp_path).resolve()),
        )

    def test_apply_cover_missing_file_raises(self):
        mock_run = MagicMock()
        with self.assertRaises(RuntimeError) as ctx:
            zhihu_publisher.apply_cover("tid", Path("/no/such/cover.png"), run_cdp_fn=mock_run)
        self.assertIn("不存在", str(ctx.exception))
        mock_run.assert_not_called()


class YidianCoverArgTests(unittest.TestCase):
    def test_draft_mode_with_cover_still_applies_cover(self):
        argv = [
            "yidian_publisher.py",
            "/tmp/article.md",
            "--mode",
            "draft",
            "--cover",
            "/tmp/cover.png",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            yidian_publisher,
            "load_article",
            return_value=("Title", "Body", "<p>Body</p>", Path("/tmp/article.md")),
        ), patch.object(
            yidian_publisher,
            "find_yidian_target",
            return_value="target-1",
        ), patch.object(
            yidian_publisher,
            "ensure_editor_ready",
            return_value="target-1",
        ), patch.object(
            yidian_publisher,
            "inject_article",
            return_value="ok",
        ), patch.object(
            yidian_publisher,
            "scroll_settings_into_view",
        ), patch.object(
            yidian_publisher,
            "ensure_content_statement",
            return_value={"found": True, "checked": False},
        ), patch.object(
            yidian_publisher,
            "attempt_ai_declaration",
            return_value=None,
        ), patch.object(
            yidian_publisher,
            "apply_cover",
        ) as apply_cover_mock, patch.object(
            yidian_publisher,
            "verify_in_management_list",
        ), patch.object(
            yidian_publisher,
            "take_screenshot",
        ), patch.object(
            yidian_publisher,
            "click_action",
            return_value="clicked",
        ):
            yidian_publisher.main()

        apply_cover_mock.assert_called_once_with("target-1", "/tmp/cover.png")



class ZhihuDeclarationTests(unittest.TestCase):
    def test_declare_ai_creation_targets_exact_label(self):
        with patch.object(zhihu_publisher, "wait_until", side_effect=[True, True]), patch.object(
            zhihu_publisher,
            "run_cdp",
            side_effect=[
                '{"ok": true, "text": "未声明"}',
                "clicked",
                "clicked",
                "内容包含AI辅助创作",
            ],
        ) as run_cdp_mock, patch.object(zhihu_publisher.time, "sleep", return_value=None):
            zhihu_publisher.declare_ai_creation("zhihu-target")

        expressions = [call.args[2] for call in run_cdp_mock.call_args_list if call.args[0] == "eval"]
        self.assertTrue(any(zhihu_publisher.ZHIHU_AI_DECLARATION in expression for expression in expressions))


class ToutiaoStrictSettingTests(unittest.TestCase):
    def test_apply_cover_targets_visible_upload_input_in_drawer(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"x")
            cover_path = handle.name
        try:
            with patch.object(toutiao_publisher, "choose_cover_mode", return_value="checked"), patch.object(
                toutiao_publisher,
                "cover_mode_is_selected",
                return_value=True,
            ), patch.object(
                toutiao_publisher,
                "wait_until",
                return_value=True,
            ), patch.object(
                toutiao_publisher,
                "click_visible_button",
                return_value="button-not-found",
            ), patch.object(
                toutiao_publisher,
                "run_cdp",
                return_value="ok",
            ) as mocked_run:
                toutiao_publisher.apply_cover("toutiao-target", cover_path)

            self.assertTrue(
                any(
                    call.args[:4]
                    == (
                        "setfile",
                        "toutiao-target",
                        ".btn-upload-handle input[type=file]",
                        str(Path(cover_path).resolve()),
                    )
                    for call in mocked_run.call_args_list
                )
            )
        finally:
            Path(cover_path).unlink(missing_ok=True)

    def test_apply_cover_falls_back_to_replace_when_add_button_missing(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"x")
            cover_path = handle.name
        try:
            calls = []

            def fake_run_cdp(command, target_id, *args, **kwargs):
                calls.append((command, target_id, *args))
                if command == "click" and args[0] == ".article-cover-add":
                    raise RuntimeError("Element not found: .article-cover-add")
                return "ok"

            with patch.object(toutiao_publisher, "choose_cover_mode", return_value="checked"), patch.object(
                toutiao_publisher,
                "cover_mode_is_selected",
                return_value=True,
            ), patch.object(
                toutiao_publisher,
                "wait_until",
                return_value=True,
            ), patch.object(
                toutiao_publisher,
                "click_visible_button",
                return_value="button-not-found",
            ), patch.object(
                toutiao_publisher,
                "run_cdp",
                side_effect=fake_run_cdp,
            ):
                toutiao_publisher.apply_cover("toutiao-target", cover_path)

            self.assertIn(("click", "toutiao-target", ".article-cover-img-replace"), calls)
        finally:
            Path(cover_path).unlink(missing_ok=True)

    def test_apply_cover_waits_for_confirm_button_to_enable(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"x")
            cover_path = handle.name
        try:
            with patch.object(toutiao_publisher, "choose_cover_mode", return_value="checked"), patch.object(
                toutiao_publisher,
                "cover_mode_is_selected",
                return_value=True,
            ), patch.object(
                toutiao_publisher,
                "wait_until",
                return_value=True,
            ), patch.object(
                toutiao_publisher,
                "run_cdp",
                return_value="ok",
            ), patch.object(
                toutiao_publisher,
                "click_visible_button",
                side_effect=["button-disabled", "clicked"],
            ) as mocked_click, patch.object(
                toutiao_publisher.time,
                "sleep",
                return_value=None,
            ):
                toutiao_publisher.apply_cover("toutiao-target", cover_path)

            self.assertEqual(mocked_click.call_count, 2)
        finally:
            Path(cover_path).unlink(missing_ok=True)

    def test_apply_cover_retries_confirm_with_xy_when_dialog_stays_open(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"x")
            cover_path = handle.name
        try:
            with patch.object(toutiao_publisher, "choose_cover_mode", return_value="checked"), patch.object(
                toutiao_publisher,
                "cover_mode_is_selected",
                return_value=True,
            ), patch.object(
                toutiao_publisher,
                "wait_until",
                side_effect=[True, False, True],
            ), patch.object(
                toutiao_publisher,
                "run_cdp",
                return_value="ok",
            ), patch.object(
                toutiao_publisher,
                "click_visible_button",
                return_value="clicked",
            ), patch.object(
                toutiao_publisher,
                "click_text_by_xy",
                return_value="clicked",
            ) as xy_click, patch.object(
                toutiao_publisher.time,
                "sleep",
                return_value=None,
            ):
                toutiao_publisher.apply_cover("toutiao-target", cover_path)

            xy_click.assert_called_once_with("toutiao-target", "确定")
        finally:
            Path(cover_path).unlink(missing_ok=True)

    def test_apply_cover_raises_when_upload_verification_times_out(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"x")
            cover_path = handle.name
        try:
            with patch.object(toutiao_publisher, "choose_cover_mode", return_value="checked"), patch.object(
                toutiao_publisher,
                "cover_mode_is_selected",
                return_value=True,
            ), patch.object(
                toutiao_publisher,
                "wait_until",
                return_value=False,
            ), patch.object(
                toutiao_publisher,
                "run_cdp",
                return_value="ok",
            ):
                with self.assertRaises(RuntimeError):
                    toutiao_publisher.apply_cover("toutiao-target", cover_path)
        finally:
            Path(cover_path).unlink(missing_ok=True)

    def test_attempt_ai_declaration_raises_when_option_missing(self):
        with patch.object(
            toutiao_publisher,
            "run_cdp",
            side_effect=[
                "already-open",
                '{"found": false}',
            ],
        ):
            with self.assertRaises(RuntimeError):
                toutiao_publisher.ensure_ai_declaration("toutiao-target", True)



class YidianStrictSettingTests(unittest.TestCase):
    def test_apply_cover_raises_when_single_cover_mode_not_confirmed(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            handle.write(b"x")
            cover_path = handle.name
        try:
            with patch.object(
                yidian_publisher,
                "wait_until",
                return_value=False,
            ), patch.object(
                yidian_publisher,
                "run_cdp",
                return_value="ok",
            ):
                with self.assertRaises(RuntimeError):
                    yidian_publisher.apply_cover("yidian-target", cover_path)
        finally:
            Path(cover_path).unlink(missing_ok=True)

    def test_attempt_ai_declaration_targets_exact_label(self):
        with patch.object(
            yidian_publisher,
            "run_cdp",
            return_value='{"found": true, "checked": true}',
        ) as run_cdp_mock:
            yidian_publisher.attempt_ai_declaration("yidian-target")

        expression = run_cdp_mock.call_args.args[2]
        self.assertIn("内容由AI生成", expression)

    def test_attempt_ai_declaration_raises_when_target_missing(self):
        with patch.object(
            yidian_publisher,
            "run_cdp",
            return_value='{"found": false}',
        ):
            with self.assertRaises(RuntimeError):
                yidian_publisher.attempt_ai_declaration("yidian-target")


class WechatCoverResolutionTests(unittest.TestCase):
    def test_create_ai_cover_generates_text_free_4k_source_and_canonical_png(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "scripts").mkdir()
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "cover": {"prefer_ai_first": True},
                        "settings": {"base_url": "https://api.test.invalid", "model": "image-model"},
                        "secrets": {"api_key": "secret-key"},
                    }
                ),
                encoding="utf-8",
            )
            article = root / "article.md"
            article.write_text("---\narticle_id: article-1\n---\n# Title\n", encoding="utf-8")
            captured = {}

            def fake_run(cmd, **kwargs):
                captured["cmd"] = cmd
                source = Path(cmd[cmd.index("--out") + 1])
                source.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (3200, 1600), color=(23, 45, 67)).save(source)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch.object(wechat_publisher, "BASE_DIR", root), patch(
                "subprocess.run",
                side_effect=fake_run,
            ):
                selected = wechat_publisher.create_ai_cover("Title", article)
            cmd = captured["cmd"]
            self.assertEqual(cmd[cmd.index("--aspect-ratio") + 1], "21:9")
            self.assertEqual(cmd[cmd.index("--image-size") + 1], "4K")
            prompt = cmd[cmd.index("--prompt") + 1]
            self.assertIn("任何可见文字", prompt)
            self.assertIn("1920x1080", prompt)
            self.assertIn("1600x800", prompt)
            self.assertEqual(Path(selected).name, "cover.png")
            self.assertEqual(validate_cover(selected), Path(selected).resolve())

    def test_select_cover_for_path_uses_publication_package_cover(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.png"
            cover = root / "assets" / "article-1" / "cover.png"
            source.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (3200, 1600), color=(23, 45, 67)).save(source)
            from ordo_engine.assignment.cover_contract import normalize_cover_source

            normalize_cover_source(source, cover)
            relative = "assets/article-1/cover.png"
            platform_lines = "".join(
                f"  {platform}: {relative}\n"
                for platform in ("wechat", "zhihu", "toutiao", "yidian", "bilibili", "jianshu")
            )
            article = root / "article.md"
            article.write_text(
                f"---\ncover: {relative}\nplatform_covers:\n{platform_lines}---\n# Title\n",
                encoding="utf-8",
            )

            selected = wechat_publisher.select_cover_for_path(article, title="Title")

        self.assertEqual(Path(selected), cover.resolve())


if __name__ == "__main__":
    unittest.main()
