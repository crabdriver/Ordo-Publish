import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from PIL import Image, ImageCms

import publish
from ordo_engine.run_lock import run_lock


class PublishPreflightTests(unittest.TestCase):
    @staticmethod
    def _draw_cover_detail(image):
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        for x in range(120, image.width - 120, 120):
            draw.line((x, 100, x, image.height - 100), fill=(220, 180, 100), width=8)
        for y in range(100, image.height - 100, 100):
            draw.line((120, y, image.width - 120, y), fill=(120, 180, 220), width=8)
        return image

    def _initialize_browser_profile(self, base: Path):
        profile = base / ".ordo" / "automation-profile"
        profile.mkdir(parents=True, exist_ok=True)
        (profile / ".ordo-profile-initialized").write_text(
            "ordo-automation-profile-v1\n", encoding="utf-8"
        )

    def _write_cover(self, path: Path, size=(1280, 720)):
        Image.new("RGB", size, color=(23, 45, 67)).save(path)

    def _write_canonical_cover(self, path: Path):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self._draw_cover_detail(Image.new("RGB", (2538, 1080), color=(23, 45, 67))).save(
            path,
            format="PNG",
            icc_profile=profile,
        )

    def _write_publication_package(self, root: Path, *, cover_size=(2538, 1080)):
        relative = "assets/article-1/cover.png"
        cover = root / relative
        cover.parent.mkdir(parents=True)
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self._draw_cover_detail(Image.new("RGB", cover_size, color=(23, 45, 67))).save(
            cover,
            format="PNG",
            icc_profile=profile,
        )
        article = root / "article.md"
        platform_lines = "".join(
            f"  {platform}: {relative}\n"
            for platform in ("wechat", "zhihu", "toutiao", "yidian", "bilibili", "jianshu")
        )
        article.write_text(
            "---\narticle_id: article-1\n"
            f"cover: {relative}\nplatform_covers:\n{platform_lines}---\n\n# Article\n",
            encoding="utf-8",
        )
        return article, cover

    def test_publication_cover_assignment_reuses_one_cover_for_all_platforms(self):
        with tempfile.TemporaryDirectory() as tmp:
            article, cover = self._write_publication_package(Path(tmp))

            assignments = publish.build_publication_cover_assignments(
                [article],
                ["wechat", "zhihu", "toutiao", "yidian", "bilibili", "jianshu"],
            )

        self.assertEqual(len(assignments), 6)
        self.assertEqual({item.cover_path for item in assignments}, {cover.resolve()})

    def test_publication_cover_assignment_rejects_invalid_manual_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article, _cover = self._write_publication_package(root)
            invalid = root / "cover.png"
            self._write_cover(invalid, size=(1200, 510))

            with self.assertRaisesRegex(ValueError, "2538x1080"):
                publish.build_publication_cover_assignments(
                    [article],
                    ["wechat", "zhihu"],
                    cover_override=invalid,
                )

    def test_preflight_blocks_invalid_publication_package_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            article, _cover = self._write_publication_package(Path(tmp), cover_size=(1200, 510))

            blockers, _warnings = publish.run_preflight_checks(
                platforms=[],
                mode="publish",
                workbench={},
                base_dir=Path(tmp),
                article_paths=[article],
            )

        self.assertTrue(any("2538x1080" in item for item in blockers), blockers)

    def test_preflight_does_not_require_legacy_pool_for_valid_publication_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article, _cover = self._write_publication_package(root)
            with patch.object(
                publish,
                "inspect_browser_platform_state",
                return_value={
                    "editor_ready": True,
                    "page_state": "editor_ready",
                    "current_url": "https://zhuanlan.zhihu.com/write",
                    "detail": "编辑器已就绪",
                },
            ):
                blockers, _warnings = publish.run_preflight_checks(
                    platforms=["zhihu"],
                    mode="publish",
                    workbench={"zhihu": "target-1"},
                    base_dir=root,
                    article_paths=[article],
                )

        self.assertFalse(any("封面池" in item for item in blockers), blockers)

    def test_wechat_preflight_does_not_require_legacy_pool_for_valid_publication_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article, _cover = self._write_publication_package(root)
            with patch.object(
                publish,
                "get_wechat_config_status",
                return_value={
                    "appid_ready": True,
                    "secret_ready": True,
                    "covers_ready": False,
                    "ai_cover_ready": False,
                },
            ), patch.object(publish.subprocess, "run") as run:
                blockers, _warnings = publish.run_preflight_checks(
                    platforms=["wechat"],
                    mode="publish",
                    workbench={},
                    base_dir=root,
                    article_paths=[article],
                )

        self.assertFalse(any("缺少可用封面" in item for item in blockers), blockers)
        run.assert_not_called()

    def test_publish_mode_defaults_to_local_remote(self):
        args = publish.parse_args(["article.md", "--mode", "publish"])
        self.assertEqual(args.remote, "local")
        self.assertFalse(args.assume_yes)

    def test_explicit_vps_remote_remains_available_for_manual_emergency_use(self):
        args = publish.parse_args(["article.md", "--mode", "publish", "--remote", "vps"])
        self.assertEqual(args.remote, "vps")

    def test_force_republish_is_explicit_and_defaults_off(self):
        self.assertFalse(publish.parse_args(["article.md"]).force_republish)
        self.assertTrue(publish.parse_args(["article.md", "--force-republish"]).force_republish)

    def test_draft_mode_defaults_to_local_remote(self):
        args = publish.parse_args(["article.md", "--mode", "draft"])
        self.assertEqual(args.remote, "local")

    def test_bootstrap_browser_uses_only_repo_profile_and_marks_after_confirmation(self):
        class FakeEngine:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.profile_dir = kwargs["base_dir"] / ".ordo" / "automation-profile"
                self.opened = []
                self.closed = False

            def connect(self):
                pass

            def get_page_for_platform(self, platform):
                self.opened.append(platform)

            def close(self):
                self.closed = True

            def mark_profile_initialized(self):
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                (self.profile_dir / ".ordo-profile-initialized").write_text(
                    "ordo-automation-profile-v1\n", encoding="utf-8"
                )

        made = []

        def factory(**kwargs):
            engine = FakeEngine(**kwargs)
            made.append(engine)
            return engine

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            publish.bootstrap_browser_profile(
                base_dir,
                ["wechat", "zhihu", "jianshu"],
                engine_factory=factory,
                input_fn=lambda _prompt: "YES",
            )
            marker = base_dir / ".ordo" / "automation-profile" / ".ordo-profile-initialized"
            self.assertTrue(marker.is_file())

        self.assertEqual(made[0].kwargs, {"mode": "standalone", "headless": False, "base_dir": base_dir})
        self.assertEqual(made[0].opened, ["zhihu", "jianshu"])
        self.assertTrue(made[0].closed)

    def test_bootstrap_failure_does_not_write_initialized_marker(self):
        class FailingEngine:
            def __init__(self, **kwargs):
                self.profile_dir = kwargs["base_dir"] / ".ordo" / "automation-profile"

            def connect(self):
                raise RuntimeError("boom")

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            with self.assertRaisesRegex(RuntimeError, "boom"):
                publish.bootstrap_browser_profile(
                    base_dir,
                    ["zhihu"],
                    engine_factory=FailingEngine,
                    input_fn=lambda _prompt: "YES",
                )

            marker = base_dir / ".ordo" / "automation-profile" / ".ordo-profile-initialized"
            self.assertFalse(marker.exists())

    def test_bootstrap_close_failure_does_not_mark_profile(self):
        class Engine:
            def __init__(self, **_kwargs):
                self.mark_profile_initialized = MagicMock()

            def connect(self):
                pass

            def get_page_for_platform(self, _platform):
                pass

            def close(self):
                raise RuntimeError("context close failed")

        engine = Engine()
        with self.assertRaisesRegex(RuntimeError, "context close failed"):
            publish.bootstrap_browser_profile(
                Path("/tmp/repo"),
                ["zhihu"],
                engine_factory=lambda **_kwargs: engine,
                input_fn=lambda _prompt: "YES",
            )

        engine.mark_profile_initialized.assert_not_called()

    def test_local_playwright_engine_is_rejected_before_legacy_browser_access(self):
        legacy_names = (
            "ensure_chrome_ready",
            "list_tabs_or_none",
            "open_missing_platform_tabs",
            "launch_chrome",
        )
        patches = [patch.object(publish, name) for name in legacy_names]
        mocks = [item.start() for item in patches]
        self.addCleanup(lambda: [item.stop() for item in patches])

        with patch.object(
            publish.sys,
            "argv",
            ["publish.py", "missing.md", "--remote", "local", "--engine", "playwright"],
        ):
            with self.assertRaisesRegex(SystemExit, "standalone"):
                publish.main()

        for mock in mocks:
            mock.assert_not_called()

    def test_local_config_playwright_is_rejected_before_legacy_browser_access(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "config.json").write_text(
                '{"engine": "playwright"}', encoding="utf-8"
            )
            with patch.object(publish, "BASE_DIR", base_dir), patch.object(
                publish.sys,
                "argv",
                ["publish.py", "missing.md", "--remote", "local"],
            ), patch.object(publish, "ensure_chrome_ready") as ensure, patch.object(
                publish, "list_tabs_or_none"
            ) as list_tabs, patch.object(
                publish, "open_missing_platform_tabs"
            ) as open_tabs:
                with self.assertRaisesRegex(SystemExit, "standalone"):
                    publish.main()

            ensure.assert_not_called()
            list_tabs.assert_not_called()
            open_tabs.assert_not_called()

    def test_wechat_only_does_not_consult_legacy_browser_engine_config(self):
        args = publish.parse_args(
            ["article.md", "--remote", "local", "--platform", "wechat"]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "config.json").write_text(
                '{"engine": "playwright"}', encoding="utf-8"
            )

            publish.require_local_standalone_engine(args, base_dir)

    def test_parse_bootstrap_browser_does_not_require_markdown_path(self):
        args = publish.parse_args(["--bootstrap-browser", "--platform", "zhihu"])
        self.assertTrue(args.bootstrap_browser)
        self.assertIsNone(args.markdown_path)

    def test_main_rejects_overlap_before_bootstrap_probe_or_publish(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / ".ordo" / "publish.lock"
            with run_lock(lock_path), patch.object(
                publish, "PUBLISH_LOCK_FILE", lock_path, create=True
            ), patch.object(
                publish.sys, "argv", ["publish.py", "--bootstrap-browser", "--platform", "zhihu"]
            ), patch.object(publish, "bootstrap_browser_profile") as bootstrap:
                with self.assertRaisesRegex(SystemExit, "已有发表任务"):
                    publish.main()
            bootstrap.assert_not_called()

    def test_main_rejects_fake_inherited_lock_fd(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            lock_path = root / ".ordo" / "publish.lock"
            other = root / "other.lock"
            other.touch()
            with other.open("r") as handle, patch.object(
                publish, "PUBLISH_LOCK_FILE", lock_path, create=True
            ), patch.dict(
                "os.environ", {"ORDO_PUBLISH_LOCK_FD": str(handle.fileno())}, clear=False
            ), patch.object(
                publish.sys, "argv", ["publish.py", "--bootstrap-browser", "--platform", "zhihu"]
            ), patch.object(publish, "bootstrap_browser_profile") as bootstrap:
                with self.assertRaisesRegex(SystemExit, "继承发布锁无效"):
                    publish.main()
            bootstrap.assert_not_called()

    def test_main_accepts_valid_inherited_lock_for_monitor_child(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / ".ordo" / "publish.lock"
            with run_lock(lock_path) as lock_fd, patch.object(
                publish, "PUBLISH_LOCK_FILE", lock_path, create=True
            ), patch.dict(
                "os.environ", {"ORDO_PUBLISH_LOCK_FD": str(lock_fd)}, clear=False
            ), patch.object(
                publish.sys, "argv", ["publish.py", "--bootstrap-browser", "--platform", "zhihu"]
            ), patch.object(publish, "bootstrap_browser_profile") as bootstrap:
                publish.main()
            bootstrap.assert_called_once()

    def test_get_cdp_runtime_env_uses_managed_browser_session_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.json").write_text(
                """
{
  "browser_session": {
    "enabled": true,
    "debug_port": 9555,
    "profile_dir": ".ordo-managed-profile"
  }
}
""".strip(),
                encoding="utf-8",
            )

            env = publish.get_cdp_runtime_env(base_dir=base, environ={"PATH": "/usr/bin"})

        self.assertEqual(env["LIVE_CDP_PORT"], "9555")
        self.assertTrue(env["ORDO_BROWSER_SESSION_PROFILE_DIR"].endswith(".ordo-managed-profile"))

    def test_run_remote_cdp_preflight_blocks_when_remote_browser_unavailable(self):
        class Executor:
            def execute(self, _cmd, timeout=None):
                return {"returncode": 1, "stdout": "", "stderr": "connect ECONNREFUSED"}

        with self.assertRaisesRegex(RuntimeError, "VPS 浏览器/CDP 预检失败"):
            publish.run_remote_cdp_preflight(Executor())

    def test_run_remote_cdp_preflight_passes_when_remote_browser_lists(self):
        class Executor:
            def execute(self, _cmd, timeout=None):
                return {"returncode": 0, "stdout": "target\\tTitle\\turl", "stderr": ""}

        res = publish.run_remote_cdp_preflight(Executor())

        self.assertEqual(res["returncode"], 0)

    def test_run_preflight_checks_ignores_cdp_connection_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._initialize_browser_profile(base)
            with patch.object(
                publish,
                "inspect_browser_platform_state",
                side_effect=AssertionError("CDP inspection called"),
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["zhihu"],
                    mode="draft",
                    workbench={"zhihu": "target-1"},
                    base_dir=base,
                    cover_mode="force_off",
                    cdp_connection={"detail": "legacy CDP"},
                )

        self.assertEqual(blockers, [])
        self.assertFalse(any("CDP" in item for item in warnings))

    def test_run_preflight_checks_warns_when_config_json_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.json").write_text("{broken", encoding="utf-8")
            covers = base / "covers"
            covers.mkdir()
            self._write_cover(covers / "cover_1.png")
            self._initialize_browser_profile(base)

            blockers, warnings = publish.run_preflight_checks(
                platforms=["zhihu"],
                mode="draft",
                workbench={"zhihu": "target-1"},
                base_dir=base,
            )

        self.assertEqual(blockers, [])
        self.assertTrue(any("config.json" in item for item in warnings))

    def test_run_preflight_checks_blocks_when_wechat_credentials_missing(self):
        with patch.object(
            publish,
            "get_wechat_config_status",
            return_value={
                "appid_ready": False,
                "secret_ready": False,
                "covers_ready": True,
                "ai_cover_ready": False,
            },
        ):
            blockers, warnings = publish.run_preflight_checks(
                platforms=["wechat"],
                mode="draft",
                workbench={},
            )

        self.assertEqual(warnings, [])
        self.assertTrue(any("WECHAT_APPID" in item for item in blockers))

    @patch("wechat_publisher.WeChatPublisher")
    def test_run_preflight_checks_warns_when_ai_cover_ready_but_local_cover_missing(self, mock_wechat_publisher):
        mock_wechat_publisher.return_value.ensure_access_token.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "secrets.env").write_text("VPS_IP=203.0.113.10\n", encoding="utf-8")
            with patch.object(
                publish,
                "get_wechat_config_status",
                return_value={
                    "appid_ready": True,
                    "secret_ready": True,
                    "covers_ready": False,
                    "ai_cover_ready": True,
                },
            ), patch.object(
                publish.subprocess,
                "run",
                return_value=CompletedProcess(["ssh"], 0, stdout="", stderr=""),
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["wechat"],
                    mode="draft",
                    workbench={},
                    base_dir=base,
                )

        self.assertEqual(blockers, [])
        self.assertTrue(any("AI 封面" in item for item in warnings))

    @patch("wechat_publisher.WeChatPublisher")
    def test_run_preflight_checks_respects_cover_override_for_wechat(self, mock_wechat_publisher):
        mock_wechat_publisher.return_value.ensure_access_token.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "secrets.env").write_text("VPS_IP=203.0.113.10\n", encoding="utf-8")
            override_dir = base / "external-covers"
            override_dir.mkdir()
            self._write_cover(override_dir / "cover_01.png")
            with patch.object(
                publish,
                "get_wechat_config_status",
                return_value={
                    "appid_ready": True,
                    "secret_ready": True,
                    "covers_ready": False,
                    "ai_cover_ready": False,
                },
            ), patch.object(
                publish.subprocess,
                "run",
                return_value=CompletedProcess(["ssh"], 0, stdout="", stderr=""),
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["wechat"],
                    mode="draft",
                    workbench={},
                    base_dir=base,
                    cover_dir_override=override_dir,
                )

        self.assertEqual(warnings, [])
        self.assertEqual(blockers, [])

    def test_run_preflight_checks_allows_local_wechat_without_vps_ip_or_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "covers").mkdir()
            self._write_cover(base / "covers" / "cover_01.png")
            with patch.object(
                publish,
                "get_wechat_config_status",
                return_value={
                    "appid_ready": True,
                    "secret_ready": True,
                    "covers_ready": True,
                    "ai_cover_ready": False,
                },
            ), patch.object(publish.subprocess, "run") as run:
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["wechat"],
                    mode="publish",
                    workbench={},
                    base_dir=base,
                )

        self.assertEqual(warnings, [])
        self.assertEqual(blockers, [])
        run.assert_not_called()

    def test_run_preflight_checks_does_not_require_browser_tabs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._initialize_browser_profile(base)
            blockers, _warnings = publish.run_preflight_checks(
                platforms=["zhihu", "toutiao"],
                mode="draft",
                workbench={"zhihu": "target-1"},
                base_dir=base,
                cover_mode="force_off",
            )

        self.assertEqual(blockers, [])

    def test_standalone_preflight_only_reads_profile_marker(self):
        forbidden = (
            "inspect_browser_platform_state",
            "get_page_text_snippet",
            "ensure_chrome_ready",
            "open_missing_platform_tabs",
            "list_tabs",
            "list_tabs_or_none",
            "launch_chrome",
            "run_cdp",
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

        with tempfile.TemporaryDirectory() as tmpdir:
            blockers, _warnings = publish.run_preflight_checks(
                platforms=["zhihu"],
                mode="publish",
                workbench={},
                base_dir=Path(tmpdir),
                cover_mode="force_off",
            )

        self.assertTrue(any("--bootstrap-browser" in item for item in blockers), blockers)
        for mock in mocks:
            mock.assert_not_called()

    def test_run_preflight_checks_does_not_probe_jianshu_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._initialize_browser_profile(base)
            with patch.object(
                publish,
                "get_page_text_snippet",
                side_effect=AssertionError("CDP page text called"),
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["jianshu"],
                    mode="publish",
                    workbench={"jianshu": "note-1"},
                    base_dir=base,
                    cover_mode="force_off",
                )

        self.assertEqual(blockers, [])
        self.assertEqual(warnings, [])

    def test_preflight_blocks_publish_when_non_wechat_cover_pool_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._initialize_browser_profile(base)
            missing_dir = base / "no_covers_here"
            with patch.object(
                publish,
                "get_wechat_config_status",
                return_value={
                    "appid_ready": True,
                    "secret_ready": True,
                    "covers_ready": True,
                    "ai_cover_ready": False,
                },
            ), patch.object(
                publish,
                "inspect_browser_platform_state",
                return_value={
                    "editor_ready": True,
                    "page_state": "editor_ready",
                    "current_url": "https://zhuanlan.zhihu.com/write",
                    "detail": "写作编辑器已就绪",
                },
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["zhihu"],
                    mode="publish",
                    workbench={"zhihu": "t-1"},
                    base_dir=base,
                    cover_dir_override=missing_dir,
                )
        self.assertEqual(warnings, [])
        self.assertTrue(
            any("封面" in b and "zhihu" in b for b in blockers),
            msg=f"expected cover pool blocker, got {blockers!r}",
        )

    def test_preflight_skips_cover_pool_warning_when_cover_mode_force_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._initialize_browser_profile(base)
            missing_dir = base / "no_covers_here"
            with patch.object(
                publish,
                "inspect_browser_platform_state",
                return_value={
                    "editor_ready": True,
                    "page_state": "editor_ready",
                    "current_url": "https://example.com/write",
                    "detail": "写作编辑器已就绪",
                },
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["toutiao", "yidian"],
                    mode="draft",
                    workbench={"toutiao": "t-1", "yidian": "y-1"},
                    base_dir=base,
                    cover_dir_override=missing_dir,
                    cover_mode="force_off",
                )
        self.assertEqual(blockers, [])
        self.assertEqual(warnings, [])

    def test_preflight_blocks_when_cover_mode_force_on_and_pool_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            missing_dir = base / "no_covers_here"
            with patch.object(
                publish,
                "inspect_browser_platform_state",
                return_value={
                    "editor_ready": True,
                    "page_state": "editor_ready",
                    "current_url": "https://example.com/write",
                    "detail": "写作编辑器已就绪",
                },
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["zhihu"],
                    mode="draft",
                    workbench={"zhihu": "t-1"},
                    base_dir=base,
                    cover_dir_override=missing_dir,
                    cover_mode="force_on",
                )
        self.assertEqual(warnings, [])
        self.assertTrue(any("封面" in item and "已明确要求启用" in item for item in blockers), blockers)

    def test_preflight_warns_draft_when_non_wechat_cover_pool_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._initialize_browser_profile(base)
            empty_covers = base / "covers"
            empty_covers.mkdir()
            with patch.object(
                publish,
                "get_wechat_config_status",
                return_value={
                    "appid_ready": True,
                    "secret_ready": True,
                    "covers_ready": True,
                    "ai_cover_ready": False,
                },
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["toutiao", "yidian"],
                    mode="draft",
                    workbench={"toutiao": "t-1", "yidian": "y-1"},
                    base_dir=base,
                    cover_dir_override=empty_covers,
                )
        self.assertEqual(blockers, [])
        self.assertTrue(
            any("封面" in w for w in warnings),
            msg=f"expected cover pool warning, got {warnings!r}",
        )

    def test_append_publish_record_includes_gui_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "publish_records.csv"
            with patch.object(publish, "PUBLISH_RECORDS_FILE", rec):
                publish.append_publish_record(
                    {
                        "article": "/a/b/post.md",
                        "platform": "zhihu",
                        "mode": "draft",
                        "status": "draft_only",
                        "returncode": 0,
                        "stdout": "",
                        "stderr": "",
                        "article_id": "0000-post",
                        "theme_name": "midnight",
                        "template_mode": "rich",
                        "cover_path": str(Path(tmp) / "c.png"),
                        "error_type": None,
                        "current_url": "https://mp.toutiao.com/profile_v4/graphic/publish",
                        "page_state": "editor_ready",
                        "smoke_step": "inject_article",
                    }
                )
            lines = rec.read_text(encoding="utf-8").splitlines()
            header = lines[0]
            self.assertIn("article_id", header)
            self.assertIn("theme_name", header)
            self.assertIn("cover_path", header)
            self.assertIn("error_type", header)
            self.assertIn("current_url", header)
            self.assertIn("page_state", header)
            self.assertIn("smoke_step", header)
            with rec.open(encoding="utf-8", newline="") as fp:
                rows = list(csv.DictReader(fp))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["article_id"], "0000-post")
            self.assertEqual(rows[0]["theme_name"], "midnight")
            self.assertEqual(rows[0]["template_mode"], "rich")
            self.assertTrue(rows[0]["cover_path"].endswith("c.png"))
            self.assertEqual(rows[0]["error_type"], "")
            self.assertEqual(rows[0]["current_url"], "https://mp.toutiao.com/profile_v4/graphic/publish")
            self.assertEqual(rows[0]["page_state"], "editor_ready")
            self.assertEqual(rows[0]["smoke_step"], "inject_article")

    def test_append_publish_record_migrates_csv_with_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "publish_records.csv"
            rec.write_text(
                "timestamp,article,platform,mode,status,returncode,stdout,stderr\n"
                "2026-03-27 12:00:00,/a/b/post.md,zhihu,draft,draft_only,0,ok,\n",
                encoding="utf-8",
            )
            with patch.object(publish, "PUBLISH_RECORDS_FILE", rec):
                publish.append_publish_record(
                    {
                        "article": "/a/b/new.md",
                        "platform": "wechat",
                        "mode": "draft",
                        "status": "draft_only",
                        "returncode": 0,
                        "stdout": "",
                        "stderr": "",
                        "article_id": "new-1",
                        "theme_name": "",
                        "template_mode": "default",
                        "cover_path": "",
                        "error_type": None,
                    }
                )
            backup = rec.with_name("publish_records.csv.bak")
            self.assertTrue(backup.exists())
            with rec.open(encoding="utf-8", newline="") as fp:
                rows = list(csv.DictReader(fp))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["platform"], "zhihu")
            self.assertEqual(rows[1]["article_id"], "new-1")

    def test_append_publish_record_blocks_on_corrupt_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "publish_records.csv"
            rec.write_bytes(b"\xff\xfe\x00broken")
            with patch.object(publish, "PUBLISH_RECORDS_FILE", rec):
                with self.assertRaisesRegex(RuntimeError, "损坏"):
                    publish.append_publish_record(
                        {
                            "article": "/a/b/new.md",
                            "platform": "wechat",
                            "mode": "draft",
                            "status": "draft_only",
                            "returncode": 0,
                            "stdout": "",
                            "stderr": "",
                            "article_id": "new-1",
                            "theme_name": "",
                            "template_mode": "default",
                            "cover_path": "",
                            "error_type": None,
                        }
                    )
            self.assertEqual(rec.read_bytes(), b"\xff\xfe\x00broken")

    def test_print_result_emits_meta_json_line(self):
        out = []

        def fake_print(*args, **_kwargs):
            out.append(args[0] if args else "")

        with patch("builtins.print", fake_print):
            publish.print_result(
                {
                    "platform": "toutiao",
                    "stdout": "",
                    "stderr": "",
                    "returncode": 0,
                    "article_id": "x",
                    "theme_name": "t1",
                    "template_mode": "plain",
                    "cover_path": "/tmp/z.png",
                    "status": "draft_only",
                    "error_type": None,
                    "current_url": "https://mp.toutiao.com/profile_v4/graphic/publish",
                    "page_state": "editor_ready",
                    "smoke_step": "draft_saved",
                }
            )
        meta_lines = [line for line in out if isinstance(line, str) and line.startswith("[META] ")]
        self.assertEqual(len(meta_lines), 1, msg=out)
        payload = json.loads(meta_lines[0].split("[META] ", 1)[1])
        self.assertEqual(payload["platform"], "toutiao")
        self.assertEqual(payload["article_id"], "x")
        self.assertEqual(payload["theme_name"], "t1")
        self.assertEqual(payload["template_mode"], "plain")
        self.assertTrue(payload["cover_path"].endswith(".png"))
        self.assertEqual(payload["status"], "draft_only")
        self.assertIsNone(payload["error_type"])
        self.assertEqual(payload["current_url"], "https://mp.toutiao.com/profile_v4/graphic/publish")
        self.assertEqual(payload["page_state"], "editor_ready")
        self.assertEqual(payload["smoke_step"], "draft_saved")

    def test_append_publish_record_truncates_long_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "publish_records.csv"
            very_long_output = "x" * 6000
            with patch.object(publish, "PUBLISH_RECORDS_FILE", rec):
                publish.append_publish_record(
                    {
                        "article": "/a/b/post.md",
                        "platform": "zhihu",
                        "mode": "draft",
                        "status": "draft_only",
                        "returncode": 0,
                        "stdout": very_long_output,
                        "stderr": very_long_output,
                        "article_id": "0000-post",
                        "theme_name": "midnight",
                        "template_mode": "rich",
                        "cover_path": str(Path(tmp) / "c.png"),
                        "error_type": None,
                    }
                )
            with rec.open(encoding="utf-8", newline="") as fp:
                rows = list(csv.DictReader(fp))
            self.assertLess(len(rows[0]["stdout"]), 4500)
            self.assertIn("[truncated]", rows[0]["stdout"])


class ChromeLaunchTests(unittest.TestCase):
    def test_iter_chrome_launch_commands_includes_managed_profile_args(self):
        commands = publish.iter_chrome_launch_commands(
            ["https://example.com"],
            platform="darwin",
            browser_session={
                "enabled": True,
                "debug_port": 9333,
                "profile_dir": "/tmp/ordo-profile",
            },
        )

        self.assertGreaterEqual(len(commands), 1)
        self.assertEqual(commands[0][:3], ["open", "-na", "Google Chrome"])
        self.assertIn("--args", commands[0])
        self.assertIn("--remote-debugging-port=9333", commands[0])
        self.assertIn("--user-data-dir=/tmp/ordo-profile", commands[0])

    def test_iter_chrome_launch_commands_macos_managed_uses_new_instance(self):
        commands = publish.iter_chrome_launch_commands(
            ["https://example.com"],
            platform="darwin",
            browser_session={
                "enabled": True,
                "debug_port": 9333,
                "profile_dir": "/tmp/ordo-profile",
            },
        )

        self.assertGreaterEqual(len(commands), 1)
        self.assertEqual(commands[0][:3], ["open", "-na", "Google Chrome"])

    def test_iter_chrome_launch_commands_windows_uses_start_command(self):
        commands = publish.iter_chrome_launch_commands(["https://example.com"], platform="win32")

        self.assertGreaterEqual(len(commands), 1)
        self.assertEqual(
            commands[0],
            ["cmd", "/c", "start", "", "chrome", "https://example.com"],
        )

    def test_iter_chrome_launch_commands_macos_keeps_open_a_behavior(self):
        commands = publish.iter_chrome_launch_commands(["https://example.com"], platform="darwin")

        self.assertEqual(commands[0], ["open", "-a", "Google Chrome", "https://example.com"])

    def test_launch_chrome_retries_after_transient_failure(self):
        command = ["open", "-na", "Google Chrome", "https://example.com"]
        transient = subprocess.CalledProcessError(1, command, stderr="application is shutting down")

        with patch.object(
            publish,
            "load_browser_session_settings",
            return_value={"enabled": True, "debug_port": 9333, "profile_dir": "/tmp/ordo-profile"},
        ), patch.object(
            publish,
            "iter_chrome_launch_commands",
            return_value=[command],
        ), patch.object(
            publish.subprocess,
            "run",
            side_effect=[transient, CompletedProcess(command, 0, "", "")],
        ) as mocked_run, patch.object(
            publish.time, "sleep", return_value=None
        ):
            app_name = publish.launch_chrome(["https://example.com"])

        self.assertEqual(app_name, "Google Chrome")
        self.assertEqual(mocked_run.call_count, 2)

    def test_ensure_chrome_ready_blocks_when_current_source_is_system(self):
        existing_tabs = [{"target": "sys-1", "title": "知乎", "url": "https://zhuanlan.zhihu.com/write"}]

        with patch.object(
            publish,
            "load_browser_session_settings",
            return_value={"enabled": True, "debug_port": 9333, "profile_dir": "/tmp/ordo-profile"},
        ), patch.object(
            publish,
            "list_tabs_or_none",
            return_value=existing_tabs,
        ), patch.object(
            publish,
            "get_cdp_connection_metadata",
            return_value={"source": "macos_devtools_port_file", "detail": "system"},
        ), patch.object(
            publish,
            "launch_chrome",
            return_value="Google Chrome",
        ) as mocked_launch, patch.object(
            publish.time, "sleep", return_value=None
        ):
            with self.assertRaisesRegex(RuntimeError, "已阻止自动启动/回退到系统 Chrome"):
                publish.ensure_chrome_ready(["zhihu"])

        mocked_launch.assert_not_called()

    def test_ensure_chrome_ready_reuses_existing_tabs_when_managed_source_is_active(self):
        tabs = [{"target": "managed-1", "title": "知乎", "url": "https://zhuanlan.zhihu.com/write"}]

        with patch.object(
            publish,
            "load_browser_session_settings",
            return_value={"enabled": True, "debug_port": 9333, "profile_dir": "/tmp/ordo-profile"},
        ), patch.object(
            publish,
            "list_tabs_or_none",
            return_value=tabs,
        ), patch.object(
            publish,
            "get_cdp_connection_metadata",
            return_value={"source": "managed_browser_port", "detail": "managed"},
        ), patch.object(
            publish,
            "launch_chrome",
            return_value="Google Chrome",
        ) as mocked_launch:
            result_tabs, launched = publish.ensure_chrome_ready(["zhihu"])

        self.assertEqual(result_tabs, tabs)
        self.assertIsNone(launched)
        mocked_launch.assert_not_called()

    def test_open_missing_platform_tabs_prefers_live_workbench_target_as_opener(self):
        tabs = [
            {"target": "zhihu-live", "title": "知乎", "url": "https://zhuanlan.zhihu.com/write"},
            {"target": "toutiao-live", "title": "头条号", "url": "https://mp.toutiao.com/profile_v4/graphic/publish"},
        ]
        tabs_after_open = tabs + [
            {"target": "yidian-live", "title": "一点号", "url": "https://mp.yidianzixun.com/#/Writing/articleEditor"},
        ]

        with patch.object(
            publish,
            "ensure_chrome_ready",
            return_value=(tabs, None),
        ), patch.object(
            publish,
            "load_workbench_targets",
            return_value={"toutiao": "toutiao-live"},
        ), patch.object(
            publish,
            "run_cdp",
            return_value="opened",
        ) as mocked_run_cdp, patch.object(
            publish,
            "list_tabs_or_none",
            side_effect=[tabs, tabs_after_open],
        ), patch.object(
            publish.time, "sleep", return_value=None
        ):
            opened = publish.open_missing_platform_tabs(["zhihu", "toutiao", "yidian"], auto_launch=True)

        self.assertEqual(opened, ["yidian"])
        self.assertEqual(mocked_run_cdp.call_args.args[1], "toutiao-live")

    def test_open_missing_platform_tabs_waits_until_missing_tabs_appear(self):
        initial_tabs = [
            {"target": "zhihu-live", "title": "知乎", "url": "https://zhuanlan.zhihu.com/write"},
            {"target": "toutiao-live", "title": "头条号", "url": "https://mp.toutiao.com/profile_v4/graphic/publish"},
        ]
        restored_tabs = initial_tabs + [
            {"target": "yidian-live", "title": "一点号", "url": "https://mp.yidianzixun.com/#/Writing/articleEditor"},
            {"target": "jianshu-live", "title": "简书", "url": "https://www.jianshu.com/writer#/"},
        ]

        with patch.object(
            publish,
            "ensure_chrome_ready",
            return_value=(initial_tabs, None),
        ), patch.object(
            publish,
            "load_workbench_targets",
            return_value={"zhihu": "zhihu-live"},
        ), patch.object(
            publish,
            "run_cdp",
            return_value="opened",
        ), patch.object(
            publish,
            "list_tabs_or_none",
            side_effect=[initial_tabs, restored_tabs],
        ) as mocked_list_tabs, patch.object(
            publish.time, "sleep", return_value=None
        ):
            opened = publish.open_missing_platform_tabs(
                ["zhihu", "toutiao", "yidian", "jianshu"],
                auto_launch=True,
            )

        self.assertEqual(opened, ["yidian", "jianshu"])
        self.assertEqual(mocked_list_tabs.call_count, 2)

    def test_describe_cdp_connection_prefers_managed_browser_source(self):
        detail = publish.describe_cdp_connection(
            {
                "source": "managed_browser_port",
                "detail": "Ordo 托管浏览器调试端口 9333",
            }
        )

        self.assertIn("Ordo 托管浏览器", detail)


if __name__ == "__main__":
    unittest.main()
