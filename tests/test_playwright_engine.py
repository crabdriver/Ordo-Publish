from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ordo_engine.platforms.playwright.engine import PlaywrightEngine
from ordo_engine.platforms.playwright.human import HumanBehavior
from ordo_engine.platforms.playwright.base_publisher import ArticlePayload, PublishResult
from ordo_engine.platforms.playwright.adapters import PlaywrightPlatformAdapter


class TestPlaywrightEngine(unittest.TestCase):
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
