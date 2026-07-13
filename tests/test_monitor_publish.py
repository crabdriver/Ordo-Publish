import importlib.util
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import markdown_utils
from PIL import Image, ImageCms

from ordo_engine.assignment.cover_contract import CoverContractError


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "monitor_publish.py"
SPEC = importlib.util.spec_from_file_location("monitor_publish", MODULE_PATH)
monitor_publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor_publish)


@contextmanager
def fake_jianshu_browser(*, dry_run=False):
    yield {"LIVE_CDP_PORT": "9333"}


class MonitorPublishTests(unittest.TestCase):
    def _write_canonical_cover(self, path: Path):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        Image.new("RGB", (2538, 1080), color=(23, 45, 67)).save(
            path,
            format="PNG",
            icc_profile=profile,
        )

    def test_script_entrypoint_can_import_project_modules(self):
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            cwd="/tmp",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_vps_default_matches_deployed_runtime(self):
        self.assertEqual(monitor_publish.DEFAULT_VPS_PATH, "/root/ordo-publish")

    def test_list_articles_only_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("# A", encoding="utf-8")
            (root / ".hidden.md").write_text("# H", encoding="utf-8")
            (root / "b.txt").write_text("B", encoding="utf-8")

            self.assertEqual([p.name for p in monitor_publish.list_articles(root)], ["a.md"])

    def test_publish_article_writes_state_lock_after_real_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "第一课.md"
            article.write_text("# 第一课", encoding="utf-8")
            state_file = root / "state.json"

            with patch.object(monitor_publish, "STATE_FILE", state_file), patch.object(
                monitor_publish,
                "run_cmd",
                return_value=0,
            ), patch.object(
                monitor_publish,
                "jianshu_dedicated_browser",
                fake_jianshu_browser,
            ):
                result = monitor_publish.publish_article(article)
                second = monitor_publish.publish_article(article)

            self.assertEqual(result, "success")
            self.assertEqual(second, "skipped")

    def test_frontmatter_rejects_noncanonical_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cover = root / "custom.png"
            self._write_canonical_cover(cover)
            article = root / "第一课.md"
            article.write_text(
                "---\ncover: custom.png\nplatform_covers:\n"
                "  wechat: custom.png\n  zhihu: custom.png\n  toutiao: custom.png\n"
                "  yidian: custom.png\n  bilibili: custom.png\n  jianshu: custom.png\n"
                "template_theme: sspai\n---\n# 第一课",
                encoding="utf-8",
            )

            meta = monitor_publish.parse_frontmatter(article)

            self.assertEqual(meta["template_theme"], "sspai")
            with self.assertRaisesRegex(CoverContractError, "cover.png"):
                monitor_publish.find_sidecar_cover(article, meta)

    def test_article_id_finds_single_publication_package_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cover = root / "assets" / "20260709-first-lesson" / "cover.png"
            cover.parent.mkdir(parents=True)
            self._write_canonical_cover(cover)
            article = root / "第一课.md"
            relative = "assets/20260709-first-lesson/cover.png"
            platform_lines = "".join(
                f"  {platform}: {relative}\n"
                for platform in ("wechat", "zhihu", "toutiao", "yidian", "bilibili", "jianshu")
            )
            article.write_text(
                "---\narticle_id: 20260709-first-lesson\n"
                f"cover: {relative}\nplatform_covers:\n{platform_lines}---\n# 第一课",
                encoding="utf-8",
            )

            meta = monitor_publish.parse_frontmatter(article)

            self.assertEqual(monitor_publish.find_sidecar_cover(article, meta), cover.resolve())

    def test_publish_command_includes_cover_and_template(self):
        cmd = monitor_publish.build_publish_cmd(
            Path("/tmp/a.md"),
            platforms=monitor_publish.PUBLISH_PLATFORMS,
            mode="publish",
            cover=Path("/tmp/c.png"),
            template_theme="sspai",
        )

        self.assertIn("--cover", cmd)
        self.assertIn("--cover-mode", cmd)
        self.assertIn("--template-theme", cmd)
        self.assertNotIn("--assume-yes", cmd)
        self.assertEqual(cmd[cmd.index("--cover") + 1], "/tmp/c.png")
        self.assertEqual(cmd[cmd.index("--cover-mode") + 1], "force_on")
        self.assertEqual(cmd[cmd.index("--template-theme") + 1], "sspai")

    def test_publish_command_can_run_local_for_jianshu(self):
        cmd = monitor_publish.build_publish_cmd(
            Path("/tmp/a.md"),
            platforms=monitor_publish.LOCAL_PUBLISH_PLATFORMS,
            mode="publish",
            remote="local",
            no_auto_launch=True,
        )

        self.assertEqual(cmd[cmd.index("--platform") + 1], "jianshu")
        self.assertEqual(cmd[cmd.index("--remote") + 1], "local")
        self.assertIn("--no-auto-launch", cmd)

    def test_markdown_frontmatter_is_not_rendered(self):
        rendered = markdown_utils.render_markdown_plain_text(
            "---\ncover: c.png\ntemplate_theme: sspai\n---\n# 标题\n\n正文"
        )

        self.assertNotIn("cover:", rendered)
        self.assertIn("标题", rendered)
        self.assertIn("正文", rendered)

    def test_attempted_article_retries_failed_groups_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "第一课.md"
            article.write_text("# 第一课", encoding="utf-8")
            state = {
                "articles": {
                    str(article.resolve()): {
                        "status": "attempted",
                        "wechat_returncode": 0,
                        "publish_returncode": 1,
                        "local_publish_returncode": 1,
                    }
                }
            }

            with patch.object(monitor_publish, "run_cmd", return_value=0) as mocked_run, patch.object(
                monitor_publish,
                "save_state",
            ), patch.object(
                monitor_publish,
                "jianshu_dedicated_browser",
                fake_jianshu_browser,
            ):
                result = monitor_publish.publish_article(article, state=state)

            self.assertEqual(result, "success")
            self.assertEqual(mocked_run.call_count, 5)
            called_platforms = [call.args[0][call.args[0].index("--platform") + 1] for call in mocked_run.call_args_list]
            self.assertEqual(called_platforms, ["zhihu", "toutiao", "yidian", "bilibili", "jianshu"])

    def test_publish_article_retries_only_failed_remote_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = Path(tmp) / "第一课.md"
            article.write_text("# 第一课", encoding="utf-8")
            state = {"articles": {str(article.resolve()): {
                "status": "attempted",
                "platforms": {
                    "wechat": {"returncode": 0},
                    "zhihu": {"returncode": 0},
                    "toutiao": {"returncode": 0},
                    "yidian": {"returncode": 1},
                    "bilibili": {"returncode": 0},
                    "jianshu": {"returncode": 0},
                },
            }}}
            with patch.object(monitor_publish, "run_cmd", return_value=0) as run, patch.object(
                monitor_publish, "save_state"
            ), patch.object(monitor_publish, "jianshu_dedicated_browser", fake_jianshu_browser):
                result = monitor_publish.publish_article(article, state=state)

            self.assertEqual(result, "success")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--platform") + 1], "yidian")

    def test_scan_once_includes_attempted_article(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "第一课.md"
            article.write_text("# 第一课", encoding="utf-8")
            state_file = root / "state.json"
            state_file.write_text(
                '{"articles":{"' + str(article.resolve()) + '":{"status":"attempted"}}}',
                encoding="utf-8",
            )

            with patch.object(monitor_publish, "STATE_FILE", state_file), patch.object(
                monitor_publish,
                "publish_article",
                return_value="success",
            ) as mocked_publish, patch.object(monitor_publish, "require_vps_ready"):
                todo = monitor_publish.scan_once(root)

            self.assertEqual([p.resolve() for p in todo], [article.resolve()])
            mocked_publish.assert_called_once()

    def test_scan_once_blocks_when_vps_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "第一课.md"
            article.write_text("# 第一课", encoding="utf-8")

            with patch.object(monitor_publish, "require_vps_ready", side_effect=RuntimeError("blocked")), patch.object(
                monitor_publish,
                "publish_article",
            ) as mocked_publish:
                with self.assertRaisesRegex(RuntimeError, "blocked"):
                    monitor_publish.scan_once(root)

            mocked_publish.assert_not_called()

    def test_publish_article_imports_successes_from_publish_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "第一课.md"
            article.write_text("# 第一课", encoding="utf-8")
            records = root / "publish_records.csv"
            records.write_text(
                "timestamp,article,article_id,platform,mode,theme_name,template_mode,cover_path,status,error_type,current_url,page_state,smoke_step,returncode,stdout,stderr\n"
                f"t,{article},,wechat,draft,,,,draft_only,,,,,0,,\n"
                f"t,{article},,zhihu,publish,,,,published,,,,,0,,\n"
                f"t,{article},,toutiao,publish,,,,published,,,,,0,,\n"
                f"t,{article},,yidian,publish,,,,published,,,,,0,,\n"
                f"t,{article},,bilibili,publish,,,,published,,,,,0,,\n",
                encoding="utf-8",
            )

            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(
                monitor_publish,
                "run_cmd",
                return_value=0,
            ) as mocked_run, patch.object(
                monitor_publish,
                "jianshu_dedicated_browser",
                fake_jianshu_browser,
            ), patch.object(monitor_publish, "save_state"), patch.object(monitor_publish, "require_vps_ready"):
                result = monitor_publish.publish_article(article, state={"articles": {}})

            self.assertEqual(result, "success")
            self.assertEqual(mocked_run.call_count, 1)
            cmd = mocked_run.call_args.args[0]
            self.assertEqual(cmd[cmd.index("--platform") + 1], "jianshu")

    def test_jianshu_dedicated_browser_starts_and_closes(self):
        class Proc:
            terminated = False
            killed = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.killed = True

        proc = Proc()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            monitor_publish,
            "find_chrome_binary",
            return_value=Path("/tmp/chrome"),
        ), patch.object(
            monitor_publish.subprocess,
            "Popen",
            return_value=proc,
        ) as mocked_popen, patch.object(
            monitor_publish,
            "wait_for_cdp_port",
            return_value=True,
        ), patch.object(
            monitor_publish,
            "close_dedicated_browser",
        ) as mocked_close:
            with patch.object(monitor_publish, "BASE_DIR", Path(tmp)):
                with monitor_publish.jianshu_dedicated_browser() as env:
                    self.assertEqual(env["LIVE_CDP_PORT"], "9333")

        mocked_popen.assert_called_once()
        mocked_close.assert_called_once()
        self.assertTrue(proc.terminated)

    def test_scan_once_skips_article_when_records_cover_all_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "第一课.md"
            article.write_text("# 第一课", encoding="utf-8")
            records = root / "publish_records.csv"
            rows = [
                "timestamp,article,article_id,platform,mode,theme_name,template_mode,cover_path,status,error_type,current_url,page_state,smoke_step,returncode,stdout,stderr",
                f"t,{article},,wechat,draft,,,,draft_only,,,,,0,,",
                f"t,{article},,zhihu,publish,,,,published,,,,,0,,",
                f"t,{article},,toutiao,publish,,,,published,,,,,0,,",
                f"t,{article},,yidian,publish,,,,published,,,,,0,,",
                f"t,{article},,bilibili,publish,,,,published,,,,,0,,",
                f"t,{article},,jianshu,publish,,,,published,,,,,0,,",
            ]
            records.write_text("\n".join(rows) + "\n", encoding="utf-8")

            with patch.object(monitor_publish, "PUBLISH_RECORDS_FILE", records), patch.object(monitor_publish, "require_vps_ready"):
                todo = monitor_publish.scan_once(root)

            self.assertEqual(todo, [])


if __name__ == "__main__":
    unittest.main()
