from pathlib import Path

from .jianshu.publisher import JianshuPlatformAdapter
from .toutiao.publisher import ToutiaoPlatformAdapter
from .wechat.publisher import WeChatPlatformAdapter
from .yidian.publisher import YidianPlatformAdapter
from .zhihu.publisher import ZhihuPlatformAdapter


def build_platform_registry(base_dir: Path):
    base_path = Path(base_dir)
    return {
        "wechat": WeChatPlatformAdapter(base_path),
        "zhihu": ZhihuPlatformAdapter(base_path),
        "toutiao": ToutiaoPlatformAdapter(base_path),
        "jianshu": JianshuPlatformAdapter(base_path),
        "yidian": YidianPlatformAdapter(base_path),
    }
