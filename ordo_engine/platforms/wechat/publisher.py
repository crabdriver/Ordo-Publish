from pathlib import Path

from ordo_engine.platforms.base import SubprocessPlatformAdapter


class WeChatPlatformAdapter(SubprocessPlatformAdapter):
    """Run WeChat official-API publisher in current execution environment."""

    def __init__(self, base_dir: Path, executor=None):
        super().__init__(
            base_dir=base_dir,
            platform="wechat",
            script_name="wechat_publisher.py",
            supports_theme=True,
            supports_cover=True,
            supports_cover_mode=True,
            executor=executor,
        )
