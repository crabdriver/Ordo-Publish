from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from ordo_engine.platforms.playwright.engine import PlaywrightEngine
from ordo_engine.platforms.playwright.human import HumanBehavior
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload,
    PlaywrightBasePublisher,
    PublishResult,
)
from ordo_engine.platforms.playwright.adapters import PlaywrightPlatformAdapter
from ordo_engine.run_state import article_key, state_file_for


class StubPublisher(PlaywrightBasePublisher):
    platform = "stub"

    def _init_human(self, page):
        return MagicMock()

    def navigate_to_editor(self):
        return MagicMock(url="https://example.test/editor")

    def fill_title(self, title):
        pass

    def fill_body(self, body):
        pass

    def upload_cover(self, cover_path):
        pass

    def configure_settings(self, article):
        pass

    def click_publish(self):
        pass

    def save_draft(self):
        pass

    def verify_result(self, mode):
        return PublishResult(platform=self.platform, status="published", page_state="published")


class TestPlaywrightEngine(unittest.TestCase):
    def test_base_publisher_records_steps_in_engine_base_dir_for_active_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            article_path = base_dir / "article.md"
            article_path.write_text("# Article", encoding="utf-8")
            identity = article_key(article_path)
            engine = MagicMock(base_dir=base_dir)
            engine.screenshot.return_value = None
            publisher = StubPublisher(engine)

            with patch(
                "ordo_engine.platforms.playwright.base_publisher.record_step"
            ) as record_step:
                result = publisher.publish(
                    ArticlePayload(title="Article", body="Body", markdown_path=article_path),
                    mode="publish",
                )

        self.assertEqual(result.status, "published")
        self.assertGreater(record_step.call_count, 0)
        expected_state_file = state_file_for(base_dir)
        for call in record_step.call_args_list:
            self.assertEqual(call.args[:3], (identity, "stub", "publish"))
            self.assertEqual(call.kwargs["state_file"], expected_state_file)

    def test_engine_init(self):
        engine = PlaywrightEngine(debug_port=9999, base_dir=Path("/tmp"))
        self.assertEqual(engine.debug_port, 9999)
        self.assertEqual(engine.base_dir, Path("/tmp"))
        self.assertIsNone(engine._browser)

    @patch("ordo_engine.platforms.playwright.engine.sync_playwright")
    def test_engine_connect(self, mock_sync_playwright):
        mock_p = MagicMock()
        mock_sync_playwright.return_value.start.return_value = mock_p
        mock_chromium = mock_p.chromium
        
        engine = PlaywrightEngine(debug_port=9999)
        engine.connect()
        
        mock_chromium.connect_over_cdp.assert_called_once_with("http://localhost:9999")
        self.assertIsNotNone(engine._browser)
        
        engine.close()
        self.assertIsNone(engine._browser)
        mock_p.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
