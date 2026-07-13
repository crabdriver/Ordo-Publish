import sys
from pathlib import Path
from ordo_engine.config import load_json_config

from .wechat.publisher import WeChatPlatformAdapter

# 旧 CDP 平台实现（ordo_engine/platforms/<platform>/）已废弃并删除，
# 现在浏览器平台统一走 playwright standalone 一套。
# 注意：微信公众号仍使用官方 API 适配器（ordo_engine/platforms/wechat/）。


def _resolve_engine_type(base_path: Path) -> str:
    """从 CLI 参数或 config.json 解析引擎类型（默认 standalone）"""
    if "--engine" in sys.argv:
        try:
            idx = sys.argv.index("--engine")
            if idx + 1 < len(sys.argv):
                return sys.argv[idx + 1]
        except ValueError:
            pass
    config, _ = load_json_config(base_path)
    return config.get("engine", "standalone")


def _is_headless() -> bool:
    """从 CLI 参数或环境变量判断是否无头模式"""
    if "--headed" in sys.argv:
        return False
    if "--headless" in sys.argv:
        return True
    return True  # 默认无头


def build_platform_registry(base_dir: Path):
    """构建平台注册表。

    所有浏览器平台（知乎/头条/简书/一点/B站）统一使用 Playwright standalone 适配器；
    微信公众号使用官方 API 适配器。
    """
    base_path = Path(base_dir)

    from .playwright.adapters import PlaywrightPlatformAdapter
    from .playwright_zhihu.publisher import ZhihuPlaywrightPublisher
    from .playwright_toutiao.publisher import ToutiaoPlaywrightPublisher
    from .playwright_jianshu.publisher import JianshuPlaywrightPublisher
    from .playwright_yidian.publisher import YidianPlaywrightPublisher
    from .playwright_bilibili.publisher import BilibiliPlaywrightPublisher

    engine_type = _resolve_engine_type(base_path)
    if engine_type not in ("playwright", "standalone"):
        print(f"[WARN] 未知 engine 类型 '{engine_type}'，回退为 standalone")
    headless = _is_headless()
    engine_mode = "standalone"  # cdp 旧路径已移除

    wechat_adapter = WeChatPlatformAdapter(base_path)

    return {
        "wechat": wechat_adapter,
        "zhihu": PlaywrightPlatformAdapter(
            base_path, "zhihu", ZhihuPlaywrightPublisher,
            engine_mode=engine_mode, headless=headless,
        ),
        "toutiao": PlaywrightPlatformAdapter(
            base_path, "toutiao", ToutiaoPlaywrightPublisher,
            engine_mode=engine_mode, headless=headless,
        ),
        "jianshu": PlaywrightPlatformAdapter(
            base_path, "jianshu", JianshuPlaywrightPublisher,
            engine_mode=engine_mode, headless=headless,
        ),
        "yidian": PlaywrightPlatformAdapter(
            base_path, "yidian", YidianPlaywrightPublisher,
            engine_mode=engine_mode, headless=headless,
        ),
        "bilibili": PlaywrightPlatformAdapter(
            base_path, "bilibili", BilibiliPlaywrightPublisher,
            engine_mode=engine_mode, headless=headless,
        ),
    }
