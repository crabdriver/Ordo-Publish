from pathlib import Path

from tiandi_engine.platforms.base import SubprocessPlatformAdapter


class WeChatPlatformAdapter(SubprocessPlatformAdapter):
    def __init__(self, base_dir: Path):
        super().__init__(
            base_dir=base_dir,
            platform="wechat",
            script_name="wechat_publisher.py",
            supports_theme=True,
        )
