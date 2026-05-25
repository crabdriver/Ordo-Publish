import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tiandi_engine.platforms.base import BasePlatformAdapter


class DummyAdapter(BasePlatformAdapter):
    def __init__(self, base_dir, platform, returncode=0, stdout="", stderr=""):
        super().__init__(base_dir=base_dir, platform=platform)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def prepare(
        self,
        markdown_file,
        mode,
        theme_name=None,
        cover_path=None,
        template_mode=None,
        article_id=None,
        cover_mode=None,
        ai_declaration_mode=None,
        scheduled_publish_at=None,
    ):
        return {
            "platform": self.platform,
            "markdown_file": str(markdown_file),
            "mode": mode,
            "theme_name": theme_name,
            "cover_path": cover_path,
            "template_mode": template_mode,
            "article_id": article_id,
            "cover_mode": cover_mode,
            "ai_declaration_mode": ai_declaration_mode,
            "scheduled_publish_at": scheduled_publish_at,
        }

    def publish(self, prepared_context):
        return {
            "platform": self.platform,
            "command": f"dummy {self.platform} {prepared_context['markdown_file']}",
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }

    def verify(self, process_result, mode):
        if process_result["returncode"] != 0:
            return "failed"
        return "draft_only" if mode == "draft" else "published"

    def collect_result(self, process_result, mode):
        from tiandi_engine.results.record import ExecutionResult

        return ExecutionResult(
            platform=self.platform,
            stage="publish",
            status=self.verify(process_result, mode),
            summary=process_result["stderr"] or process_result["stdout"] or "ok",
            stdout=process_result["stdout"],
            stderr=process_result["stderr"],
            retryable=False,
        )


class WorkbenchBridgeTests(unittest.TestCase):
    def setUp(self):
        self._environ_patcher = patch.dict(
            "os.environ",
            {
                "ORDO_SHARED_REPO_ROOT": "/dev/null",
                "WECHAT_APPID": "",
                "WECHAT_SECRET": "",
                "WECHAT_AUTHOR": "",
            },
            clear=False,
        )
        self._environ_patcher.start()

    def tearDown(self):
        self._environ_patcher.stop()

    def _write_cover(self, path: Path, size=(1280, 720)):
        Image.new("RGB", size, color=(23, 45, 67)).save(path)

    def test_import_sources_supports_file_folder_and_paste(self):
        from tiandi_engine.workbench.bridge import import_sources

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            md_path = base / "single.md"
            md_path.write_text("# 标题\n\n正文", encoding="utf-8")
            folder = base / "folder"
            folder.mkdir()
            (folder / "a.txt").write_text("标题A\n\n正文A", encoding="utf-8")
            (folder / "b.md").write_text("# 标题B\n\n正文B", encoding="utf-8")

            single = import_sources(base, import_mode="file", source_path=str(md_path))
            batch = import_sources(base, import_mode="folder", source_path=str(folder))
            pasted = import_sources(base, import_mode="paste", pasted_text="粘贴标题\n\n粘贴正文")

            self.assertTrue(Path(batch["job"]["manifest_path"]).is_file())
            manifest_payload = json.loads(Path(batch["job"]["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest_payload["article_count"], 2)
            self.assertEqual(manifest_payload["source_count"], 2)
            self.assertEqual(len(manifest_payload["drafts"]), 2)

        self.assertEqual(single["job"]["article_count"], 1)
        self.assertEqual(batch["job"]["article_count"], 2)
        self.assertEqual(batch["job"]["drafts"][0]["title"], "标题A")
        self.assertEqual(pasted["job"]["drafts"][0]["source_kind"], "paste")

    def test_import_sources_folder_keeps_successes_and_records_failures(self):
        from tiandi_engine.workbench.bridge import import_sources

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            folder = base / "folder"
            folder.mkdir()
            (folder / "a.txt").write_text("标题A\n\n正文A", encoding="utf-8")
            (folder / "broken.bin").write_bytes(b"\x00\x01\x02")

            payload = import_sources(base, import_mode="folder", source_path=str(folder))

        self.assertEqual(payload["job"]["source_count"], 2)
        self.assertEqual(payload["job"]["article_count"], 1)
        self.assertEqual(payload["job"]["failure_count"], 1)
        self.assertEqual(payload["job"]["drafts"][0]["title"], "标题A")
        self.assertEqual(payload["job"]["failures"][0]["source_kind"], "bin")
        self.assertIn("unsupported", payload["job"]["failures"][0]["message"])

    def test_run_publish_job_rejects_when_publish_lock_exists(self):
        from tiandi_engine.workbench.bridge import WORKBENCH_ROOT, run_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            staged_dir = base / ".tiandidistribute" / "workbench" / "articles" / "job"
            staged_dir.mkdir(parents=True, exist_ok=True)
            markdown_path = staged_dir / "a1.md"
            markdown_path.write_text("# 标题\n\n正文", encoding="utf-8")
            lock_path = base / WORKBENCH_ROOT / "publish.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text("busy", encoding="utf-8")
            plan = {
                "publish_job": {
                    "job_id": "job-1",
                    "article_ids": ["a1"],
                    "platforms": ["zhihu"],
                    "status": "pending",
                    "current_step": "",
                    "success_count": 0,
                    "failure_count": 0,
                    "skip_count": 0,
                    "recoverable": True,
                    "error_summary": "",
                },
                "mode": "draft",
                "continue_on_error": False,
                "drafts": [
                    {
                        "article_id": "a1",
                        "title": "标题",
                        "body_markdown": "# 标题\n\n正文",
                        "source_path": str(markdown_path),
                        "source_kind": "markdown",
                        "image_paths": [],
                        "word_count": 2,
                        "template_mode": "default",
                        "theme_name": None,
                        "is_config_complete": True,
                    }
                ],
                "staged_articles": [{"article_id": "a1", "markdown_path": str(markdown_path)}],
                "context_map": [
                    {
                        "article_id": "a1",
                        "platform": "zhihu",
                        "markdown_path": str(markdown_path),
                        "theme_name": None,
                        "template_mode": "default",
                        "cover_path": None,
                        "cover_mode": "auto",
                        "ai_declaration_mode": "auto",
                        "scheduled_publish_at": None,
                    }
                ],
            }

            with self.assertRaisesRegex(RuntimeError, "已有发布任务正在执行"):
                run_publish_job(base, plan, registry={})

    def test_discover_resources_returns_theme_and_cover_pool_details(self):
        from tiandi_engine.workbench.bridge import discover_resources

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "themes").mkdir()
            (base / "themes" / "night.json").write_text('{"name": "Night Theme"}', encoding="utf-8")
            (base / "covers").mkdir()
            self._write_cover(base / "covers" / "a.png")

            payload = discover_resources(base)

        self.assertEqual(payload["theme_pool"]["count"], 1)
        self.assertEqual(payload["theme_pool"]["entries"][0]["theme_id"], "night")
        self.assertTrue(payload["cover_pool"]["ok"])
        self.assertEqual(payload["cover_pool"]["count"], 1)
        self.assertTrue(payload["browser"]["remote_debugging_required"])
        self.assertIn("zhihu", payload["browser"]["browser_platforms"])

    def test_discover_resources_includes_runtime_root_and_python(self):
        from tiandi_engine.workbench.bridge import discover_resources

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            payload = discover_resources(base)

        self.assertEqual(payload["runtime"]["repo_root"], str(base.resolve()))
        self.assertTrue(payload["runtime"]["python_executable"])

    def test_discover_resources_includes_browser_session_settings(self):
        from tiandi_engine.workbench.bridge import discover_resources

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            payload = discover_resources(base)

        self.assertTrue(payload["browser"]["managed_session"]["enabled"])
        self.assertEqual(payload["browser"]["managed_session"]["debug_port"], 9333)
        self.assertIn(".tiandidistribute", payload["browser"]["managed_session"]["profile_dir"])

    def test_discover_resources_reads_browser_session_state(self):
        from tiandi_engine.workbench.bridge import discover_resources

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            session_dir = base / ".tiandidistribute" / "browser-session"
            session_dir.mkdir(parents=True)
            (session_dir / "state.json").write_text(
                json.dumps(
                    {
                        "mode": "managed",
                        "last_checked_at": "2026-03-28T12:00:00",
                        "platforms": {
                            "zhihu": {
                                "status": "expiring_soon",
                                "last_healthy_at": "2026-03-20T12:00:00",
                                "last_reminded_at": "2026-03-27T12:00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            payload = discover_resources(base)

        self.assertEqual(payload["browser"]["session_state"]["mode"], "managed")
        self.assertEqual(payload["browser"]["session_state"]["platforms"]["zhihu"]["status"], "expiring_soon")

    def test_discover_resources_marks_stale_healthy_session_as_expiring_soon(self):
        from tiandi_engine.workbench.bridge import discover_resources

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.json").write_text(
                """
{
  "browser_session": {
    "remind_after_days": 5
  }
}
""".strip(),
                encoding="utf-8",
            )
            session_dir = base / ".tiandidistribute" / "browser-session"
            session_dir.mkdir(parents=True)
            (session_dir / "state.json").write_text(
                json.dumps(
                    {
                        "mode": "managed",
                        "platforms": {
                            "zhihu": {
                                "status": "healthy",
                                "last_healthy_at": "2026-03-20T12:00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("tiandi_engine.workbench.bridge.time.time", return_value=1774785600):
                payload = discover_resources(base)

        self.assertEqual(payload["browser"]["session_state"]["platforms"]["zhihu"]["status"], "expiring_soon")

    def test_discover_resources_returns_config_warning_when_config_json_invalid(self):
        from tiandi_engine.workbench.bridge import discover_resources

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.json").write_text("{broken", encoding="utf-8")

            payload = discover_resources(base)

        self.assertIn("config.json", payload["config_warning"] or "")

    def test_plan_publish_job_creates_assignments_and_staged_markdown(self):
        from tiandi_engine.workbench.bridge import import_sources, plan_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "themes").mkdir()
            (base / "themes" / "night.json").write_text('{"name": "Night Theme"}', encoding="utf-8")
            (base / "covers").mkdir()
            self._write_cover(base / "covers" / "a.png")

            imported = import_sources(base, import_mode="paste", pasted_text="桥接标题\n\n桥接正文")
            plan = plan_publish_job(
                base,
                drafts=imported["job"]["drafts"],
                platforms=["wechat", "zhihu"],
                mode="draft",
                seed=1,
            )

            self.assertEqual(plan["publish_job"]["platforms"], ["wechat", "zhihu"])
            self.assertEqual(len(plan["template_assignments"]), 1)
            self.assertEqual(len(plan["cover_assignments"]), 1)
            staged_path = Path(plan["staged_articles"][0]["markdown_path"])
            self.assertTrue(staged_path.is_file())
            self.assertIn("桥接标题", staged_path.read_text(encoding="utf-8"))
            self.assertTrue(any(item["platform"] == "zhihu" for item in plan["context_map"]))

    def test_plan_publish_job_includes_publish_option_modes_in_context_map(self):
        from tiandi_engine.workbench.bridge import import_sources, plan_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "themes").mkdir()
            (base / "themes" / "night.json").write_text('{"name": "Night Theme"}', encoding="utf-8")
            (base / "covers").mkdir()
            self._write_cover(base / "covers" / "a.png")
            imported = import_sources(base, import_mode="paste", pasted_text="桥接标题\n\n桥接正文")

            plan = plan_publish_job(
                base,
                drafts=imported["job"]["drafts"],
                platforms=["zhihu"],
                mode="draft",
                cover_mode="force_off",
                ai_declaration_mode="force_off",
            )

        self.assertEqual(plan["cover_assignments"], [])
        self.assertEqual(plan["context_map"][0]["cover_mode"], "force_off")
        self.assertEqual(plan["context_map"][0]["ai_declaration_mode"], "force_off")

    def test_plan_publish_job_includes_scheduled_publish_at_for_toutiao_publish(self):
        from tiandi_engine.workbench.bridge import import_sources, plan_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            imported = import_sources(base, import_mode="paste", pasted_text="桥接标题\n\n桥接正文")

            plan = plan_publish_job(
                base,
                drafts=imported["job"]["drafts"],
                platforms=["toutiao", "zhihu"],
                mode="publish",
                scheduled_publish_at="2026-03-30T09:30",
            )

        self.assertEqual(plan["scheduled_publish_at"], "2026-03-30T09:30")
        self.assertEqual(plan["publish_job"]["scheduled_publish_at"], "2026-03-30T09:30")
        toutiao_context = next(item for item in plan["context_map"] if item["platform"] == "toutiao")
        zhihu_context = next(item for item in plan["context_map"] if item["platform"] == "zhihu")
        self.assertEqual(toutiao_context["scheduled_publish_at"], "2026-03-30T09:30")
        self.assertIsNone(zhihu_context["scheduled_publish_at"])

    def test_plan_publish_job_skips_cover_assignment_for_jianshu_boundary(self):
        from tiandi_engine.workbench.bridge import import_sources, plan_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "covers").mkdir()
            self._write_cover(base / "covers" / "a.png")
            imported = import_sources(base, import_mode="paste", pasted_text="桥接标题\n\n桥接正文")

            plan = plan_publish_job(
                base,
                drafts=imported["job"]["drafts"],
                platforms=["jianshu"],
                mode="publish",
                cover_mode="auto",
            )

        self.assertEqual(plan["cover_assignments"], [])
        self.assertIsNone(plan["context_map"][0]["cover_path"])

    def test_plan_publish_job_blocks_force_on_cover_when_pool_missing(self):
        from tiandi_engine.workbench.bridge import import_sources, plan_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            imported = import_sources(base, import_mode="paste", pasted_text="桥接标题\n\n桥接正文")

            with self.assertRaisesRegex(ValueError, "封面"):
                plan_publish_job(
                    base,
                    drafts=imported["job"]["drafts"],
                    platforms=["zhihu"],
                    mode="draft",
                    cover_mode="force_on",
                )

    def test_run_publish_job_emits_structured_events(self):
        from tiandi_engine.workbench.bridge import import_sources, plan_publish_job, run_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "themes").mkdir()
            (base / "themes" / "night.json").write_text('{"name": "Night Theme"}', encoding="utf-8")
            imported = import_sources(base, import_mode="paste", pasted_text="执行标题\n\n执行正文")
            plan = plan_publish_job(
                base,
                drafts=imported["job"]["drafts"],
                platforms=["wechat"],
                mode="draft",
            )
            registry = {
                "wechat": DummyAdapter(base, "wechat", returncode=0, stdout="ok"),
            }

            result = run_publish_job(base, plan, registry=registry)

        event_types = [event["type"] for event in result["events"]]
        self.assertEqual(event_types[0], "job_started")
        self.assertIn("platform_started", event_types)
        self.assertIn("platform_finished", event_types)
        self.assertEqual(event_types[-1], "job_finished")
        self.assertEqual(result["publish_job"]["success_count"], 1)
        self.assertEqual(result["results"][0]["status"], "draft_only")

    def test_run_publish_job_supports_sparse_context_map_for_retry_plans(self):
        from tiandi_engine.workbench.bridge import run_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            a1 = base / "a1.md"
            a2 = base / "a2.md"
            a1.write_text("# A1\n\nbody", encoding="utf-8")
            a2.write_text("# A2\n\nbody", encoding="utf-8")
            registry = {
                "wechat": DummyAdapter(base, "wechat", returncode=0, stdout="ok"),
                "zhihu": DummyAdapter(base, "zhihu", returncode=0, stdout="ok"),
            }
            sparse_plan = {
                "publish_job": {
                    "job_id": "retry-job",
                    "article_ids": ["a1", "a2"],
                    "platforms": ["wechat", "zhihu"],
                    "status": "pending",
                    "current_step": "",
                    "success_count": 0,
                    "failure_count": 0,
                    "skip_count": 0,
                    "recoverable": True,
                    "error_summary": "",
                },
                "mode": "draft",
                "continue_on_error": True,
                "drafts": [
                    {"article_id": "a1", "title": "A1", "body_markdown": "# A1", "source_path": None, "source_kind": "markdown"},
                    {"article_id": "a2", "title": "A2", "body_markdown": "# A2", "source_path": None, "source_kind": "markdown"},
                ],
                "staged_articles": [
                    {"article_id": "a1", "markdown_path": str(a1)},
                    {"article_id": "a2", "markdown_path": str(a2)},
                ],
                "context_map": [
                    {
                        "article_id": "a1",
                        "platform": "zhihu",
                        "markdown_path": str(a1),
                        "theme_name": None,
                        "template_mode": "default",
                        "cover_path": None,
                    },
                    {
                        "article_id": "a2",
                        "platform": "wechat",
                        "markdown_path": str(a2),
                        "theme_name": None,
                        "template_mode": "default",
                        "cover_path": None,
                    },
                ],
            }

            result = run_publish_job(base, sparse_plan, registry=registry)

        self.assertEqual(len(result["results"]), 2)
        self.assertEqual([item["platform"] for item in result["results"]], ["zhihu", "wechat"])

    def test_run_publish_job_passes_scheduled_publish_at_to_adapter(self):
        from tiandi_engine.workbench.bridge import run_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            article = base / "article.md"
            article.write_text("# 标题\n\n正文", encoding="utf-8")
            registry = {
                "toutiao": DummyAdapter(
                    base_dir=base,
                    platform="toutiao",
                    returncode=0,
                    stdout="已设置定时发布",
                )
            }
            plan = {
                "publish_job": {
                    "job_id": "scheduled-job",
                    "article_ids": ["article"],
                    "platforms": ["toutiao"],
                    "status": "pending",
                    "current_step": "",
                    "success_count": 0,
                    "failure_count": 0,
                    "skip_count": 0,
                    "recoverable": True,
                    "error_summary": "",
                    "scheduled_publish_at": "2026-03-30T09:30",
                },
                "mode": "publish",
                "continue_on_error": False,
                "drafts": [
                    {"article_id": "article", "title": "标题", "body_markdown": "# 标题", "source_path": None, "source_kind": "markdown"}
                ],
                "staged_articles": [{"article_id": "article", "markdown_path": str(article)}],
                "context_map": [
                    {
                        "article_id": "article",
                        "platform": "toutiao",
                        "markdown_path": str(article),
                        "theme_name": None,
                        "template_mode": "default",
                        "cover_path": None,
                        "cover_mode": "auto",
                        "ai_declaration_mode": "auto",
                        "scheduled_publish_at": "2026-03-30T09:30",
                    }
                ],
            }

            result = run_publish_job(base, plan, registry=registry)

        self.assertEqual(result["results"][0]["scheduled_publish_at"], "2026-03-30T09:30")

    def test_run_publish_job_uses_failed_summary_when_continue_on_error_finishes_late_success(self):
        from tiandi_engine.workbench.bridge import run_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            article = base / "article.md"
            article.write_text("# Title\n\nbody", encoding="utf-8")
            registry = {
                "wechat": DummyAdapter(base, "wechat", returncode=1, stderr="wechat failed"),
                "zhihu": DummyAdapter(base, "zhihu", returncode=0, stdout="zhihu ok"),
            }
            plan = {
                "publish_job": {
                    "job_id": "job-summary",
                    "article_ids": ["article"],
                    "platforms": ["wechat", "zhihu"],
                    "status": "pending",
                    "current_step": "",
                    "success_count": 0,
                    "failure_count": 0,
                    "skip_count": 0,
                    "recoverable": True,
                    "error_summary": "",
                },
                "mode": "draft",
                "continue_on_error": True,
                "drafts": [
                    {"article_id": "article", "title": "Title", "body_markdown": "# Title", "source_path": None, "source_kind": "markdown"}
                ],
                "staged_articles": [{"article_id": "article", "markdown_path": str(article)}],
                "context_map": [
                    {
                        "article_id": "article",
                        "platform": "wechat",
                        "markdown_path": str(article),
                        "theme_name": None,
                        "template_mode": "default",
                        "cover_path": None,
                    },
                    {
                        "article_id": "article",
                        "platform": "zhihu",
                        "markdown_path": str(article),
                        "theme_name": None,
                        "template_mode": "default",
                        "cover_path": None,
                    },
                ],
            }

            result = run_publish_job(base, plan, registry=registry)

        self.assertEqual(result["publish_job"]["status"], "failed")
        self.assertIn("wechat failed", result["publish_job"]["error_summary"])

    def test_read_recent_history_reads_records_and_session_snapshot(self):
        from tiandi_engine.workbench.bridge import read_recent_history

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            records = base / "publish_records.csv"
            records.write_text(
                "timestamp,article,article_id,platform,mode,theme_name,template_mode,cover_path,status,error_type,returncode,stdout,stderr\n"
                "2026-03-27 12:00:00,/tmp/a.md,a1,zhihu,draft,night,default,/tmp/c.png,draft_only,,0,ok,\n",
                encoding="utf-8",
            )
            session_dir = base / ".tiandidistribute" / "publish-console"
            session_dir.mkdir(parents=True)
            (session_dir / "publish-console-session.json").write_text(
                '{"summary":{"total_articles":1},"items":[{"article_id":"a1"}]}',
                encoding="utf-8",
            )

            history = read_recent_history(base, limit=5)

        self.assertEqual(len(history["records"]), 1)
        self.assertEqual(history["records"][0]["platform"], "zhihu")
        self.assertEqual(history["session"]["summary"]["total_articles"], 1)

    def test_read_recent_history_reads_last_workbench_plan_and_result(self):
        from tiandi_engine.workbench.bridge import read_recent_history

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workbench_dir = base / ".tiandidistribute" / "workbench"
            workbench_dir.mkdir(parents=True)
            (workbench_dir / "last-plan.json").write_text(
                json.dumps(
                    {
                        "publish_job": {
                            "job_id": "plan-1",
                            "article_ids": ["a1"],
                            "platforms": ["zhihu"],
                            "status": "pending",
                            "current_step": "",
                            "success_count": 0,
                            "failure_count": 0,
                            "skip_count": 0,
                            "recoverable": True,
                            "error_summary": "",
                        },
                        "mode": "draft",
                    }
                ),
                encoding="utf-8",
            )
            (workbench_dir / "last-result.json").write_text(
                json.dumps(
                    {
                        "publish_job": {
                            "job_id": "plan-1",
                            "status": "failed",
                            "article_ids": ["a1"],
                            "platforms": ["zhihu"],
                        },
                        "results": [{"article_id": "a1", "platform": "zhihu", "status": "failed"}],
                    }
                ),
                encoding="utf-8",
            )

            history = read_recent_history(base, limit=5)

        self.assertEqual(history["last_plan"]["publish_job"]["job_id"], "plan-1")
        self.assertEqual(history["last_result"]["publish_job"]["status"], "failed")
        self.assertEqual(history["recovery"]["status"], "recoverable")

    def test_read_recent_history_tolerates_corrupt_snapshots_and_reports_state(self):
        from tiandi_engine.workbench.bridge import read_recent_history

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workbench_dir = base / ".tiandidistribute" / "workbench"
            session_dir = base / ".tiandidistribute" / "publish-console"
            workbench_dir.mkdir(parents=True)
            session_dir.mkdir(parents=True)
            (workbench_dir / "last-plan.json").write_text("{not-json", encoding="utf-8")
            (session_dir / "publish-console-session.json").write_text(
                '{"summary":{"total_articles":1},"items":[{"article_id":"a1"}]}',
                encoding="utf-8",
            )

            history = read_recent_history(base, limit=5)

        self.assertIsNone(history["last_plan"])
        self.assertEqual(history["recovery"]["status"], "snapshot_corrupted")
        self.assertIn("last_plan", history["recovery"]["issues"])

    def test_read_recent_history_reports_result_missing_when_only_plan_exists(self):
        from tiandi_engine.workbench.bridge import read_recent_history

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workbench_dir = base / ".tiandidistribute" / "workbench"
            workbench_dir.mkdir(parents=True)
            (workbench_dir / "last-plan.json").write_text(
                json.dumps(
                    {
                        "publish_job": {
                            "job_id": "plan-only",
                            "article_ids": ["a1"],
                            "platforms": ["zhihu"],
                            "status": "pending",
                            "current_step": "",
                            "success_count": 0,
                            "failure_count": 0,
                            "skip_count": 0,
                            "recoverable": True,
                            "error_summary": "",
                        }
                    }
                ),
                encoding="utf-8",
            )

            history = read_recent_history(base, limit=5)

        self.assertEqual(history["recovery"]["status"], "result_missing")

    def test_read_recent_history_marks_restore_unavailable_when_staged_markdown_missing(self):
        from tiandi_engine.workbench.bridge import read_recent_history

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workbench_dir = base / ".tiandidistribute" / "workbench"
            workbench_dir.mkdir(parents=True)
            (workbench_dir / "last-plan.json").write_text(
                json.dumps(
                    {
                        "publish_job": {
                            "job_id": "plan-restore",
                            "article_ids": ["a1"],
                            "platforms": ["zhihu"],
                            "status": "pending",
                            "current_step": "",
                            "success_count": 0,
                            "failure_count": 0,
                            "skip_count": 0,
                            "recoverable": True,
                            "error_summary": "",
                        },
                        "staged_articles": [
                            {
                                "article_id": "a1",
                                "markdown_path": str(base / ".tiandidistribute" / "workbench" / "articles" / "missing.md"),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            history = read_recent_history(base, limit=5)

        self.assertFalse(history["recovery"]["can_restore_plan"])
        self.assertEqual(len(history["recovery"]["missing_staged_articles"]), 1)

    def test_plan_publish_job_preserves_last_result_without_explicit_reset(self):
        from tiandi_engine.workbench.bridge import import_sources, plan_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workbench_dir = base / ".tiandidistribute" / "workbench"
            workbench_dir.mkdir(parents=True)
            last_result = workbench_dir / "last-result.json"
            last_result.write_text('{"publish_job":{"job_id":"old-job","status":"failed"}}', encoding="utf-8")
            imported = import_sources(base, import_mode="paste", pasted_text="标题\n\n正文")

            plan_publish_job(base, drafts=imported["job"]["drafts"], platforms=["wechat"], mode="draft")
            self.assertTrue(last_result.exists())

    def test_plan_publish_job_can_explicitly_reset_last_result(self):
        from tiandi_engine.workbench.bridge import import_sources, plan_publish_job

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workbench_dir = base / ".tiandidistribute" / "workbench"
            workbench_dir.mkdir(parents=True)
            last_result = workbench_dir / "last-result.json"
            last_result.write_text('{"publish_job":{"job_id":"old-job","status":"failed"}}', encoding="utf-8")
            imported = import_sources(base, import_mode="paste", pasted_text="标题\n\n正文")

            plan_publish_job(
                base,
                drafts=imported["job"]["drafts"],
                platforms=["wechat"],
                mode="draft",
                clear_last_result=True,
            )
            self.assertFalse(last_result.exists())

    def test_handle_bridge_command_routes_json_requests(self):
        from tiandi_engine.workbench.bridge import handle_bridge_command

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            response = handle_bridge_command(
                base,
                {
                    "command": "import_sources",
                    "import_mode": "paste",
                    "pasted_text": "命令标题\n\n命令正文",
                },
            )

        self.assertIn("job", response)
        self.assertEqual(response["job"]["drafts"][0]["title"], "命令标题")

    def test_read_wechat_settings_returns_empty_values_when_missing(self):
        from tiandi_engine.workbench.bridge import read_wechat_settings

        with tempfile.TemporaryDirectory() as tmp:
            payload = read_wechat_settings(Path(tmp))

        self.assertEqual(payload["app_id"], "")
        self.assertEqual(payload["secret"], "")
        self.assertEqual(payload["author"], "")
        self.assertFalse(payload["status"]["appid_ready"])
        self.assertFalse(payload["status"]["secret_ready"])

    def test_save_wechat_settings_persists_values_and_preserves_other_lines(self):
        from tiandi_engine.workbench.bridge import read_wechat_settings, save_wechat_settings

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env_path = base / "secrets.env"
            env_path.write_text("# keep\nOTHER_KEY=1\nWECHAT_APPID=old\n", encoding="utf-8")

            saved = save_wechat_settings(
                base,
                app_id="wx_app_123",
                secret="secret_456",
                author="Wizard",
            )
            reread = read_wechat_settings(base)
            content = env_path.read_text(encoding="utf-8")

        self.assertEqual(saved["app_id"], "wx_app_123")
        self.assertEqual(reread["secret"], "secret_456")
        self.assertEqual(reread["author"], "Wizard")
        self.assertIn("# keep", content)
        self.assertIn("OTHER_KEY=1", content)
        self.assertIn("WECHAT_APPID=wx_app_123", content)
        self.assertIn("WECHAT_SECRET=secret_456", content)
        self.assertIn("WECHAT_AUTHOR=Wizard", content)

    def test_save_wechat_settings_does_not_clear_existing_values_without_explicit_clear(self):
        from tiandi_engine.workbench.bridge import read_wechat_settings, save_wechat_settings

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            env_path = base / "secrets.env"
            env_path.write_text(
                "WECHAT_APPID=keep-app\nWECHAT_SECRET=keep-secret\nWECHAT_AUTHOR=keep-author\n",
                encoding="utf-8",
            )

            save_wechat_settings(base, app_id="", secret="", author="")
            reread = read_wechat_settings(base)

        self.assertEqual(reread["app_id"], "keep-app")
        self.assertEqual(reread["secret"], "keep-secret")
        self.assertEqual(reread["author"], "keep-author")

    def test_discover_resources_includes_wechat_status(self):
        from tiandi_engine.workbench.bridge import discover_resources, save_wechat_settings

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            payload_before = discover_resources(base)
            save_wechat_settings(base, app_id="wx_1", secret="sec_1", author="A")
            payload_after = discover_resources(base)

        self.assertIn("wechat", payload_before)
        self.assertFalse(payload_before["wechat"]["status"]["appid_ready"])
        self.assertTrue(payload_after["wechat"]["status"]["appid_ready"])
        self.assertEqual(payload_after["wechat"]["settings"]["author"], "A")

    def test_handle_bridge_command_routes_wechat_setting_commands(self):
        from tiandi_engine.workbench.bridge import handle_bridge_command

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            save_response = handle_bridge_command(
                base,
                {
                    "command": "save_wechat_settings",
                    "app_id": "wx_test",
                    "secret": "sec_test",
                    "author": "author_test",
                },
            )
            read_response = handle_bridge_command(base, {"command": "read_wechat_settings"})

        self.assertEqual(save_response["app_id"], "wx_test")
        self.assertEqual(read_response["secret"], "sec_test")

    def test_stream_bridge_appends_publish_records_for_desktop_runs(self):
        import publish
        from scripts import workbench_bridge as bridge_script

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            records_path = base / "publish_records.csv"
            stdin = io.StringIO(json.dumps({"command": "run_publish_job_stream", "plan": {"publish_job": {"job_id": "job-1"}}}))
            stdout = io.StringIO()

            def fake_run_publish_job(base_dir, plan_payload, *, registry=None, append_record=None, event_sink=None):
                result = {
                    "article": str(base / "article.md"),
                    "article_id": "article-1",
                    "platform": "wechat",
                    "mode": "draft",
                    "theme_name": "",
                    "template_mode": "default",
                    "cover_path": "",
                    "status": "draft_only",
                    "error_type": None,
                    "returncode": 0,
                    "stdout": "ok",
                    "stderr": "",
                    "summary": "ok",
                    "retryable": False,
                }
                if event_sink:
                    event_sink({"type": "job_started", "job_id": "job-1"})
                if append_record:
                    append_record(result)
                return {
                    "publish_job": {
                        "job_id": "job-1",
                        "status": "completed",
                        "success_count": 1,
                        "failure_count": 0,
                        "skip_count": 0,
                        "recoverable": True,
                        "error_summary": "",
                        "current_step": "done",
                    },
                    "events": [{"type": "job_started", "job_id": "job-1"}],
                    "results": [result],
                }

            with patch.object(bridge_script, "ROOT_DIR", base), patch.object(
                publish, "PUBLISH_RECORDS_FILE", records_path
            ), patch("sys.stdin", stdin), patch("sys.stdout", stdout), patch(
                "tiandi_engine.workbench.bridge.run_publish_job", side_effect=fake_run_publish_job
            ):
                bridge_script.main()

            self.assertTrue(records_path.exists())
            content = records_path.read_text(encoding="utf-8")
            self.assertIn("article_id", content)
            self.assertIn("article-1", content)
            self.assertIn('"type": "command_result"', stdout.getvalue())

    def test_packaged_bridge_discover_resources_works_from_minimal_runtime_layout(self):
        manifest = json.loads((Path(__file__).resolve().parent.parent / "desktop" / "runtime-manifest.json").read_text(encoding="utf-8"))
        repo_root = Path(__file__).resolve().parent.parent

        with tempfile.TemporaryDirectory() as tmp:
            runtime_repo = Path(tmp) / "repo"
            runtime_repo.mkdir(parents=True)
            for relative in manifest["include"]:
                source = repo_root / relative
                target = runtime_repo / relative
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

            proc = subprocess.run(
                [sys.executable, str(runtime_repo / "scripts" / "workbench_bridge.py")],
                input=json.dumps({"command": "discover_resources"}),
                text=True,
                capture_output=True,
                cwd=tmp,
            )

        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["runtime"]["repo_root"], str(runtime_repo.resolve()))
        self.assertIn("theme_pool", payload)
        self.assertIn("cover_pool", payload)


if __name__ == "__main__":
    unittest.main()
