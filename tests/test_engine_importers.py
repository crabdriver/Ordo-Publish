import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from tiandi_engine.importers.sources import (
    UnsupportedSourceError,
    import_file,
    import_pasted_text,
    list_import_candidates,
)
from tiandi_engine.models.workbench import (
    ArticleDraft,
    CoverAssignment,
    ImportFailure,
    ImportJob,
    PublishJob,
    TemplateAssignment,
)


def write_minimal_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Title"/></w:pPr>
      <w:r><w:t>文档标题</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>第一段。</w:t></w:r></w:p>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
      <w:r><w:t>列表项一</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>第二段。</w:t></w:r></w:p>
  </w:body>
</w:document>
""",
        )


class TestMarkdownImport(unittest.TestCase):
    def test_markdown_single_file_uses_first_h1_as_title(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ignored_name.md"
            path.write_text("# 13-01_主标题\n\n正文段落。", encoding="utf-8")
            draft = import_file(path)
        self.assertEqual(draft.title, "主标题")
        self.assertIn("# 13-01_主标题", draft.body_markdown)
        self.assertIn("正文段落", draft.body_markdown)
        self.assertEqual(draft.source_kind, "markdown")
        self.assertEqual(draft.word_count, len("正文段落。"))

    def test_markdown_falls_back_to_filename_without_h1(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "13-01_stem_only.md"
            path.write_text("无井号标题\n\n内容。", encoding="utf-8")
            draft = import_file(path)
        self.assertEqual(draft.title, "stem_only")
        self.assertEqual(draft.word_count, len("无井号标题内容。"))

    def test_markdown_empty_h1_falls_back_to_filename_stem(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fallback_name.md"
            path.write_text("# \n\n正文甲乙。", encoding="utf-8")
            draft = import_file(path)
            self.assertEqual(draft.title, "fallback_name")
            self.assertEqual(draft.word_count, len("正文甲乙。"))


class TestDirectoryFilter(unittest.TestCase):
    def test_directory_lists_supported_candidates_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "z.md").write_text("# z", encoding="utf-8")
            (root / "a.txt").write_text("t\n\nb", encoding="utf-8")
            (root / "m.docx").write_bytes(b"PK\x03\x04")  # not valid zip; listing only
            (root / "skip.pdf").write_bytes(b"")
            (root / "x.png").write_bytes(b"")
            (root / "nested").mkdir()
            (root / "nested" / "inner.md").write_text("# n", encoding="utf-8")
            candidates = list_import_candidates(root)
            names = [p.name for p in candidates]
            self.assertEqual(names, ["a.txt", "m.docx", "skip.pdf", "x.png", "z.md"])

    def test_directory_lists_pdf_png_and_extensionless_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "z.md").write_text("# z", encoding="utf-8")
            (root / "a.txt").write_text("t\n\nb", encoding="utf-8")
            (root / "m.docx").write_bytes(b"PK\x03\x04")
            (root / "p.pdf").write_bytes(b"%PDF-1.4")
            Image.new("RGB", (32, 32), color=(12, 34, 56)).save(root / "i.png")
            (root / "plain").write_text("标题\n\n正文", encoding="utf-8")
            (root / "skip.bin").write_bytes(b"\x00\x01\x02")
            candidates = list_import_candidates(root)
            names = [p.name for p in candidates]
            self.assertEqual(names, ["a.txt", "i.png", "m.docx", "p.pdf", "plain", "z.md"])


class TestTxtNormalization(unittest.TestCase):
    def test_txt_first_nonempty_line_is_title_rest_markdown_paragraphs(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "note.txt"
            path.write_text("\n\n13-02_标题行\n\n第一段。\n\n第二段。\n", encoding="utf-8")
            draft = import_file(path)
            self.assertEqual(draft.title, "标题行")
            self.assertEqual(draft.source_kind, "txt")
            self.assertIn("第一段", draft.body_markdown)
            self.assertIn("第二段", draft.body_markdown)
            self.assertIn("\n\n", draft.body_markdown)
            self.assertEqual(draft.word_count, len("第一段。第二段。"))

    def test_extensionless_utf8_file_imports_as_plain_text(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "plain"
            path.write_text("标题行\n\n第一段。\n\n第二段。", encoding="utf-8")
            draft = import_file(path)
            self.assertEqual(draft.title, "标题行")
            self.assertEqual(draft.source_kind, "text")
            self.assertIn("第一段。", draft.body_markdown)
            self.assertIn("第二段。", draft.body_markdown)

    def test_extensionless_binary_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "blob"
            path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
            with self.assertRaises(UnsupportedSourceError):
                import_file(path)


class TestPasteImport(unittest.TestCase):
    def test_paste_text_import_produces_draft_with_source_paste(self):
        draft = import_pasted_text("  粘贴标题  \n\n正文一块。\n")
        self.assertEqual(draft.source_kind, "paste")
        self.assertEqual(draft.title, "粘贴标题")
        self.assertIn("正文一块", draft.body_markdown)
        self.assertIsNone(draft.source_path)
        self.assertEqual(draft.word_count, len("正文一块。"))

    def test_paste_text_with_empty_markdown_h1_falls_back_to_untitled(self):
        draft = import_pasted_text("# \n\n正文甲乙。")
        self.assertEqual(draft.title, "Untitled")
        self.assertEqual(draft.body_markdown, "正文甲乙。")
        self.assertEqual(draft.word_count, len("正文甲乙。"))


class TestDocxImport(unittest.TestCase):
    def test_docx_import_extracts_title_paragraphs_and_lists(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x.docx"
            write_minimal_docx(path)
            draft = import_file(path)

        self.assertEqual(draft.source_kind, "docx")
        self.assertEqual(draft.title, "文档标题")
        self.assertIn("第一段。", draft.body_markdown)
        self.assertIn("- 列表项一", draft.body_markdown)
        self.assertIn("第二段。", draft.body_markdown)
        self.assertGreater(draft.word_count, 0)


class TestPdfAndImageImport(unittest.TestCase):
    def test_pdf_import_uses_extracted_text_content(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.pdf"
            path.write_bytes(b"%PDF-1.4")
            with patch(
                "tiandi_engine.importers.sources._extract_pdf_text_content",
                return_value="PDF标题\n\nPDF正文第一段。\n\nPDF正文第二段。",
            ):
                draft = import_file(path)

        self.assertEqual(draft.source_kind, "pdf")
        self.assertEqual(draft.title, "PDF标题")
        self.assertIn("PDF正文第一段。", draft.body_markdown)
        self.assertIn("PDF正文第二段。", draft.body_markdown)

    def test_png_import_uses_ocr_text_content(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "scan.png"
            Image.new("RGB", (240, 120), color=(255, 255, 255)).save(path)
            with patch(
                "tiandi_engine.importers.sources._extract_image_text_content",
                return_value="图片标题\n\nOCR 正文第一段。\n\nOCR 正文第二段。",
            ):
                draft = import_file(path)

        self.assertEqual(draft.source_kind, "image")
        self.assertEqual(draft.title, "图片标题")
        self.assertIn("OCR 正文第一段。", draft.body_markdown)
        self.assertIn("OCR 正文第二段。", draft.body_markdown)


class TestWorkbenchToDict(unittest.TestCase):
    def test_article_draft_to_dict_serializes_paths_and_tuples(self):
        p = Path("/tmp/example.md")
        draft = ArticleDraft(
            article_id="a1",
            title="T",
            body_markdown="body",
            source_path=p,
            source_kind="markdown",
            image_paths=(Path("/tmp/i.png"),),
            word_count=4,
            template_mode="default",
            theme_name="midnight",
            is_config_complete=True,
        )
        d = draft.to_dict()
        self.assertEqual(d["article_id"], "a1")
        self.assertEqual(d["source_path"], "/tmp/example.md")
        self.assertEqual(d["image_paths"], ["/tmp/i.png"])
        self.assertEqual(d["word_count"], 4)
        self.assertEqual(d["template_mode"], "default")
        self.assertTrue(d["is_config_complete"])

    def test_import_job_and_nested_to_dict(self):
        draft = ArticleDraft(
            article_id="x",
            title="t",
            body_markdown="b",
            source_path=None,
            source_kind="paste",
            image_paths=(),
            word_count=1,
            template_mode="default",
            theme_name=None,
            is_config_complete=False,
        )
        job = ImportJob(
            job_id="ij1",
            import_mode="paste",
            source_path=None,
            pasted_preview="hi",
            imported_at="2026-01-01T00:00:00",
            source_count=2,
            manifest_path=Path("/tmp/import.json"),
            failures=(
                ImportFailure(
                    source_path=Path("/tmp/bad.pdf"),
                    source_kind="pdf",
                    error_type="UnsupportedSourceError",
                    message="pdf has no extractable text",
                ),
            ),
            drafts=(draft,),
        )
        payload = job.to_dict()
        self.assertEqual(payload["job_id"], "ij1")
        self.assertEqual(payload["import_mode"], "paste")
        self.assertEqual(payload["source_count"], 2)
        self.assertEqual(payload["manifest_path"], "/tmp/import.json")
        self.assertEqual(len(payload["drafts"]), 1)
        self.assertEqual(payload["failure_count"], 1)
        self.assertEqual(payload["failures"][0]["source_kind"], "pdf")
        self.assertEqual(payload["drafts"][0]["title"], "t")

    def test_template_cover_publish_to_dict(self):
        ta = TemplateAssignment(
            article_id="a",
            template_mode="custom",
            theme_id="t1",
            theme_name="n",
            is_random=False,
            is_manual_override=True,
            is_confirmed=False,
        )
        ca = CoverAssignment(
            article_id="a",
            platform="zhihu",
            cover_path=Path("/c.jpg"),
            cover_source="pool",
            is_random=True,
            is_manual_override=False,
        )
        pj = PublishJob(
            job_id="p1",
            article_ids=("a",),
            platforms=("zhihu",),
            status="running",
            current_step="zhihu:login",
            success_count=0,
            failure_count=0,
            skip_count=0,
            recoverable=True,
            error_summary="",
            scheduled_publish_at="2026-03-30T09:30",
        )
        self.assertFalse(ta.to_dict()["is_confirmed"])
        self.assertEqual(ca.to_dict()["platform"], "zhihu")
        self.assertEqual(pj.to_dict()["current_step"], "zhihu:login")
        self.assertTrue(pj.to_dict()["recoverable"])
        self.assertEqual(pj.to_dict()["scheduled_publish_at"], "2026-03-30T09:30")


if __name__ == "__main__":
    unittest.main()
