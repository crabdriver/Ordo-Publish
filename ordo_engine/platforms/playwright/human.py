from __future__ import annotations

import math
import platform
import random
import time
from typing import Optional, Tuple

try:
    from patchright.sync_api import Locator, Page
except ImportError:
    from playwright.sync_api import Locator, Page


def _modifier_key() -> str:
    """macOS 用 Meta，Linux/Windows 用 Control"""
    return "Meta" if platform.system() == "Darwin" else "Control"


def _bezier_points(
    start: Tuple[float, float],
    end: Tuple[float, float],
    steps: int = 20,
) -> list:
    """生成三次 Bezier 曲线路径点

    使用两个随机控制点模拟人手移动鼠标的弧线轨迹。
    """
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    dist = math.hypot(dx, dy)

    # Random control points offset perpendicular to the line
    spread = max(dist * 0.3, 20)
    cp1 = (
        sx + dx * random.uniform(0.2, 0.4) + random.uniform(-spread, spread),
        sy + dy * random.uniform(0.2, 0.4) + random.uniform(-spread, spread),
    )
    cp2 = (
        sx + dx * random.uniform(0.6, 0.8) + random.uniform(-spread, spread),
        sy + dy * random.uniform(0.6, 0.8) + random.uniform(-spread, spread),
    )

    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3 * sx + 3 * u**2 * t * cp1[0] + 3 * u * t**2 * cp2[0] + t**3 * ex
        y = u**3 * sy + 3 * u**2 * t * cp1[1] + 3 * u * t**2 * cp2[1] + t**3 * ey
        points.append((x, y))
    return points


class HumanBehavior:
    """为 Playwright Page 注入人像化行为模拟

    所有方法都是同步的，直接操作 Page 对象。
    包含：Bezier 曲线鼠标移动、变速打字、剪贴板粘贴、平滑滚动。
    """

    def __init__(
        self,
        page: Page,
        *,
        typing_speed: str = "normal",
        mouse_bezier: bool = True,
        random_delays: bool = True,
    ):
        self.page = page
        self.typing_speed = typing_speed
        self.mouse_bezier = mouse_bezier
        self.random_delays = random_delays
        self._modifier = _modifier_key()
        self._mouse_x: float = 0
        self._mouse_y: float = 0

    def _sleep(self, base: float, std: float = 0.0):
        """带随机偏移的等待"""
        if not self.random_delays:
            time.sleep(base)
            return
        duration = max(0.01, random.gauss(base, std)) if std > 0 else base
        time.sleep(duration)

    def human_wait(self, min_s: float = 0.5, max_s: float = 1.5):
        """模拟人类随机停顿"""
        time.sleep(random.uniform(min_s, max_s))

    # ── Mouse ──────────────────────────────────────────────────

    def move_mouse_to(self, x: float, y: float, *, steps: int = 0):
        """Bezier 曲线移动鼠标到目标位置"""
        if not self.mouse_bezier or (self._mouse_x == 0 and self._mouse_y == 0):
            # First move or bezier disabled: direct move
            self.page.mouse.move(x, y)
            self._mouse_x = x
            self._mouse_y = y
            return

        num_steps = steps or random.randint(15, 30)
        points = _bezier_points(
            (self._mouse_x, self._mouse_y), (x, y), steps=num_steps
        )
        for px, py in points:
            self.page.mouse.move(px, py)
            time.sleep(random.uniform(0.005, 0.02))

        self._mouse_x = x
        self._mouse_y = y

    def human_click(
        self,
        locator: Locator,
        *,
        offset_range: int = 5,
        pre_delay_min: float = 0.1,
        pre_delay_max: float = 0.3,
    ):
        """人像化点击：Bezier 移动 → 停顿 → 带偏移点击"""
        box = locator.bounding_box()
        if not box:
            # Fallback: direct click via Playwright
            locator.click()
            return

        target_x = box["x"] + box["width"] / 2 + random.randint(-offset_range, offset_range)
        target_y = box["y"] + box["height"] / 2 + random.randint(-offset_range, offset_range)

        self.move_mouse_to(target_x, target_y)
        self._sleep(random.uniform(pre_delay_min, pre_delay_max))
        self.page.mouse.click(target_x, target_y)

    def human_click_xy(self, x: float, y: float):
        """人像化坐标点击"""
        self.move_mouse_to(x, y)
        self._sleep(0.15, 0.05)
        self.page.mouse.click(x, y)

    # ── Keyboard ───────────────────────────────────────────────

    def _has_keyboard(self) -> bool:
        """检测 page 对象是否有 .keyboard 属性（Frame 没有）"""
        return hasattr(self.page, "keyboard")

    def _kbd_press(self, keys: str):
        """兼容 Page / Frame 的按键操作"""
        if self._has_keyboard():
            self.page.keyboard.press(keys)
            return
        # Frame 无 keyboard 属性，用 JS execCommand 模拟常用快捷键
        kl = keys.lower()
        is_mod = any(m in kl for m in ("control", "meta", "cmd"))
        if is_mod and "a" in kl:
            self.page.evaluate("() => document.execCommand('selectAll')")
        elif keys in ("Delete", "Backspace"):
            self.page.evaluate("() => document.execCommand('delete')")
        elif is_mod and "v" in kl:
            self.page.evaluate("() => document.execCommand('paste')")

    def _kbd_type(self, char: str):
        """兼容 Page / Frame 的单字输入"""
        if self._has_keyboard():
            self.page.keyboard.type(char)
        else:
            self.page.evaluate(
                "(char) => document.execCommand('insertText', false, char)",
                char,
            )

    def human_type(
        self,
        text: str,
        *,
        speed: Optional[str] = None,
    ):
        """模拟人类打字：变速、偶尔停顿

        speed: 'slow' (150ms avg), 'normal' (80ms avg), 'fast' (40ms avg)
        """
        effective_speed = speed or self.typing_speed
        speed_map = {
            "slow": (0.15, 0.05),
            "normal": (0.08, 0.03),
            "fast": (0.04, 0.015),
        }
        mean, std = speed_map.get(effective_speed, speed_map["normal"])

        for i, char in enumerate(text):
            self._kbd_type(char)
            delay = max(0.02, random.gauss(mean, std))
            time.sleep(delay)

            # 5% chance of thinking pause
            if random.random() < 0.05:
                time.sleep(random.uniform(0.3, 0.8))

            # Occasional burst-then-pause
            if i > 0 and i % random.randint(8, 15) == 0 and random.random() < 0.3:
                time.sleep(random.uniform(0.2, 0.5))

    def human_paste(self, content: str):
        """通过剪贴板粘贴内容（全选+粘贴）"""
        self.page.evaluate("(text) => navigator.clipboard.writeText(text)", content)
        self._sleep(0.3, 0.1)
        self._kbd_press(f"{self._modifier}+a")
        self._sleep(0.15, 0.05)
        self._kbd_press(f"{self._modifier}+v")
        self._sleep(0.5, 0.2)

    def human_paste_without_select(self, content: str):
        """粘贴但不全选（追加模式）"""
        self.page.evaluate("(text) => navigator.clipboard.writeText(text)", content)
        self._sleep(0.3, 0.1)
        self._kbd_press(f"{self._modifier}+v")
        self._sleep(0.5, 0.2)

    def human_clear_and_type(self, locator: Locator, text: str):
        """清空输入框并打字输入"""
        self.human_click(locator)
        self._sleep(0.2, 0.05)
        self._kbd_press(f"{self._modifier}+a")
        self._sleep(0.1, 0.03)
        self._kbd_press("Backspace")
        self._sleep(0.2, 0.05)
        self.human_type(text)

    # ── Scroll ─────────────────────────────────────────────────

    def human_scroll_to(self, target_y: int):
        """分段平滑滚动到目标位置"""
        current_y = self.page.evaluate("window.scrollY")
        delta = target_y - current_y
        if abs(delta) < 50:
            return

        steps = random.randint(4, 10)
        remaining = delta
        for i in range(steps):
            step_delta = remaining / (steps - i) + random.uniform(-20, 20)
            self.page.mouse.wheel(0, step_delta)
            remaining -= step_delta
            time.sleep(random.uniform(0.03, 0.12))

    def human_scroll_into_view(self, locator: Locator):
        """滚动直到元素可见"""
        locator.scroll_into_view_if_needed()
        self._sleep(0.3, 0.1)
