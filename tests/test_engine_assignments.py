import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ordo_engine.assignment.covers import (
    COVER_PLATFORMS,
    CoverPoolError,
    assign_covers,
    list_cover_files,
)
from ordo_engine.assignment.templates import assign_templates, scan_theme_pool
from ordo_engine.config import load_engine_config


class ThemePoolScanTests(unittest.TestCase):
    def test_scan_theme_pool_reads_json_stems_and_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha.json").write_text('{"name": "Alpha 名"}', encoding="utf-8")
            (root / "beta.json").write_text("{}", encoding="utf-8")
            pool = scan_theme_pool(root)
            by_id = {e.theme_id: e for e in pool}
            self.assertEqual(set(by_id), {"alpha", "beta"})
            self.assertEqual(by_id["alpha"].display_name, "Alpha 名")
            self.assertEqual(by_id["beta"].display_name, "beta")

    def test_scan_theme_pool_falls_back_to_stem_for_non_utf8_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gbk_theme.json").write_bytes('{"name": "中文"}'.encode("gbk"))
            pool = scan_theme_pool(root)
            by_id = {e.theme_id: e for e in pool}
            self.assertEqual(by_id["gbk_theme"].display_name, "gbk_theme")

    def test_scan_theme_pool_ignores_macos_metadata_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sspai.json").write_text('{"name": "少数派"}', encoding="utf-8")
            (root / "._sspai.json").write_bytes(b"not real json")
            pool = scan_theme_pool(root)
            self.assertEqual([e.theme_id for e in pool], ["sspai"])

    def test_gzh_design_themes_are_registered(self):
        root = Path(__file__).resolve().parents[1] / "themes"
        pool = scan_theme_pool(root)
        ids = {e.theme_id for e in pool}
        self.assertTrue(
            {
                "gzh-moyu-green",
                "gzh-red-white",
                "gzh-graphite-minimal",
                "gzh-zen-whitespace",
                "gzh-moyu-ticket",
                "gzh-olive-journal",
            }.issubset(ids)
        )


class TemplateAssignmentTests(unittest.TestCase):
    def test_default_mode_assigns_theme_per_article(self):
        with tempfile.TemporaryDirectory() as tmp:
            themes = Path(tmp) / "themes"
            themes.mkdir()
            for name in ("t1", "t2", "t3"):
                (themes / f"{name}.json").write_text('{"name": "N"}', encoding="utf-8")
            out = assign_templates(
                ["a", "b", "c"],
                themes_dir=themes,
                assignment_mode="default",
                seed=42,
            )
            self.assertEqual(len(out), 3)
            themes_used = {x.theme_id for x in out}
            self.assertTrue(themes_used.issubset({"t1", "t2", "t3"}))
            self.assertGreater(len(themes_used), 1)
            self.assertTrue(all(x.template_mode == "default" for x in out))
            self.assertFalse(any(x.is_manual_override for x in out))

    def test_default_mode_is_deterministic_with_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            themes = Path(tmp) / "themes"
            themes.mkdir()
            for name in ("t1", "t2"):
                (themes / f"{name}.json").write_text("{}", encoding="utf-8")
            a = assign_templates(["x", "y"], themes_dir=themes, assignment_mode="default", seed=7)
            b = assign_templates(["x", "y"], themes_dir=themes, assignment_mode="default", seed=7)
            self.assertEqual([x.theme_id for x in a], [x.theme_id for x in b])

    def test_custom_mode_respects_manual_map_and_fallback_default_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            themes = Path(tmp) / "themes"
            themes.mkdir()
            for name in ("apple", "zebra"):
                (themes / f"{name}.json").write_text("{}", encoding="utf-8")
            out = assign_templates(
                ["p1", "p2"],
                themes_dir=themes,
                assignment_mode="custom",
                manual_theme_by_article={"p1": "zebra"},
                seed=0,
            )
            by_id = {x.article_id: x for x in out}
            self.assertEqual(by_id["p1"].theme_id, "zebra")
            self.assertTrue(by_id["p1"].is_manual_override)
            self.assertEqual(by_id["p1"].template_mode, "custom")
            # 未映射项回退默认主题：排序后的首个 theme_id
            self.assertEqual(by_id["p2"].theme_id, "apple")
            self.assertFalse(by_id["p2"].is_manual_override)


class CoverAssignmentTests(unittest.TestCase):
    def _write_cover(self, d: Path, name: str):
        return self._write_png_cover(d, name, (1280, 720))

    def _write_png_cover(self, d: Path, name: str, size):
        p = d / name
        Image.new("RGB", size, color=(12, 34, 56)).save(p)
        return p

    def test_assign_covers_is_article_times_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / "covers"
            cdir.mkdir()
            self._write_cover(cdir, "a.png")
            self._write_cover(cdir, "b.png")
            platforms = ["wechat", "zhihu", "toutiao", "jianshu", "bilibili"]
            out = assign_covers(
                ["art1"],
                platforms,
                cover_dir=cdir,
                recent_cover_paths=(),
                repeat_window=0,
                seed=1,
            )
            keys = {(x.article_id, x.platform) for x in out}
            self.assertEqual(
                keys,
                {
                    ("art1", "wechat"),
                    ("art1", "zhihu"),
                    ("art1", "toutiao"),
                    ("art1", "bilibili"),
                },
            )
            self.assertEqual(COVER_PLATFORMS, tuple(sorted(COVER_PLATFORMS)))

    def test_same_article_reuses_one_cover_for_every_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / "covers"
            cdir.mkdir()
            for i in range(4):
                self._write_cover(cdir, f"c{i}.png")
            platforms = ["zhihu", "toutiao", "jianshu", "yidian"]
            out = assign_covers(
                ["only"],
                platforms,
                cover_dir=cdir,
                recent_cover_paths=(),
                repeat_window=0,
                seed=99,
            )
            paths = [x.cover_path for x in out]
            self.assertEqual(len(set(paths)), 1)

    def test_assign_covers_prefers_landscape_cover_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / "covers"
            cdir.mkdir()
            square = self._write_png_cover(cdir, "square.png", (1024, 1024))
            wide = self._write_png_cover(cdir, "wide.png", (1376, 768))
            out = assign_covers(
                ["only"],
                ["zhihu", "toutiao", "yidian", "bilibili"],
                cover_dir=cdir,
                recent_cover_paths=(),
                repeat_window=0,
                seed=1,
            )
            self.assertTrue(out)
            self.assertEqual({x.cover_path for x in out}, {wide})
            self.assertNotIn(square, {x.cover_path for x in out})

    def test_recent_history_avoids_recent_covers_when_possible(self):
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / "covers"
            cdir.mkdir()
            p1 = self._write_cover(cdir, "x.png")
            p2 = self._write_cover(cdir, "y.png")
            recent = (str(p1),)
            out = assign_covers(
                ["a"],
                ["zhihu"],
                cover_dir=cdir,
                recent_cover_paths=recent,
                repeat_window=1,
                seed=0,
            )
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0].cover_path.resolve(), p2.resolve())

    def test_list_cover_files_skips_tiny_placeholder_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / "covers"
            cdir.mkdir()
            self._write_png_cover(cdir, "tiny.png", (1, 1))
            valid = self._write_png_cover(cdir, "valid.png", (1280, 720))

            files = list_cover_files(cdir)

        self.assertEqual([path.resolve() for path in files], [valid.resolve()])

    def test_missing_cover_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            with self.assertRaises(CoverPoolError) as ctx:
                list_cover_files(missing)
            self.assertIn("不存在", str(ctx.exception))

    def test_empty_cover_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / "empty"
            cdir.mkdir()
            with self.assertRaises(CoverPoolError) as ctx:
                list_cover_files(cdir)
            self.assertIn("空", str(ctx.exception))


class EngineConfigDiscoveryTests(unittest.TestCase):
    def test_config_exposes_assignment_fields_and_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "themes").mkdir()
            (base / "themes" / "z.json").write_text('{"name": "Z"}', encoding="utf-8")
            cfg = load_engine_config(base)
            self.assertEqual(cfg.get_default_template_mode(), "default")
            self.assertEqual(cfg.get_cover_repeat_window(), 8)
            themes = cfg.discover_theme_pool()
            self.assertEqual(themes["count"], 1)
            self.assertEqual(themes["theme_ids"], ["z"])
            cov = cfg.discover_cover_pool()
            self.assertFalse(cov["ok"])
            self.assertIn("error", cov)


if __name__ == "__main__":
    unittest.main()
