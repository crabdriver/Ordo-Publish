import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms

from ordo_engine.assignment.cover_contract import (
    COVER_FILENAME,
    COVER_MAX_BYTES,
    COVER_SIZE,
    CoverContractError,
    normalize_cover_source,
    resolve_publication_cover,
    validate_cover,
)


def srgb_profile_bytes():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def write_png(path: Path, *, size=COVER_SIZE, profile=True):
    kwargs = {"icc_profile": srgb_profile_bytes()} if profile else {}
    Image.new("RGB", size, color=(23, 45, 67)).save(path, format="PNG", **kwargs)


class CoverContractTests(unittest.TestCase):
    def test_valid_cover_matches_canonical_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            cover = Path(tmp) / COVER_FILENAME
            write_png(cover)

            self.assertEqual(validate_cover(cover), cover.resolve())

    def test_rejects_obsolete_and_inexact_dimensions(self):
        for size in ((1200, 510), (2541, 1080)):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as tmp:
                cover = Path(tmp) / COVER_FILENAME
                write_png(cover, size=size)

                with self.assertRaisesRegex(CoverContractError, "2538x1080"):
                    validate_cover(cover)

    def test_rejects_wrong_filename_format_profile_and_file_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            wrong_name = root / "other.png"
            write_png(wrong_name)
            with self.assertRaisesRegex(CoverContractError, "cover.png"):
                validate_cover(wrong_name)

            jpeg = root / COVER_FILENAME
            Image.new("RGB", COVER_SIZE).save(jpeg, format="JPEG")
            with self.assertRaisesRegex(CoverContractError, "PNG"):
                validate_cover(jpeg)

            write_png(jpeg, profile=False)
            with self.assertRaisesRegex(CoverContractError, "sRGB"):
                validate_cover(jpeg)

            jpeg.write_bytes(b"x" * (COVER_MAX_BYTES + 1))
            with self.assertRaisesRegex(CoverContractError, "5 MB"):
                validate_cover(jpeg)

    def test_resolves_one_cover_for_every_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            relative = "assets/article-1/cover.png"
            cover = root / relative
            cover.parent.mkdir(parents=True)
            write_png(cover)
            article = root / "article.md"
            article.write_text(
                "---\n"
                "article_id: article-1\n"
                f"cover: {relative}\n"
                "platform_covers:\n"
                f"  wechat: {relative}\n"
                f"  zhihu: {relative}\n"
                f"  toutiao: {relative}\n"
                f"  yidian: {relative}\n"
                f"  bilibili: {relative}\n"
                f"  jianshu: {relative}\n"
                "---\n\n# Article\n",
                encoding="utf-8",
            )

            self.assertEqual(resolve_publication_cover(article), cover.resolve())

    def test_rejects_mismatched_platform_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            article = root / "article.md"
            article.write_text(
                "---\ncover: assets/a/cover.png\nplatform_covers:\n"
                "  wechat: assets/a/cover.png\n  zhihu: assets/b/cover.png\n---\n# Article\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(CoverContractError, "同一张"):
                resolve_publication_cover(article)

    def test_normalizes_only_high_resolution_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            output = root / COVER_FILENAME
            Image.new("RGB", (3200, 1600), color=(23, 45, 67)).save(source)

            self.assertEqual(normalize_cover_source(source, output), output.resolve())
            self.assertEqual(validate_cover(output), output.resolve())

            small = root / "small.png"
            Image.new("RGB", (1200, 510)).save(small)
            with self.assertRaisesRegex(CoverContractError, "禁止放大"):
                normalize_cover_source(small, output)


if __name__ == "__main__":
    unittest.main()
