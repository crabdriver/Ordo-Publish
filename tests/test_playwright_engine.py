from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from ordo_engine.platforms.playwright.engine import (
    PlaywrightEngine, BrowserProfileBusyError,
)
from ordo_engine.platforms.playwright.human import HumanBehavior
from ordo_engine.platforms.playwright.base_publisher import (
    ArticlePayload,
    PlaywrightBasePublisher,
    PublishResult,
)
from ordo_engine.platforms.playwright.adapters import PlaywrightPlatformAdapter
from ordo_engine.platforms.playwright_bilibili.publisher import BilibiliPlaywrightPublisher
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
    @patch("ordo_engine.platforms.playwright.engine.subprocess.run")
    def test_find_profile_processes_parses_parent_pid_and_full_start_time(self, run):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PlaywrightEngine(base_dir=Path(tmpdir), headless=True)
            profile = str(engine.profile_dir.resolve())
            run.return_value = MagicMock(
                returncode=0,
                stdout=(
                    "  100    50 Tue Jul 15 11:29:28 2026 "
                    f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                    f"--user-data-dir={profile}\n"
                ),
            )

            processes = engine._find_profile_processes()

            self.assertEqual(processes, [{
                "pid": 100,
                "ppid": 50,
                "start_time": "Tue Jul 15 11:29:28 2026",
                "args": (
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                    f"--user-data-dir={profile}"
                ),
            }])

    def test_capture_ownership_treats_chrome_process_tree_as_one_browser(self):
        """Chrome 根进程及其 helper 子进程属于同一个 owned browser。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PlaywrightEngine(base_dir=Path(tmpdir), headless=True)
            processes = [
                {"pid": 100, "ppid": 50, "start_time": "root-start", "args": "chrome"},
                {"pid": 101, "ppid": 100, "start_time": "child-start", "args": "renderer"},
                {"pid": 102, "ppid": 100, "start_time": "child-start", "args": "gpu"},
                {"pid": 103, "ppid": 101, "start_time": "child-start", "args": "utility"},
            ]

            with patch.object(engine, "_find_profile_processes", return_value=processes):
                engine._capture_ownership()

            self.assertEqual(engine._owned_pid, 100)
            self.assertEqual(engine._owned_start_time, "root-start")

    def test_cleanup_stale_profile_lock_keeps_live_pid(self):
        """存活进程使用 profile → BrowserProfileBusyError。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PlaywrightEngine(base_dir=Path(tmpdir), headless=True)
            engine.profile_dir.mkdir(parents=True)
            lock = engine.profile_dir / "SingletonLock"
            lock.symlink_to("host-4242")

            with patch.object(engine, "_find_profile_processes",
                              return_value=[{"pid": 4242, "start_time": "now", "args": "chrome"}]):
                with self.assertRaises(BrowserProfileBusyError):
                    engine._cleanup_stale_lock()

            self.assertTrue(lock.is_symlink(), "存活进程时不应删除锁")

    def test_cleanup_stale_profile_lock_removes_dead_pid(self):
        """无进程使用 profile → 安全删除孤儿锁。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PlaywrightEngine(base_dir=Path(tmpdir), headless=True)
            engine.profile_dir.mkdir(parents=True)
            lock = engine.profile_dir / "SingletonLock"
            lock.symlink_to("host-4242")
            (engine.profile_dir / "SingletonCookie").write_text("x", encoding="utf-8")

            with patch.object(engine, "_find_profile_processes", return_value=[]):
                engine._cleanup_stale_lock()

            self.assertFalse(lock.is_symlink())
            self.assertFalse((engine.profile_dir / "SingletonCookie").exists())

    def test_cleanup_stale_profile_lock_fails_closed_when_owner_unknown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PlaywrightEngine(base_dir=Path(tmpdir), headless=True)
            engine.profile_dir.mkdir(parents=True)
            lock = engine.profile_dir / "SingletonLock"
            lock.write_text("unknown", encoding="utf-8")

            engine._cleanup_stale_lock()

            self.assertTrue(lock.exists())

    def test_profile_rejects_ordo_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside:
            base_dir = Path(tmpdir)
            (base_dir / ".ordo").symlink_to(Path(outside), target_is_directory=True)
            engine = PlaywrightEngine(base_dir=base_dir, headless=False)

            with self.assertRaisesRegex(RuntimeError, "symlink"):
                engine.mark_profile_initialized()

            self.assertFalse((Path(outside) / "automation-profile" / ".ordo-profile-initialized").exists())

    def test_profile_rejects_profile_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside:
            base_dir = Path(tmpdir)
            (base_dir / ".ordo").mkdir()
            (base_dir / ".ordo" / "automation-profile").symlink_to(
                Path(outside), target_is_directory=True
            )
            engine = PlaywrightEngine(base_dir=base_dir, headless=False)

            with self.assertRaisesRegex(RuntimeError, "symlink"):
                engine.mark_profile_initialized()

            self.assertFalse((Path(outside) / ".ordo-profile-initialized").exists())

    def test_profile_rejects_marker_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.NamedTemporaryFile() as outside:
            base_dir = Path(tmpdir)
            profile = base_dir / ".ordo" / "automation-profile"
            profile.mkdir(parents=True)
            (profile / ".ordo-profile-initialized").symlink_to(Path(outside.name))
            engine = PlaywrightEngine(base_dir=base_dir, headless=False)

            with self.assertRaisesRegex(RuntimeError, "symlink"):
                _ = engine.profile_is_initialized

    def test_new_platform_page_is_leased_without_implicit_navigation(self):
        engine = PlaywrightEngine(base_dir=Path("/tmp/repo"), headless=False)
        page = MagicMock(url="about:blank")
        context = MagicMock(pages=[])
        context.new_page.return_value = page
        engine._context = context

        self.assertIs(engine.get_page_for_platform("zhihu"), page)
        page.goto.assert_not_called()

        released = engine.release_page_for_platform("zhihu")
        self.assertIs(released, page)
        page.close.assert_called_once_with()

    def test_reused_platform_page_is_released_after_later_failure(self):
        engine = PlaywrightEngine(base_dir=Path("/tmp/repo"), headless=False)
        page = MagicMock(url="https://zhuanlan.zhihu.com/write")
        engine._context = MagicMock(pages=[page])

        self.assertIs(engine.get_page_for_platform("zhihu"), page)
        released = engine.release_page_for_platform("zhihu")

        self.assertIs(released, page)
        page.close.assert_called_once_with()

    def test_release_keeps_lease_and_raises_when_page_close_fails(self):
        engine = PlaywrightEngine(base_dir=Path("/tmp/repo"), headless=False)
        page = MagicMock()
        page.close.side_effect = RuntimeError("page close failed")
        engine._platform_pages["zhihu"] = page

        with self.assertRaisesRegex(RuntimeError, "page close failed"):
            engine.release_page_for_platform("zhihu")

        self.assertIs(engine._platform_pages["zhihu"], page)

    def test_engine_close_attempts_pages_context_and_driver_then_raises_first_error(self):
        engine = PlaywrightEngine(base_dir=Path("/tmp/repo"), headless=False)
        bad_page = MagicMock()
        bad_page.close.side_effect = RuntimeError("page close failed")
        good_page = MagicMock()
        context = MagicMock()
        context.close.side_effect = RuntimeError("context close failed")
        driver = MagicMock()
        driver.stop.side_effect = RuntimeError("driver stop failed")
        engine._platform_pages = {"zhihu": bad_page, "jianshu": good_page}
        engine._context = context
        engine._playwright = driver

        with self.assertRaisesRegex(RuntimeError, "page close failed"):
            engine.close()

        bad_page.close.assert_called_once_with()
        good_page.close.assert_called_once_with()
        context.close.assert_called_once_with()
        driver.stop.assert_called_once_with()
        self.assertIn("zhihu", engine._platform_pages)
        self.assertNotIn("jianshu", engine._platform_pages)
        self.assertIs(engine._context, context)
        self.assertIs(engine._playwright, driver)

    def test_headless_login_failure_does_not_sleep_screenshot_or_poll(self):
        engine = MagicMock(headless=True)
        page = MagicMock(url="https://example.test/login")
        page.locator.return_value.count.return_value = 0
        page.evaluate.return_value = "请登录"
        publisher = StubPublisher(engine)

        with patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "--bootstrap-browser"):
                publisher._wait_for_login_if_needed(
                    page, "editor", "#title", "测试平台"
                )

        sleep.assert_not_called()
        engine.screenshot.assert_not_called()

    def test_bilibili_headless_login_failure_does_not_wait(self):
        engine = MagicMock(headless=True)
        page = MagicMock(url="https://member.bilibili.com/login")
        page.frames = []
        page.evaluate.return_value = "扫码登录"
        engine.get_page_for_platform.return_value = page
        publisher = BilibiliPlaywrightPublisher(engine)

        with patch("ordo_engine.platforms.playwright_bilibili.publisher.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "--bootstrap-browser"):
                publisher.navigate_to_editor()

        sleep.assert_not_called()
        engine.screenshot.assert_not_called()

    def test_headless_missing_initialized_profile_fails_before_browser_start(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "ordo_engine.platforms.playwright.engine.sync_playwright"
        ) as mock_sync_playwright:
            engine = PlaywrightEngine(base_dir=Path(tmpdir), headless=True)

            with self.assertRaisesRegex(RuntimeError, "--bootstrap-browser"):
                engine.connect()

        mock_sync_playwright.assert_not_called()

    def test_profile_requires_explicit_initialization_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PlaywrightEngine(base_dir=Path(tmpdir), headless=True)
            engine.profile_dir.mkdir(parents=True)
            (engine.profile_dir / "some-chrome-file").write_text("x", encoding="utf-8")

            self.assertFalse(engine.profile_is_initialized)

    def test_state_persistence_failure_stops_before_submit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            article_path = base_dir / "article.md"
            article_path.write_text("# Article", encoding="utf-8")
            publisher = StubPublisher(MagicMock(base_dir=base_dir))
            publisher.click_publish = MagicMock()
            publisher.save_draft = MagicMock()

            def persist_step(_identity, _platform, _mode, step, **_kwargs):
                if step == "settings_configured":
                    raise OSError("disk full")

            with patch(
                "ordo_engine.platforms.playwright.base_publisher.record_step",
                side_effect=persist_step,
            ):
                result = publisher.publish(
                    ArticlePayload(title="Article", body="Body", markdown_path=article_path),
                    mode="publish",
                )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "disk full")
        publisher.click_publish.assert_not_called()
        publisher.save_draft.assert_not_called()

    def test_post_submit_exception_rechecks_and_reports_draft_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            article_path = base_dir / "article.md"
            article_path.write_text("# Article", encoding="utf-8")

            class DraftAfterSubmitFailurePublisher(StubPublisher):
                def click_publish(self):
                    raise RuntimeError("publish dialog closed unexpectedly")

                def verify_result(self, _mode):
                    return PublishResult(
                        platform=self.platform,
                        status="draft_only",
                        page_state="draft_saved",
                    )

            engine = MagicMock(base_dir=base_dir)
            engine.screenshot.return_value = None
            result = DraftAfterSubmitFailurePublisher(engine).publish(
                ArticlePayload(title="Article", body="Body", markdown_path=article_path),
                mode="publish",
            )

        self.assertEqual(result.status, "draft_only")
        self.assertEqual(result.page_state, "draft_saved")

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
        self.assertEqual(engine.base_dir, Path("/tmp").resolve())
        self.assertIsNone(engine._browser)

    @patch("ordo_engine.platforms.playwright.engine.sync_playwright")
    def test_engine_connect(self, mock_sync_playwright):
        mock_p = MagicMock()
        mock_sync_playwright.return_value.start.return_value = mock_p

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = PlaywrightEngine(debug_port=9999, base_dir=Path(tmpdir))
            engine.mark_profile_initialized()
            with patch.object(engine, "_capture_ownership"):
                engine.connect()

            mock_p.chromium.launch_persistent_context.assert_called_once()
            launch_kwargs = mock_p.chromium.launch_persistent_context.call_args.kwargs
            self.assertEqual(launch_kwargs["user_data_dir"], str(engine.profile_dir))
            self.assertTrue(launch_kwargs["headless"])
            self.assertIs(engine._context, mock_p.chromium.launch_persistent_context.return_value)
            self.assertIsNone(engine._browser)

            engine.close()
            self.assertIsNone(engine._context)
            mock_p.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
