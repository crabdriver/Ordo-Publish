from __future__ import annotations

import os
import random
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

# Try patchright first, fall back to playwright
try:
    from patchright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
except ImportError:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright


PLATFORM_URL_PATTERNS: Dict[str, List[str]] = {
    "zhihu": ["zhihu.com"],
    "toutiao": ["mp.toutiao.com"],
    "jianshu": ["jianshu.com/writer"],
    "yidian": ["mp.yidianzixun.com"],
    "bilibili": ["member.bilibili.com"],
}

PLATFORM_EDITOR_URLS: Dict[str, str] = {
    "zhihu": "https://zhuanlan.zhihu.com/write",
    "toutiao": "https://mp.toutiao.com/profile_v4/graphic/publish",
    "jianshu": "https://www.jianshu.com/writer#/",
    "yidian": "https://mp.yidianzixun.com/#/Writing/articleEditor",
    "bilibili": "https://member.bilibili.com/platform/upload/text/new-edit",
}

# System Chrome path (macOS)
SYSTEM_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Anti-detection: remove automation flags that platforms can detect
_ANTI_DETECT_ARGS = [
    "--disable-blink-features=AutomationControlled",
]

# Memory-optimization flags for standalone mode
_HEADLESS_CHROME_ARGS = [
    *_ANTI_DETECT_ARGS,
    "--disable-extensions",
    "--disable-plugins",
    "--disable-sync",
    "--disable-translate",
    "--no-first-run",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--mute-audio",
    "--no-default-browser-check",
]

# Anti-detection script: injected on every page load to hide automation signals.
# 注意：patchright 已原生处理 webdriver / plugins / chrome.runtime 等指纹，
# 这里只做「不与之冲突」的最小补充，避免旧代码里 `navigator.plugins=[1,2,3,4,5]`
# 这种反而更易被识别为伪造的写法。
_ANTI_DETECT_SCRIPT = """
// 隐藏 webdriver 标记（patchright 也会处理，这里双保险且写法规范）
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
// 仅补充语言字段，不覆盖 plugins（交给 patchright 原生处理）
try {
  Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
} catch (e) {}
"""


class PlaywrightEngine:
    """管理 Playwright 浏览器连接（standalone 模式）

    启动独立的无头 Chrome 实例（完全隔离，不影响用户浏览器）。

    standalone 模式的优势：
    - 完全隔离：独立进程、独立内存，不影响用户日常浏览
    - 无头运行：省 30-40% 内存，后台静默执行
    - 用完即关：发布完成后关闭浏览器，释放所有内存
    - 登录持久化：独立 profile 保存登录态，无需每次扫码
    - 不依赖用户 Chrome：用户可以正常开关自己的 Chrome
    """

    def __init__(
        self,
        mode: str = "standalone",
        debug_port: int = 9333,
        base_dir: Optional[Path] = None,
        headless: Optional[bool] = None,
        profile_dir: Optional[Path] = None,
        executable_path: Optional[str] = None,
    ):
        # cdp 旧路径已移除，仅保留 standalone
        self.mode = "standalone"
        self.debug_port = debug_port
        self.base_dir = base_dir or Path(__file__).resolve().parents[3]
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None  # standalone 模式的 context

        # Standalone 模式参数
        if headless is None:
            # 默认无头；首次登录（无 profile）时自动切有头
            headless = True
        self.headless = headless

        # Profile 目录：保存登录态
        self.profile_dir = profile_dir or (self.base_dir / ".ordo" / "automation-profile")

        # Chrome 路径：优先系统 Chrome，回退到 Playwright 自带的 Chromium
        self.executable_path = executable_path or (
            SYSTEM_CHROME if os.path.exists(SYSTEM_CHROME) else None
        )

    @property
    def _is_standalone(self) -> bool:
        return self.mode == "standalone"

    @property
    def _has_existing_profile(self) -> bool:
        """检查是否已有登录态（profile 目录非空）"""
        if not self.profile_dir.exists():
            return False
        return any(self.profile_dir.iterdir())

    def connect(self):
        """启动独立浏览器"""
        self._launch_standalone()

    def _cleanup_stale_lock(self):
        """清理孤儿 SingletonLock：若锁文件存在但没有任何 Chrome 进程持有它，
        说明上次运行硬崩溃遗留，必须清理否则后续 launch 会全部失败（雪崩）。"""
        lock = self.profile_dir / "SingletonLock"
        if not lock.exists():
            return
        try:
            # 检查是否有 Chrome 进程在使用这个 profile
            out = subprocess.run(
                ["pgrep", "-f", f"user-data-dir={self.profile_dir}"],
                capture_output=True, text=True, timeout=10,
            )
            if out.stdout.strip():
                # 仍有进程持有，不清理（避免误杀正在运行的浏览器）
                return
        except Exception:
            return
        # 无进程持有 → 孤儿锁，清理
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (self.profile_dir / name).unlink(missing_ok=True)
            except Exception:
                pass
        print("[engine] 检测到孤儿锁（上次异常退出遗留），已清理")

    def _random_viewport(self) -> dict:
        """在几组常见真实分辨率间随机选择，避免固定视口形成可识别的行为画像。"""
        viewports = [
            {"width": 1280, "height": 800},
            {"width": 1366, "height": 768},
            {"width": 1440, "height": 900},
            {"width": 1536, "height": 864},
            {"width": 1920, "height": 1080},
        ]
        return random.choice(viewports)

    def _launch_standalone(self):
        """Standalone 模式：启动独立的无头 Chrome 实例"""
        # 确保 profile 目录存在
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # 清理可能遗留的孤儿锁，避免后续平台全部 launch 失败
        self._cleanup_stale_lock()

        # 如果无头模式但还没有登录态，自动切有头模式（首次登录需要扫码）
        effective_headless = self.headless
        if not effective_headless and not self._has_existing_profile:
            # 用户明确要 headed，或者首次登录 → headed
            pass
        elif effective_headless and not self._has_existing_profile:
            # 无头模式但没登录态 → 自动切有头，让用户扫码
            print("[engine] 首次使用检测到无登录态，切换为有头模式以便扫码登录")
            effective_headless = False

        launch_kwargs = {
            "user_data_dir": str(self.profile_dir),
            "headless": effective_headless,
            "args": _HEADLESS_CHROME_ARGS,
            "viewport": self._random_viewport(),
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
        }
        if self.executable_path:
            launch_kwargs["executable_path"] = self.executable_path

        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                **launch_kwargs
            )
            # 注入反检测脚本：隐藏 navigator.webdriver 等自动化特征
            try:
                self._context.add_init_script(_ANTI_DETECT_SCRIPT)
            except Exception:
                pass
            # launch_persistent_context 返回的是 BrowserContext，不是 Browser
            self._browser = None  # standalone 模式不用 _browser
        except Exception as exc:
            self._playwright.stop()
            self._playwright = None
            raise RuntimeError(
                f"无法启动独立浏览器。profile_dir={self.profile_dir}\n"
                f"错误: {exc}"
            ) from exc

        mode_label = "无头" if effective_headless else "有头"
        print(f"[engine] 独立浏览器已启动 ({mode_label}模式, profile={self.profile_dir.name})")

    @property
    def browser(self) -> Browser:
        """兼容旧接口：返回 browser 或 context"""
        if self._is_standalone:
            if self._context is None:
                raise RuntimeError("PlaywrightEngine 未启动，请先调用 connect()")
            return self._context
        if self._browser is None:
            raise RuntimeError("PlaywrightEngine 未连接，请先调用 connect()")
        return self._browser

    @property
    def context(self) -> BrowserContext:
        """获取 BrowserContext（standalone 模式下直接返回，cdp 模式取第一个）"""
        if self._is_standalone:
            if self._context is None:
                raise RuntimeError("PlaywrightEngine 未启动，请先调用 connect()")
            return self._context
        # CDP 模式：返回第一个 context
        if self._browser and self._browser.contexts:
            return self._browser.contexts[0]
        raise RuntimeError("无可用 BrowserContext")

    def get_page_for_platform(self, platform: str) -> Page:
        """查找已有平台 tab 或创建新 tab"""
        patterns = PLATFORM_URL_PATTERNS.get(platform, [])
        editor_url = PLATFORM_EDITOR_URLS.get(platform)

        # 获取搜索范围
        if self._is_standalone:
            contexts = [self._context] if self._context else []
        else:
            contexts = self.browser.contexts if self._browser else []

        # Search existing pages
        for ctx in contexts:
            for page in ctx.pages:
                page_url = page.url or ""
                if any(pattern in page_url for pattern in patterns):
                    return page

        # No existing tab found — open new one
        if not editor_url:
            raise RuntimeError(f"未知平台: {platform}")

        if self._is_standalone:
            page = self._context.new_page()
        else:
            ctx = contexts[0] if contexts else self.browser.new_context()
            page = ctx.new_page()

        page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
        return page

    def screenshot(self, page: Page, platform: str, step: str) -> Optional[Path]:
        """保存当前页面截图到 .ordo/screenshots/{platform}/"""
        try:
            screenshot_dir = self.base_dir / ".ordo" / "screenshots" / platform
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = int(time.time() * 1000)
            filepath = screenshot_dir / f"{timestamp}_{step}.png"
            page.screenshot(path=str(filepath))
            return filepath
        except Exception:
            return None

    def close(self):
        """关闭浏览器连接

        standalone 模式：关闭浏览器并释放所有内存
        cdp 模式：仅断开连接（不关闭用户的浏览器）
        """
        if self._is_standalone:
            # 关闭所有页面
            if self._context:
                try:
                    # 关闭 context 会关闭所有页面并释放浏览器进程
                    self._context.close()
                except Exception:
                    pass
                self._context = None
        else:
            # CDP 模式：不关闭浏览器，只断开连接
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        if self._is_standalone:
            print("[engine] 独立浏览器已关闭，内存已释放")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()
