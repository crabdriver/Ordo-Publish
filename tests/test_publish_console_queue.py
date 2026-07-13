import unittest
from argparse import Namespace
from unittest.mock import patch

import publish


class PublishConsoleQueueTests(unittest.TestCase):
    def test_console_theme_mode_is_disabled_with_safe_alternatives(self):
        args = Namespace(wechat_theme_mode="console")

        with self.assertRaisesRegex(RuntimeError, "fixed/random"):
            publish.resolve_wechat_theme_mode(args, ["chinese"])

    def test_console_queue_fails_before_any_browser_or_publish_helper(self):
        forbidden = (
            "ensure_console_target",
            "list_tabs",
            "list_tabs_or_none",
            "ensure_chrome_ready",
            "launch_chrome",
            "run_cdp",
            "run_platform",
        )
        patches = [
            patch.object(
                publish,
                name,
                side_effect=AssertionError(f"forbidden helper called: {name}"),
            )
            for name in forbidden
        ]
        mocks = [item.start() for item in patches]
        self.addCleanup(lambda: [item.stop() for item in patches])

        with self.assertRaisesRegex(RuntimeError, "fixed/random"):
            publish.run_console_queue(
                Namespace(),
                platforms=["wechat", "zhihu"],
                article_paths=[],
                available_themes=["chinese"],
            )

        for mock in mocks:
            mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
