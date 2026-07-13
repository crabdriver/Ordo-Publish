"""发布前「选择器存活探针」

每天发布前先轻量检查各平台关键选择器是否还在，把「平台改版 → 选择器断裂」
这个最隐蔽的失败提前暴露出来，而不是等 9 点静默崩。

检测方式：用已登录的 automation-profile 打开编辑器，
- 若标题选择器可见 → OK
- 若跳到登录页（标题选择器为 0 且 URL 不在编辑器域）→ 需要登录
- 若编辑器域内但标题选择器为 0 → 选择器可能已失效（平台改版）
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List

from ordo_engine.platforms.playwright.engine import PlaywrightEngine
from ordo_engine.platforms.playwright_zhihu.locators import ZhihuLocators
from ordo_engine.platforms.playwright_toutiao.locators import ToutiaoLocators
from ordo_engine.platforms.playwright_jianshu.locators import JianshuLocators
from ordo_engine.platforms.playwright_yidian.locators import YidianLocators
from ordo_engine.platforms.playwright_bilibili.locators import BilibiliLocators

# platform -> (编辑器 URL, 标题选择器)
PROBE_MAP = {
    "zhihu": ("https://zhuanlan.zhihu.com/write", ZhihuLocators.TITLE_INPUT),
    "toutiao": ("https://mp.toutiao.com/profile_v4/graphic/publish", ToutiaoLocators.TITLE_INPUT),
    "jianshu": ("https://www.jianshu.com/writer#/", JianshuLocators.TITLE_INPUT),
    "yidian": ("https://mp.yidianzixun.com/#/Writing/articleEditor", YidianLocators.TITLE_INPUT),
    "bilibili": ("https://member.bilibili.com/platform/upload/text/new-edit", BilibiliLocators.TITLE_INPUT),
}

# 编辑器 URL 关键串（用于判断是否仍在编辑器域）
EDITOR_MARKERS = {
    "zhihu": "zhuanlan.zhihu.com",
    "toutiao": "mp.toutiao.com",
    "jianshu": "jianshu.com/writer",
    "yidian": "mp.yidianzixun.com",
    "bilibili": "member.bilibili.com",
}


def probe_platform(engine: PlaywrightEngine, platform: str) -> Dict[str, str]:
    """探测单个平台，返回 {status, detail}。status ∈ ok|login|broken|error"""
    editor_url, title_selector = PROBE_MAP[platform]
    try:
        page = engine.get_page_for_platform(platform)
        # 等待编辑器加载
        time.sleep(4)
        title_count = page.locator(title_selector).count()
        url = page.url or ""
        marker = EDITOR_MARKERS[platform]

        if title_count > 0:
            return {"status": "ok", "detail": f"标题选择器命中 {title_count} 个"}
        if marker not in url:
            return {"status": "login", "detail": f"未在编辑器域（当前 {url}），可能需登录"}
        return {"status": "broken", "detail": f"在编辑器域但标题选择器 0 命中，可能已改版"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}


def probe_platforms(platforms: List[str], headless: bool = True) -> Dict[str, Dict[str, str]]:
    """对多个平台跑探针，返回 {platform: {status, detail}}"""
    engine = PlaywrightEngine(mode="standalone", headless=headless)
    engine.connect()
    results: Dict[str, Dict[str, str]] = {}
    try:
        for p in platforms:
            if p == "wechat":
                results[p] = {"status": "ok", "detail": "微信走 API 模式，无需探针"}
                continue
            if p not in PROBE_MAP:
                results[p] = {"status": "skip", "detail": "无探针配置"}
                continue
            print(f"[PROBE] 探测 {p} ...")
            results[p] = probe_platform(engine, p)
            print(f"[PROBE] {p}: {results[p]['status']} - {results[p]['detail']}")
    finally:
        engine.close()
    return results


if __name__ == "__main__":
    import sys
    targets = sys.argv[1:] or ["zhihu", "toutiao", "jianshu", "yidian", "bilibili"]
    out = probe_platforms(targets, headless=True)
    broken = [k for k, v in out.items() if v["status"] in ("broken", "error")]
    print("\n===== 探针汇总 =====")
    for k, v in out.items():
        print(f"  {k}: {v['status']} - {v['detail']}")
    if broken:
        print(f"\n⚠️ 以下平台选择器异常，请人工核查：{broken}")
        sys.exit(1)
    print("\n✅ 所有平台选择器存活正常")
