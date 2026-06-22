import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordo_engine.workbench.preflight import build_real_publish_preflight


class RealPublishPreflightTests(unittest.TestCase):
    def test_build_real_publish_preflight_writes_report_with_blockers_and_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_dir = base / "articles"
            source_dir.mkdir()
            cover_dir = base / "covers"
            cover_dir.mkdir()
            matrix_path = base / ".ordo" / "workbench" / "matrices" / "mx.json"
            matrix_path.parent.mkdir(parents=True, exist_ok=True)
            matrix_path.write_text("{}", encoding="utf-8")

            with patch(
                "ordo_engine.workbench.preflight.import_sources",
                return_value={
                    "job": {
                        "job_id": "import-1",
                        "article_count": 2,
                        "failure_count": 1,
                        "source_count": 3,
                        "manifest_path": str(base / "manifest.json"),
                        "drafts": [{"article_id": "a1", "title": "标题", "body_markdown": "# 标题", "source_path": None, "source_kind": "markdown", "image_paths": [], "word_count": 2, "template_mode": "default", "theme_name": None, "is_config_complete": False}],
                        "failures": [{"source_path": str(source_dir / "broken.bin"), "source_kind": "bin", "error_type": "UnsupportedSourceError", "message": "unsupported"}],
                    },
                    "resources": {"theme_pool": {"count": 3}, "cover_pool": {"count": 14}},
                },
            ), patch(
                "ordo_engine.workbench.preflight.build_publish_matrix",
                return_value={
                    "matrix_path": str(matrix_path),
                    "representative_article_ids": ["a1"],
                    "production_strategy": {"template_mode": "custom", "manual_theme_by_article": {"a1": "alpha"}, "manual_cover_by_article_platform": {"a1:zhihu": "/tmp/c.png"}},
                },
            ), patch(
                "ordo_engine.workbench.preflight.publish.list_tabs_or_none",
                return_value=[{"id": "tab-1"}],
            ), patch(
                "ordo_engine.workbench.preflight.publish.ensure_chrome_ready",
                return_value=([{"id": "tab-1"}], "Google Chrome"),
            ), patch(
                "ordo_engine.workbench.preflight.publish.open_missing_platform_tabs",
                return_value=["zhihu"],
            ), patch(
                "ordo_engine.workbench.preflight.publish.list_tabs",
                return_value=[{"id": "tab-1"}],
            ), patch(
                "ordo_engine.workbench.preflight.publish.bind_workbench",
                return_value={"zhihu": "tab-1"},
            ), patch(
                "ordo_engine.workbench.preflight.publish.get_cdp_connection_metadata",
                return_value={"detail": "cdp ok"},
            ), patch(
                "ordo_engine.workbench.preflight.publish.run_preflight_checks",
                return_value=(["登录失效"], ["头条标签页刚恢复"]),
            ):
                payload = build_real_publish_preflight(
                    base,
                    source_path=str(source_dir),
                    cover_dir=str(cover_dir),
                    platforms=("wechat", "zhihu", "toutiao"),
                    seed=7,
                    report_id="rp-1",
                )

            report_path = Path(payload["report_path"])
            self.assertTrue(report_path.is_file())
            self.assertEqual(payload["import_job"]["source_count"], 3)
            self.assertEqual(payload["import_job"]["failure_count"], 1)
            self.assertEqual(payload["matrix"]["matrix_path"], str(matrix_path))
            self.assertEqual(payload["blockers"], ["登录失效"])
            self.assertEqual(payload["warnings"], ["头条标签页刚恢复"])
            on_disk = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["report_id"], "rp-1")

    def test_build_real_publish_preflight_auto_opens_browser_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source_dir = base / "articles"
            source_dir.mkdir()
            cover_dir = base / "covers"
            cover_dir.mkdir()
            matrix_path = base / ".ordo" / "workbench" / "matrices" / "mx.json"
            matrix_path.parent.mkdir(parents=True, exist_ok=True)
            matrix_path.write_text("{}", encoding="utf-8")

            with patch(
                "ordo_engine.workbench.preflight.import_sources",
                return_value={
                    "job": {"job_id": "import-1", "article_count": 1, "failure_count": 0, "source_count": 1, "manifest_path": str(base / "manifest.json"), "drafts": [], "failures": []},
                    "resources": {},
                },
            ), patch(
                "ordo_engine.workbench.preflight.build_publish_matrix",
                return_value={
                    "matrix_path": str(matrix_path),
                    "representative_article_ids": [],
                    "production_strategy": {"template_mode": "custom", "manual_theme_by_article": {}, "manual_cover_by_article_platform": {}},
                },
            ), patch(
                "ordo_engine.workbench.preflight.publish.ensure_chrome_ready",
                return_value=([{"id": "tab-1"}], "Google Chrome"),
            ) as ensure_ready, patch(
                "ordo_engine.workbench.preflight.publish.open_missing_platform_tabs",
                return_value=["zhihu", "toutiao"],
            ) as open_tabs, patch(
                "ordo_engine.workbench.preflight.publish.list_tabs",
                return_value=[{"id": "tab-1"}],
            ), patch(
                "ordo_engine.workbench.preflight.publish.bind_workbench",
                return_value={"zhihu": "tab-1", "toutiao": "tab-1"},
            ), patch(
                "ordo_engine.workbench.preflight.publish.get_cdp_connection_metadata",
                return_value=None,
            ), patch(
                "ordo_engine.workbench.preflight.publish.run_preflight_checks",
                return_value=([], []),
            ):
                payload = build_real_publish_preflight(
                    base,
                    source_path=str(source_dir),
                    cover_dir=str(cover_dir),
                    platforms=("wechat", "zhihu", "toutiao"),
                    seed=7,
                )

        ensure_ready.assert_called_once()
        open_tabs.assert_called_once()
        self.assertTrue(payload["ready"])


if __name__ == "__main__":
    unittest.main()
