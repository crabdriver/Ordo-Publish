import json
import sys
import unittest
from unittest.mock import patch

import toutiao_publisher
import yidian_publisher
import zhihu_publisher


class BrowserPublisherBehaviorTests(unittest.TestCase):
    def test_yidian_sets_uploaded_cover_via_visible_button(self):
        with patch.object(yidian_publisher, "run_cdp", return_value="clicked") as run:
            self.assertEqual(yidian_publisher.set_latest_cover("target-1"), "clicked")

        expression = run.call_args.args[2]
        self.assertIn(".pre-img-item .setting-btn", expression)

    def test_zhihu_ai_declaration_matches_live_copy(self):
        self.assertEqual(
            zhihu_publisher.normalize_ui_text(zhihu_publisher.ZHIHU_AI_DECLARATION),
            zhihu_publisher.normalize_ui_text("包含 AI 辅助创作"),
        )

    def test_toutiao_wait_for_draft_saved_accepts_saving_signal(self):
        with patch.object(toutiao_publisher, "wait_until", return_value=True) as mocked_wait:
            self.assertTrue(toutiao_publisher.wait_for_draft_saved("target-1"))

        args = mocked_wait.call_args.args
        self.assertEqual(args[0], "target-1")
        self.assertIn("草稿已保存", args[1])


    def test_toutiao_wait_for_cover_upload_accepts_uploaded_signal(self):
        with patch.object(toutiao_publisher, "wait_until", return_value=True) as mocked_wait:
            self.assertTrue(toutiao_publisher.wait_for_cover_upload("target-1"))

        args = mocked_wait.call_args.args
        self.assertEqual(args[0], "target-1")
        self.assertIn("已上传", args[1])

    def test_toutiao_wait_for_scheduled_publish_accepts_schedule_signal(self):
        with patch.object(toutiao_publisher, "wait_until", return_value=True) as mocked_wait:
            self.assertTrue(toutiao_publisher.wait_for_scheduled_publish("target-1"))

        args = mocked_wait.call_args.args
        self.assertEqual(args[0], "target-1")
        self.assertIn("已设置定时发布", args[1])

    def test_toutiao_click_button_with_fallback_prefers_dom_click(self):
        with patch.object(toutiao_publisher, "click_button", return_value="clicked") as mocked_dom, patch.object(
            toutiao_publisher,
            "wait_for_publish_button_effect",
            return_value=True,
        ) as mocked_wait, patch.object(
            toutiao_publisher,
            "click_text_by_xy",
        ) as mocked_xy:
            result = toutiao_publisher.click_button_with_fallback("target-1", "预览并发布")

        self.assertEqual(result, "clicked")
        mocked_dom.assert_called_once_with("target-1", "预览并发布")
        mocked_wait.assert_called_once_with("target-1", "预览并发布", timeout_seconds=3)
        mocked_xy.assert_not_called()

    def test_toutiao_click_button_with_fallback_uses_xy_when_dom_click_has_no_effect(self):
        with patch.object(toutiao_publisher, "click_button", return_value="clicked"), patch.object(
            toutiao_publisher,
            "wait_for_publish_button_effect",
            return_value=False,
        ), patch.object(
            toutiao_publisher,
            "click_text_by_xy",
            return_value="clicked",
        ) as mocked_xy:
            result = toutiao_publisher.click_button_with_fallback("target-1", "预览并发布")

        self.assertEqual(result, "clicked")
        mocked_xy.assert_called_once_with("target-1", "预览并发布")

    def test_toutiao_click_button_with_fallback_uses_xy_when_dom_misses(self):
        with patch.object(toutiao_publisher, "click_button", return_value="button-not-found"), patch.object(
            toutiao_publisher,
            "click_text_by_xy",
            return_value="clicked",
        ) as mocked_xy:
            result = toutiao_publisher.click_button_with_fallback("target-1", "确认发布")

        self.assertEqual(result, "clicked")
        mocked_xy.assert_called_once_with("target-1", "确认发布")

    def test_yidian_publish_verification_checks_review_list(self):
        with patch.object(yidian_publisher, "run_cdp", return_value="ok") as mocked_cdp, patch.object(
            yidian_publisher,
            "wait_until",
            return_value=True,
        ), patch.object(yidian_publisher, "take_screenshot"), patch.object(yidian_publisher.time, "sleep"):
            yidian_publisher.verify_in_management_list("target-1", "标题", is_draft=False)

        mocked_cdp.assert_any_call(
            "nav",
            "target-1",
            "https://mp.yidianzixun.com/#/ArticleManual/original/review",
        )

    def test_yidian_draft_verification_checks_draft_list(self):
        with patch.object(yidian_publisher, "run_cdp", return_value="clicked") as mocked_cdp, patch.object(
            yidian_publisher,
            "wait_until",
            return_value=True,
        ), patch.object(yidian_publisher, "take_screenshot"), patch.object(yidian_publisher.time, "sleep"):
            yidian_publisher.verify_in_management_list("target-1", "标题", is_draft=True)

        mocked_cdp.assert_any_call(
            "nav",
            "target-1",
            "https://mp.yidianzixun.com/#/ArticleManual/original/draft",
        )

    def test_toutiao_detect_publish_limit_matches_daily_cap_copy(self):
        with patch.object(
            toutiao_publisher,
            "run_cdp",
            return_value="无法发布\n今日发文已达 50 篇上限，可保存草稿后明日发布\n确定",
        ):
            self.assertEqual(toutiao_publisher.detect_publish_limit("target-1"), "今日发文已达")

    def test_toutiao_select_byte_option_targets_visible_select_options(self):
        with patch.object(
            toutiao_publisher,
            "run_cdp",
            side_effect=["clicked", "clicked"],
        ) as mocked_run, patch.object(toutiao_publisher.time, "sleep", return_value=None):
            result = toutiao_publisher.select_byte_option("target-1", ".hour-select", "10")

        self.assertEqual(result, "clicked")
        eval_expression = mocked_run.call_args_list[1].args[2]
        self.assertIn("byte-select-option", eval_expression)
        self.assertIn("10", eval_expression)

    def test_toutiao_ensure_editor_ready_uses_fallback_selectors(self):
        with patch.object(toutiao_publisher, "wait_until", return_value=True) as mocked_wait:
            toutiao_publisher.ensure_editor_ready("target-1")

        expression = mocked_wait.call_args.args[1]
        self.assertIn('placeholder*=\\"标题\\"', expression)
        self.assertIn('[contenteditable=\\"true\\"]', expression)

    def test_toutiao_inject_article_uses_fallback_title_and_editor_selectors(self):
        with patch.object(toutiao_publisher, "run_cdp", side_effect=["标题", '{"bodyLength": 2}']) as mocked_run:
            toutiao_publisher.inject_article("target-1", "标题", "<p>正文</p>")

        title_expression = mocked_run.call_args_list[0].args[2]
        body_expression = mocked_run.call_args_list[1].args[2]
        self.assertIn('placeholder*=\\"标题\\"', title_expression)
        self.assertIn('[contenteditable=\\"true\\"]', body_expression)

    def test_toutiao_main_publish_with_schedule_uses_schedule_path(self):
        argv = [
            "toutiao_publisher.py",
            "article.md",
            "--mode",
            "publish",
            "--scheduled-publish-at",
            "2026-03-30T09:30",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            toutiao_publisher,
            "load_article",
            return_value=("标题", "正文", "<p>正文</p>", "/tmp/article.md"),
        ), patch.object(
            toutiao_publisher, "find_toutiao_target", return_value="target-1"
        ), patch.object(
            toutiao_publisher, "ensure_editor_ready", return_value="target-1"
        ), patch.object(
            toutiao_publisher, "inject_article", return_value='{"title":"标题","bodyLength":2}'
        ), patch.object(
            toutiao_publisher, "choose_cover_mode", return_value="checked"
        ), patch.object(
            toutiao_publisher, "cover_mode_is_selected", return_value=True
        ), patch.object(
            toutiao_publisher, "choose_required_radio", return_value="clicked"
        ), patch.object(
            toutiao_publisher, "ensure_ai_declaration", return_value="checked"
        ), patch.object(
            toutiao_publisher, "click_button_with_fallback", return_value="clicked"
        ) as mocked_click, patch.object(
            toutiao_publisher, "schedule_publish", return_value="scheduled"
        ) as mocked_schedule, patch.object(
            toutiao_publisher, "detect_publish_limit", return_value=None
        ), patch.object(
            toutiao_publisher, "wait_for_scheduled_publish", return_value=True
        ), patch.object(
            toutiao_publisher, "verify_in_management_list"
        ), patch.object(
            toutiao_publisher, "take_screenshot"
        ), patch.object(
            toutiao_publisher, "emit_smoke_state", return_value=None
        ):
            toutiao_publisher.main()

        mocked_schedule.assert_called_once_with("target-1", "2026-03-30T09:30")
        mocked_click.assert_any_call("target-1", "定时发布")

    def test_yidian_attempt_ai_declaration_rechecks_after_click(self):
        outputs = iter(
            [
                json.dumps(
                    {
                        "found": True,
                        "checked": False,
                        "already": False,
                        "text": "内容由AI生成",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "found": True,
                        "checked": True,
                        "already": False,
                        "text": "内容由AI生成",
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        with patch.object(yidian_publisher, "run_cdp", side_effect=lambda *_args: next(outputs)), patch.object(
            yidian_publisher.time, "sleep", return_value=None
        ):
            result = yidian_publisher.attempt_ai_declaration("target-1")

        self.assertTrue(result["checked"])

    def test_yidian_wait_for_cover_upload_accepts_cover_items(self):
        with patch.object(yidian_publisher, "wait_until", return_value=True) as mocked_wait:
            self.assertTrue(yidian_publisher.wait_for_cover_upload("target-1"))

        args = mocked_wait.call_args.args
        self.assertEqual(args[0], "target-1")
        self.assertIn("cover-item", args[1])

    def test_yidian_ensure_content_statement_supports_personal_opinion(self):
        outputs = iter(
            [
                json.dumps(
                    {
                        "found": True,
                        "checked": False,
                        "already": False,
                        "text": "个人观点，仅供参考",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "found": True,
                        "checked": True,
                        "already": False,
                        "text": "个人观点，仅供参考",
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        with patch.object(yidian_publisher, "run_cdp", side_effect=lambda *_args: next(outputs)), patch.object(
            yidian_publisher.time, "sleep", return_value=None
        ):
            result = yidian_publisher.ensure_content_statement("target-1", "个人观点，仅供参考")

        self.assertTrue(result["checked"])

    def test_yidian_main_force_off_uses_default_cover_before_draft_save(self):
        argv = [
            "yidian_publisher.py",
            "article.md",
            "--mode",
            "draft",
            "--cover-mode",
            "force_off",
            "--ai-declaration-mode",
            "force_off",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            yidian_publisher,
            "load_article",
            return_value=("标题", "正文", "<p>正文</p>", "/tmp/article.md"),
        ), patch.object(
            yidian_publisher, "find_yidian_target", return_value="target-1"
        ), patch.object(
            yidian_publisher, "ensure_editor_ready", return_value="target-1"
        ), patch.object(
            yidian_publisher, "inject_article", return_value='{"title":"标题","bodyLength":2}'
        ), patch.object(
            yidian_publisher, "ensure_content_statement", return_value={"found": True, "checked": True}
        ) as mocked_statement, patch.object(
            yidian_publisher, "scroll_settings_into_view"
        ), patch.object(
            yidian_publisher, "select_default_cover", return_value="selected-default"
        ) as mocked_cover, patch.object(
            yidian_publisher, "wait_for_default_cover", return_value=True
        ) as mocked_wait_cover, patch.object(
            yidian_publisher, "click_action", return_value="clicked"
        ), patch.object(
            yidian_publisher, "verify_in_management_list"
        ), patch.object(
            yidian_publisher, "take_screenshot"
        ), patch.object(
            yidian_publisher, "emit_smoke_state", return_value=None
        ):
            yidian_publisher.main()

        mocked_statement.assert_called_once_with("target-1", "个人观点，仅供参考")
        mocked_cover.assert_called_once_with("target-1")
        mocked_wait_cover.assert_called_once_with("target-1")


if __name__ == "__main__":
    unittest.main()
