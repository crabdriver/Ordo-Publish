from typing import Dict, Iterable, List, Optional


PLATFORM_MATCHES = {
    "zhihu": ["zhihu.com"],
    "toutiao": ["mp.toutiao.com"],
    "jianshu": ["jianshu.com/writer"],
    "yidian": ["mp.yidianzixun.com"],
}


def platform_tab_exists(platform: str, tabs: Iterable[Dict]):
    matches = PLATFORM_MATCHES.get(platform, [])
    return any(any(keyword in tab.get("url", "") for keyword in matches) for tab in tabs)


def find_platform_target(platform: str, tabs: List[Dict]) -> Optional[str]:
    matches = PLATFORM_MATCHES.get(platform, [])
    for tab in tabs:
        url = tab.get("url", "")
        if any(keyword in url for keyword in matches):
            return tab.get("target")
    return None


def bind_workbench(platforms: Iterable[str], tabs: List[Dict]):
    return {
        platform: find_platform_target(platform, tabs)
        for platform in platforms
        if find_platform_target(platform, tabs)
    }
