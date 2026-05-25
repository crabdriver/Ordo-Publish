import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tiandi_engine.models.workbench import ArticleDraft
from tiandi_engine.workbench.matrix import build_publish_matrix, select_representative_drafts


class PublishMatrixTests(unittest.TestCase):
    def _draft(self, article_id: str, source_kind: str, word_count: int = 10) -> ArticleDraft:
        return ArticleDraft(
            article_id=article_id,
            title=article_id,
            body_markdown=f"# {article_id}\n\n正文",
            source_path=Path(f"/tmp/{article_id}"),
            source_kind=source_kind,
            word_count=word_count,
        )

    def _write_cover(self, root: Path, name: str):
        Image.new("RGB", (1280, 720), color=(12, 34, 56)).save(root / name)

    def test_select_representative_drafts_prefers_source_kind_diversity(self):
        drafts = (
            self._draft("a", "docx"),
            self._draft("b", "docx"),
            self._draft("c", "pdf"),
            self._draft("d", "image"),
            self._draft("e", "markdown"),
            self._draft("f", "txt"),
        )

        selected = select_representative_drafts(drafts, max_count=4)

        self.assertEqual([item.article_id for item in selected], ["a", "c", "d", "e"])

    def test_build_publish_matrix_writes_matrix_file_and_fixed_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "themes").mkdir()
            for name in ("alpha", "beta", "gamma"):
                (base / "themes" / f"{name}.json").write_text('{"name": "Theme"}', encoding="utf-8")
            cover_dir = base / "covers"
            cover_dir.mkdir()
            for index in range(1, 5):
                self._write_cover(cover_dir, f"cover_{index:02d}.png")

            drafts = (
                self._draft("art-1", "docx"),
                self._draft("art-2", "pdf"),
                self._draft("art-3", "image"),
                self._draft("art-4", "markdown"),
            )

            payload = build_publish_matrix(
                base,
                drafts=drafts,
                platforms=("wechat", "zhihu", "toutiao", "yidian", "jianshu"),
                seed=7,
                matrix_id="mx-1",
            )

            matrix_path = Path(payload["matrix_path"])
            self.assertTrue(matrix_path.is_file())
            self.assertEqual(payload["representative_article_ids"], ["art-1", "art-2", "art-3", "art-4"])
            self.assertEqual(payload["production_strategy"]["template_mode"], "custom")
            self.assertEqual(set(payload["production_strategy"]["manual_theme_by_article"]), {"art-1", "art-2", "art-3", "art-4"})
            self.assertTrue(payload["production_strategy"]["manual_cover_by_article_platform"])
            self.assertIn("art-1:zhihu", payload["production_strategy"]["manual_cover_by_article_platform"])
            self.assertEqual(payload["cover_matrix"]["missing_cover_case"]["article_id"], "art-1")
            on_disk = json.loads(matrix_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["matrix_id"], "mx-1")

    def test_build_publish_matrix_accepts_serialized_draft_dicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "themes").mkdir()
            (base / "themes" / "alpha.json").write_text('{"name": "Theme"}', encoding="utf-8")
            cover_dir = base / "covers"
            cover_dir.mkdir()
            self._write_cover(cover_dir, "cover_01.png")

            drafts = [self._draft("art-1", "markdown").to_dict()]

            payload = build_publish_matrix(
                base,
                drafts=drafts,
                platforms=("wechat", "zhihu"),
                seed=7,
                matrix_id="mx-dict",
            )

        self.assertEqual(payload["representative_article_ids"], ["art-1"])


if __name__ == "__main__":
    unittest.main()
