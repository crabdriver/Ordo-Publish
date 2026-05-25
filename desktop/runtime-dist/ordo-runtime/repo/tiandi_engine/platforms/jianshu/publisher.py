from pathlib import Path

from tiandi_engine.platforms.base import SubprocessPlatformAdapter


class JianshuPlatformAdapter(SubprocessPlatformAdapter):
    def __init__(self, base_dir: Path):
        super().__init__(
            base_dir=base_dir,
            platform="jianshu",
            script_name="jianshu_publisher.py",
            supports_theme=True,
            supports_cover=True,
            supports_template_mode=True,
            supports_article_id=True,
            supports_cover_mode=True,
            supports_ai_declaration_mode=True,
        )

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
        return super().prepare(
            markdown_file=markdown_file,
            mode=mode,
            theme_name=theme_name,
            cover_path=cover_path,
            template_mode=template_mode,
            article_id=article_id,
            cover_mode=cover_mode,
            ai_declaration_mode=ai_declaration_mode,
            scheduled_publish_at=scheduled_publish_at,
        )
