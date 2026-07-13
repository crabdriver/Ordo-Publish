"""按平台生成内容变体，降低全网判重 / 算法降权风险

原理（第一性原理）：
平台算法最反感的恰恰是「同一篇内容同时铺满全网」。真人运营者会改标题、
改开头钩子、调排版。这里做轻量、确定性的变换，让 6 个平台拿到的正文彼此不同，
但核心信息一致。

变换内容：
- 正文开头加平台化「钩子句」
- 正文结尾加平台化「互动引导」
- 标题保持原样（避免过度改动导致语义漂移；标题判重权重高，改了反而容易被误判为不同选题）

可通过 config.json 的 `content_variants: false` 关闭（默认开启）。
"""
from __future__ import annotations

LEAD_HOOKS = {
    "zhihu": "最近一直在想一个问题：",
    "toutiao": "别划走，这篇文章值得你花 3 分钟读完：",
    "jianshu": "夜深人静，写一点心里话：",
    "yidian": "今天分享一个很实在的观点：",
    "bilibili": "这期想认真聊聊：",
    "wechat": "你好呀，今天想和你分享：",
}

TAIL_NOTE = {
    "zhihu": "\n\n——以上是我的一些不成熟看法，欢迎在评论区交流。",
    "toutiao": "\n\n觉得有用，点个赞再走，你的支持是我持续分享的动力。",
    "jianshu": "\n\n写完这些，心里安静了不少。与君共勉。",
    "yidian": "\n\n观点仅供参考，也欢迎留言说说你的看法。",
    "bilibili": "\n\n如果对你有帮助，记得一键三连支持一下～",
    "wechat": "\n\n如果这篇文章让你有了一点共鸣，点个「在看」吧。",
}


def _normalize_body(body: str) -> str:
    """去掉多余空行、尾部空白，统一为干净正文"""
    lines = [ln.rstrip() for ln in body.splitlines()]
    # 去掉首尾空行
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def generate_variant(platform: str, title: str, body: str) -> tuple[str, str]:
    """返回 (标题变体, 正文变体)。platform 不在表中时原样返回。"""
    if platform not in LEAD_HOOKS:
        return title, body

    hook = LEAD_HOOKS[platform]
    tail = TAIL_NOTE[platform]
    body = _normalize_body(body)

    if hook and not body.startswith(hook):
        body = hook + body

    if tail and tail not in body:
        body = body.rstrip() + tail + "\n"

    # 标题暂保持原样（见模块 docstring 说明）
    return title, body
