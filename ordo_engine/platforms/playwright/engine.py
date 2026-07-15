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

PROFILE_MARKER_NAME = ".ordo-profile-initialized"
PROFILE_MARKER_CONTENT = "ordo-automation-profile-v1\n"

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


class BrowserCleanupError(RuntimeError):
    pass


class BrowserOwnershipError(RuntimeError):
    """无法唯一识别 owned 浏览器进程。"""
    pass


class BrowserProfileBusyError(RuntimeError):
    """profile 正被其他进程使用。"""
    pass


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
        self.base_dir = Path(
            base_dir or Path(__file__).resolve().parents[3]
        ).resolve()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None  # standalone 模式的 context
        self._platform_pages: Dict[str, Page] = {}

        # Standalone 模式参数
        if headless is None:
            # 默认无头；首次登录只能通过显式 bootstrap 完成。
            headless = True
        self.headless = headless

        # Profile 目录：保存登录态
        expected_profile_dir = self.base_dir / ".ordo" / "automation-profile"
        if (
            profile_dir is not None
            and Path(profile_dir).resolve() != expected_profile_dir.resolve()
        ):
            raise ValueError(f"浏览器 profile 必须位于仓库内: {expected_profile_dir}")
        self.profile_dir = expected_profile_dir

        # Chrome 路径：优先系统 Chrome，回退到 Playwright 自带的 Chromium
        self.executable_path = executable_path or (
            SYSTEM_CHROME if os.path.exists(SYSTEM_CHROME) else None
        )

        # Owned-process 追踪
        self._owned_pid: Optional[int] = None
        self._owned_start_time: Optional[str] = None

    @property
    def _is_standalone(self) -> bool:
        return self.mode == "standalone"

    @property
    def profile_is_initialized(self) -> bool:
        """只接受显式 bootstrap 写入的版本化标志。"""
        marker = self._validate_profile_paths()
        if not marker.is_file():
            return False
        try:
            return marker.read_text(encoding="utf-8") == PROFILE_MARKER_CONTENT
        except OSError:
            return False

    @property
    def _has_existing_profile(self) -> bool:
        return self.profile_is_initialized

    def mark_profile_initialized(self) -> None:
        marker = self._validate_profile_paths()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        marker = self._validate_profile_paths()
        marker.write_text(
            PROFILE_MARKER_CONTENT,
            encoding="utf-8",
        )

    def _validate_profile_paths(self) -> Path:
        base_dir = self.base_dir.resolve()
        ordo_dir = base_dir / ".ordo"
        profile_dir = ordo_dir / "automation-profile"
        marker = profile_dir / PROFILE_MARKER_NAME
        for path in (ordo_dir, profile_dir, marker):
            if path.is_symlink():
                raise RuntimeError(f"浏览器 profile 路径禁止 symlink: {path}")
        try:
            profile_dir.resolve(strict=False).relative_to(base_dir)
        except ValueError as exc:
            raise RuntimeError(f"浏览器 profile 逃逸仓库: {profile_dir}") from exc
        return marker

    def connect(self):
        """启动独立浏览器"""
        self._launch_standalone()

    def _find_profile_processes(self) -> list[dict]:
        """通过完整命令行 `--user-data-dir=<profile>` 查找使用本 profile 的进程。

        返回 list[dict]，每项含 pid, start_time, args。
        无法唯一识别 → BrowserOwnershipError。
        禁止按 Chrome 名称清理。
        """
        profile_str = str(self.profile_dir.resolve())
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid,lstart,args"],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            return []
        except Exception:
            return []

        if result.returncode != 0:
            return []  # sandbox/权限限制

        processes = []
        for line in result.stdout.splitlines():
            if f"--user-data-dir={profile_str}" in line:
                parts = line.strip().split(None, 2)
                if len(parts) >= 3:
                    try:
                        pid = int(parts[0])
                    except ValueError:
                        continue
                    processes.append({
                        "pid": pid,
                        "start_time": parts[1].strip(),
                        "args": parts[2],
                    })

        return processes

    def _cleanup_stale_lock(self):
        """安全清理 SingletonLock。

        规则：
        - 锁存在且精确 profile 进程仍存活 → BrowserProfileBusyError，停止。
        - 锁存在但没有任何进程使用该 profile → 允许删除孤儿锁。
        - 禁止仅凭文件年龄判断锁已经失效。
        """
        lock = self.profile_dir / "SingletonLock"
        if not lock.is_symlink():
            return

        # 检查是否有存活进程使用这个 profile
        processes = self._find_profile_processes()
        if processes:
            raise BrowserProfileBusyError(
                f"profile 正被进程使用: PID={processes[0]['pid']} "
                f"profile={self.profile_dir}"
            )

        # 无存活进程 → 安全删除孤儿锁
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (self.profile_dir / name).unlink(missing_ok=True)
            except Exception:
                pass
        print("[engine] 检测到孤儿锁（无存活进程使用 profile），已清理")

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
        self._validate_profile_paths()
        if self.headless and not self.profile_is_initialized:
            raise RuntimeError(
                "自动发布 profile 尚未初始化；请先运行 publish.py --bootstrap-browser"
            )

        # 确保 profile 目录存在
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # 清理可能遗留的孤儿锁，避免后续平台全部 launch 失败
        self._cleanup_stale_lock()

        effective_headless = self.headless

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

        # 识别 owned 进程：通过 --user-data-dir 命令行
        self._capture_ownership()

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
                    self._platform_pages[platform] = page
                    return page

        # No existing tab found — open new one
        if not editor_url:
            raise RuntimeError(f"未知平台: {platform}")

        if self._is_standalone:
            page = self._context.new_page()
        else:
            ctx = contexts[0] if contexts else self.browser.new_context()
            page = ctx.new_page()

        # goto/登录/选择器可抛错；必须先登记，确保 adapter finally 能释放。
        self._platform_pages[platform] = page
        page.goto(editor_url, wait_until="domcontentloaded", timeout=30000)
        return page

    def release_page_for_platform(self, platform: str) -> Optional[Page]:
        """释放平台 page lease；关闭失败时保留 lease 并向上抛错。"""
        page = self._platform_pages.get(platform)
        if page is not None:
            page.close()
            if self._platform_pages.get(platform) is page:
                self._platform_pages.pop(platform, None)
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

    def _capture_ownership(self):
        """捕获 owned 浏览器根进程的 PID 和启动时间。

        headed 模式（bootstrap/手动登录）：跳过严格验证。
        无法通过 ps 识别时记录告警但不阻断。
        """
        if not self.headless:
            print("[engine] headed 模式跳过进程所有权验证")
            return

        processes = self._find_profile_processes()
        if not processes:
            print("[engine] 无法通过 ps 识别浏览器进程（sandbox/权限限制），跳过所有权验证")
            return
        if len(processes) > 1:
            raise BrowserOwnershipError(
                f"多个进程使用同一 profile ({self.profile_dir}): "
                f"PIDs={[p['pid'] for p in processes]}"
            )
        proc = processes[0]
        self._owned_pid = proc["pid"]
        self._owned_start_time = proc["start_time"]
        print(f"[engine] owned PID={self._owned_pid}")

    def verify_cleanup(self) -> dict:
        """验证 owned 浏览器已彻底退出。

        检查：
        1. owned PID 已退出
        2. 无其他进程使用本 profile（SingletonLock 已释放或可安全删除）
        3. 返回验证结果 dict

        即使 close() 抛异常，也必须执行此验证。
        """
        result = {"ok": True, "pid_exited": True, "profile_free": True, "details": []}

        # 1. 验证 owned PID 退出
        if self._owned_pid is not None:
            try:
                os.kill(self._owned_pid, 0)
                result["pid_exited"] = False
                result["ok"] = False
                result["details"].append(f"PID {self._owned_pid} 仍存活")
            except ProcessLookupError:
                pass
            except (PermissionError, OSError):
                result["details"].append(f"无法确认 PID {self._owned_pid} 状态")

        # 2. 验证 profile 无其他进程
        processes = self._find_profile_processes()
        if processes:
            result["profile_free"] = False
            result["ok"] = False
            result["details"].append(
                f"仍有进程使用 profile: PIDs={[p['pid'] for p in processes]}"
            )

        if result["ok"]:
            print("[engine] 浏览器清理验证通过")

        return result

    def close(self):
        """关闭浏览器并释放所有内存。

        standalone 模式：关闭 page → 关闭 context → 停止 playwright。
        关闭失败时记录错误但继续执行（调用方应随后执行 verify_cleanup）。
        禁止按进程名清理，禁止 killall Chrome。
        """
        errors = []
        if self._is_standalone:
            for platform_name in list(self._platform_pages):
                try:
                    self.release_page_for_platform(platform_name)
                except Exception as exc:
                    errors.append(exc)
            if self._context:
                try:
                    self._context.close()
                except Exception as exc:
                    errors.append(exc)
                else:
                    self._context = None
                    self._platform_pages.clear()
        else:
            self._browser = None

        if self._playwright:
            try:
                self._playwright.stop()
            except Exception as exc:
                errors.append(exc)
            else:
                self._playwright = None

        if errors:
            raise BrowserCleanupError(f"浏览器清理失败: {errors[0]}") from errors[0]
        if self._is_standalone:
            print("[engine] 独立浏览器已关闭，内存已释放")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()
