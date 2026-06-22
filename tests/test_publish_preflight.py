import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from PIL import Image

import publish


class PublishPreflightTests(unittest.TestCase):
    def _write_cover(self, path: Path, size=(1280, 720)):
        Image.new("RGB", size, color=(23, 45, 67)).save(path)

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

    def test_run_preflight_checks_warns_with_cdp_connection_source(self):
        blockers, warnings = publish.run_preflight_checks(
            platforms=["zhihu"],
            mode="draft",
            workbench={"zhihu": "target-1"},
            cdp_connection={
                "source": "windows_devtools_port_file",
                "detail": "当前 CDP 连接来源：LOCALAPPDATA/Google/Chrome/User Data/DevToolsActivePort",
            },
        )

        self.assertEqual(blockers, [])
        self.assertTrue(any("当前 CDP 连接来源" in item for item in warnings))

    def test_run_preflight_checks_warns_when_config_json_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.json").write_text("{broken", encoding="utf-8")
            covers = base / "covers"
            covers.mkdir()
            self._write_cover(covers / "cover_1.png")

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

    def test_run_preflight_checks_blocks_wechat_without_vps_ip(self):
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
            ):
                blockers, warnings = publish.run_preflight_checks(
                    platforms=["wechat"],
                    mode="publish",
                    workbench={},
                    base_dir=base,
                )

        self.assertEqual(warnings, [])
        self.assertTrue(any("必须走 VPS" in item for item in blockers), blockers)

    def test_run_preflight_checks_requires_browser_tabs_for_browser_platforms(self):
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
            blockers, _warnings = publish.run_preflight_checks(
                platforms=["zhihu", "toutiao"],
                mode="draft",
                workbench={"zhihu": "target-1"},
            )

        self.assertIn("未找到 `toutiao` 的可用标签页，请先在当前远程调试 Chrome 中打开并登录", blockers)

    def test_run_preflight_checks_still_inspects_existing_tabs_when_some_targets_missing(self):
        with patch.object(
            publish,
            "inspect_browser_platform_state",
            return_value={
                "editor_ready": False,
                "page_state": "login_required",
                "current_url": "https://www.zhihu.com/signin",
                "detail": "当前标签页仍处于登录或校验状态",
            },
        ), patch.object(
            publish,
            "persist_browser_session_health",
            return_value={},
        ):
            blockers, warnings = publish.run_preflight_checks(
                platforms=["zhihu", "toutiao"],
                mode="draft",
                workbench={"zhihu": "target-1"},
            )

        self.assertEqual(warnings, [])
        self.assertTrue(any("未找到 `toutiao`" in item for item in blockers))
        self.assertTrue(any("知乎预检未通过" in item for item in blockers))

    def test_run_preflight_checks_blocks_when_browser_page_is_not_editor_ready(self):
        with patch.object(
            publish,
            "inspect_browser_platform_state",
            return_value={
                "editor_ready": False,
                "page_state": "login_required",
                "current_url": "https://www.zhihu.com/signin",
                "detail": "当前标签页不在知乎写作页",
            },
        ), patch.object(
            publish,
            "discover_cover_pool_status",
            return_value={"ok": True, "cover_dir": "/tmp/covers", "error": None},
        ):
            blockers, warnings = publish.run_preflight_checks(
                platforms=["zhihu"],
                mode="draft",
                workbench={"zhihu": "target-1"},
            )

        self.assertEqual(warnings, [])
        self.assertTrue(any("知乎预检未通过" in item for item in blockers))
        self.assertTrue(any("https://www.zhihu.com/signin" in item for item in blockers))

    def test_run_preflight_checks_warns_when_browser_preflight_cannot_read_page_state(self):
        with patch.object(
            publish,
            "inspect_browser_platform_state",
            side_effect=subprocess.CalledProcessError(1, ["node", "live_cdp.mjs"]),
        ):
            blockers, warnings = publish.run_preflight_checks(
                platforms=["toutiao"],
                mode="draft",
                workbench={"toutiao": "target-1"},
            )

        self.assertEqual(blockers, [])
        self.assertTrue(any("头条号预检读取失败" in item for item in warnings))

    def test_run_preflight_checks_persists_healthy_browser_session_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "covers").mkdir()
            self._write_cover(base / "covers" / "cover_1.png")
            with patch.object(
                publish,
                "inspect_browser_platform_state",
                return_value={
                    "editor_ready": True,
                    "page_state": "editor_ready",
                    "current_url": "https://zhuanlan.zhihu.com/write",
                    "detail": "写作编辑器已就绪",
                },
            ):
                publish.run_preflight_checks(
                    platforms=["zhihu"],
                    mode="draft",
                    workbench={"zhihu": "target-1"},
                    base_dir=base,
                    cdp_connection={
                        "source": "managed_browser_port",
                        "detail": "当前 CDP 连接来源：Ordo 托管浏览器调试端口 9333",
                    },
                )

            payload = json.loads((base / ".ordo" / "browser-session" / "state.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["mode"], "managed")
        self.assertEqual(payload["platforms"]["zhihu"]["status"], "healthy")
        self.assertEqual(payload["platforms"]["zhihu"]["page_state"], "editor_ready")
        self.assertTrue(payload["platforms"]["zhihu"]["last_healthy_at"])

    def test_run_preflight_checks_marks_browser_session_relogin_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "covers").mkdir()
            self._write_cover(base / "covers" / "cover_1.png")
            with patch.object(
                publish,
                "inspect_browser_platform_state",
                return_value={
                    "editor_ready": False,
                    "page_state": "login_required",
                    "current_url": "https://www.zhihu.com/signin",
                    "detail": "需要重新登录",
                },
            ):
                blockers, _warnings = publish.run_preflight_checks(
                    platforms=["zhihu"],
                    mode="draft",
                    workbench={"zhihu": "target-1"},
                    base_dir=base,
                    cdp_connection={
                        "source": "managed_browser_port",
                        "detail": "当前 CDP 连接来源：Ordo 托管浏览器调试端口 9333",
                    },
                )

            payload = json.loads((base / ".ordo" / "browser-session" / "state.json").read_text(encoding="utf-8"))

        self.assertTrue(any("知乎预检未通过" in item for item in blockers))
        self.assertEqual(payload["platforms"]["zhihu"]["status"], "expired_or_relogin_required")
        self.assertTrue(payload["platforms"]["zhihu"]["last_relogin_required_at"])

    def test_run_preflight_checks_blocks_jianshu_daily_limit(self):
        with patch.object(
            publish,
            "get_page_text_snippet",
            return_value="每天只能发布 2 篇公开文章，今天已达上限",
        ), patch.object(
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
                "current_url": "https://www.jianshu.com/writer#/notebooks/1/notes/1",
                "detail": "写作编辑器已就绪",
            },
        ):
            blockers, warnings = publish.run_preflight_checks(
                platforms=["jianshu"],
                mode="publish",
                workbench={"jianshu": "note-1"},
            )

        self.assertEqual(blockers, [])
        self.assertTrue(any("简书今天已达到公开文章发布上限" in item for item in warnings))

    def test_preflight_blocks_publish_when_non_wechat_cover_pool_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
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

    def test_append_publish_record_recovers_from_corrupt_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "publish_records.csv"
            rec.write_bytes(b"\xff\xfe\x00broken")
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
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["article_id"], "new-1")

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

    def test_ensure_chrome_ready_launches_managed_browser_when_current_source_is_system(self):
        existing_tabs = [{"target": "sys-1", "title": "知乎", "url": "https://zhuanlan.zhihu.com/write"}]
        managed_tabs = [{"target": "managed-1", "title": "知乎", "url": "https://zhuanlan.zhihu.com/write"}]

        with patch.object(
            publish,
            "load_browser_session_settings",
            return_value={"enabled": True, "debug_port": 9333, "profile_dir": "/tmp/ordo-profile"},
        ), patch.object(
            publish,
            "list_tabs_or_none",
            side_effect=[existing_tabs, managed_tabs],
        ), patch.object(
            publish,
            "get_cdp_connection_metadata",
            side_effect=[
                {"source": "macos_devtools_port_file", "detail": "system"},
                {"source": "managed_browser_port", "detail": "managed"},
            ],
        ), patch.object(
            publish,
            "launch_chrome",
            return_value="Google Chrome",
        ) as mocked_launch, patch.object(
            publish.time, "sleep", return_value=None
        ):
            tabs, launched = publish.ensure_chrome_ready(["zhihu"])

        self.assertEqual(tabs, managed_tabs)
        self.assertEqual(launched, "Google Chrome")
        mocked_launch.assert_called_once()

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
