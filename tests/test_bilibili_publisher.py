import json
import unittest
from pathlib import Path
from unittest.mock import patch

import bilibili_publisher


class BilibiliPublisherTests(unittest.TestCase):
    def test_verify_in_management_list_raises_when_title_missing(self):
        def fake_run_cdp(command, target_id, expression=None):
            if command == "nav":
                return ""
            if "querySelector('a[href=" in expression:
                return "clicked"
            return json.dumps({"ok": False, "count": 2})

        with patch.object(bilibili_publisher, "run_cdp", side_effect=fake_run_cdp), patch.object(
            bilibili_publisher, "take_screenshot"
        ), patch.object(bilibili_publisher.time, "sleep"):
            with self.assertRaises(RuntimeError):
                bilibili_publisher.verify_in_management_list("target-1", "Missing Title", is_draft=True)

    def test_apply_cover_raises_when_crop_confirm_missing(self):
        cover_path = Path("/tmp/cover.png")

        def fake_run_cdp(command, *args, **kwargs):
            if command == "eval" and "input[type=\"file\"]" in args[-1]:
                return "true"
            if command == "setfile":
                return "Set file ok"
            if command == "eval" and "crop-dialog-not-found" in args[-1]:
                return "crop-dialog-not-found"
            return "ok"

        with patch.object(bilibili_publisher, "ensure_custom_cover_on"), patch.object(
            bilibili_publisher, "wait_until", return_value=True
        ), patch.object(bilibili_publisher, "run_cdp", side_effect=fake_run_cdp), patch.object(
            bilibili_publisher.time, "sleep"
        ):
            with self.assertRaises(RuntimeError):
                bilibili_publisher.apply_cover("target-1", cover_path)


if __name__ == "__main__":
    unittest.main()
