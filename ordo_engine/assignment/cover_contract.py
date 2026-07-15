from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageCms, ImageFilter, ImageOps, ImageStat


COVER_FILENAME = "cover.png"
COVER_SIZE = (2538, 1080)
COVER_MAX_BYTES = 5 * 1024 * 1024
MIN_COVER_EDGE_DETAIL = 4.0
COVER_PLATFORMS = ("wechat", "zhihu", "toutiao", "yidian", "bilibili", "jianshu")


class CoverContractError(ValueError):
    pass


def _srgb_profile_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _unquote(value: str) -> str:
    return value.strip().strip("\"'")


def _parse_cover_frontmatter(article_path: Path) -> tuple[str, dict[str, str]]:
    text = article_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise CoverContractError(f"文章缺少发布包 frontmatter: {article_path.name}")
    end = text.find("\n---", 4)
    if end == -1:
        raise CoverContractError(f"文章 frontmatter 未闭合: {article_path.name}")

    cover = ""
    platform_covers: dict[str, str] = {}
    in_platform_covers = False
    for raw_line in text[4:end].splitlines():
        if raw_line.startswith((" ", "\t")):
            if in_platform_covers and ":" in raw_line:
                key, value = raw_line.strip().split(":", 1)
                platform_covers[key.strip()] = _unquote(value)
            continue
        in_platform_covers = False
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        if key == "cover":
            cover = _unquote(value)
        elif key == "platform_covers":
            in_platform_covers = True
    return cover, platform_covers


def validate_cover(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise CoverContractError(f"封面文件不存在: {candidate}")
    if candidate.name != COVER_FILENAME:
        raise CoverContractError(f"封面文件名必须是 {COVER_FILENAME}: {candidate.name}")
    if candidate.stat().st_size > COVER_MAX_BYTES:
        raise CoverContractError(f"封面文件超过 5 MB: {candidate}")

    try:
        with Image.open(candidate) as image:
            if image.format != "PNG":
                raise CoverContractError(f"封面格式必须是 PNG: {candidate}")
            if image.size != COVER_SIZE:
                raise CoverContractError(
                    f"封面尺寸必须精确为 2538x1080，当前为 {image.width}x{image.height}: {candidate}"
                )
            icc = image.info.get("icc_profile")
            if not icc:
                raise CoverContractError(f"封面必须嵌入 sRGB 色彩配置: {candidate}")
            profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if "srgb" not in ImageCms.getProfileDescription(profile).lower():
                raise CoverContractError(f"封面色彩空间必须是 sRGB: {candidate}")
            inset = image.convert("L").crop((50, 50, image.width - 50, image.height - 50))
            edge_detail = ImageStat.Stat(inset.filter(ImageFilter.FIND_EDGES)).mean[0]
            if edge_detail < MIN_COVER_EDGE_DETAIL:
                raise CoverContractError(f"封面视觉细节不足，疑似占位图: {candidate}")
    except CoverContractError:
        raise
    except Exception as exc:
        raise CoverContractError(f"无法读取封面图片: {candidate}: {exc}") from exc
    return candidate


def resolve_publication_cover(article_path: str | Path) -> Path:
    article = Path(article_path).expanduser().resolve()
    cover_value, platform_covers = _parse_cover_frontmatter(article)
    if not cover_value:
        raise CoverContractError(f"发布包缺少 cover: {article.name}")
    if any(value != cover_value for value in platform_covers.values()):
        raise CoverContractError("所有 platform_covers 必须指向同一张 cover.png")
    missing = [platform for platform in COVER_PLATFORMS if platform_covers.get(platform) != cover_value]
    if missing:
        raise CoverContractError(f"platform_covers 缺少统一平台路径: {', '.join(missing)}")

    raw_cover = Path(cover_value).expanduser()
    cover = raw_cover if raw_cover.is_absolute() else article.parent / raw_cover
    return validate_cover(cover)


def resolve_wechat_cover(article_path: str | Path) -> Path:
    """解析微信封面；浏览器平台封面暂停时不校验 platform_covers。"""
    article = Path(article_path).expanduser().resolve()
    cover_value, _platform_covers = _parse_cover_frontmatter(article)
    if not cover_value:
        raise CoverContractError(f"发布包缺少 cover: {article.name}")
    raw_cover = Path(cover_value).expanduser()
    cover = raw_cover if raw_cover.is_absolute() else article.parent / raw_cover
    return validate_cover(cover)


def normalize_cover_source(source_path: str | Path, output_path: str | Path) -> Path:
    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if output.name != COVER_FILENAME:
        raise CoverContractError(f"封面文件名必须是 {COVER_FILENAME}: {output.name}")

    try:
        with Image.open(source) as image:
            if image.width < COVER_SIZE[0] or image.height < COVER_SIZE[1]:
                raise CoverContractError(
                    f"源图 {image.width}x{image.height} 小于目标 2538x1080，禁止放大"
                )
            normalized = ImageOps.fit(
                image.convert("RGB"),
                COVER_SIZE,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except CoverContractError:
        raise
    except Exception as exc:
        raise CoverContractError(f"无法读取封面源图: {source}: {exc}") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    normalized.save(
        output,
        format="PNG",
        optimize=True,
        compress_level=9,
        icc_profile=_srgb_profile_bytes(),
    )
    return validate_cover(output)
