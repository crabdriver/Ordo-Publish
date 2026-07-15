"""只读报告渲染 —— 纯函数，不写文件、不重跑发布。

外部自动化软件负责推送飞书等外部渠道。
"""

_STAGE_LABELS = {
    "published": "已正式发布",
    "draft_saved": "草稿已保存",
    "draft_prepared": "草稿已准备（待核验）",
    "publish_attempted": "已点击发布（待核验）",
    "limited_after_draft": "草稿已保存，受发布数量限制",
    "blocked_no_draft": "平台能力阻断，未生成草稿",
    "manual_verify": "需要人工核验",
    "failed_before_draft": "保存草稿失败",
    "not_executed": "未执行",
    "pending": "等待中",
    "preflight_ok": "预检通过",
}


def render_report(snapshot: dict) -> str:
    """纯函数：输入 BatchCoordinator.build_summary() 的输出，返回格式化文本。"""
    lines = ["=" * 50, "  自动发布报告", "=" * 50, ""]

    for identity, article in snapshot.get("articles", {}).items():
        title = article.get("title") or article.get("article_id") or identity
        article_stage = article.get("article_stage", "pending")
        block_reason = article.get("article_block_reason", "")
        status_line = f"📄 {title}"
        if article_stage == "needs_review":
            status_line += f" ⚠️ 需人工确认({block_reason})"
        elif article_stage == "completed":
            status_line += " ✅ 已完成"
        lines.append(status_line)

        platforms = article.get("platforms", {})
        if not platforms:
            lines.append("   (无平台记录)")
        for pf_key, pf_data in sorted(platforms.items()):
            stage = pf_data.get("stage", "pending")
            label = _STAGE_LABELS.get(stage, stage)
            if stage == "limited_after_draft" and not pf_data.get("draft_ref"):
                label = "达到发布数量限制，草稿未核验"
            error = pf_data.get("error")
            error_type = pf_data.get("error_type")
            ref = pf_data.get("draft_ref") or pf_data.get("published_ref") or ""

            detail = f"   {pf_key}: {label}"
            if ref:
                detail += f" [{ref[:60]}]"
            if error_type:
                detail += f" ({error_type})"
            if error:
                detail += f"\n      原因: {error[:200]}"
            lines.append(detail)

        lines.append("")

    # 汇总统计
    total_published = 0
    total_draft = 0
    total_manual = 0
    total_not_executed = 0
    for article in snapshot.get("articles", {}).values():
        for pf_data in article.get("platforms", {}).values():
            s = pf_data.get("stage", "")
            if s == "published":
                total_published += 1
            elif s == "draft_saved":
                total_draft += 1
            elif s == "manual_verify":
                total_manual += 1
            elif s == "not_executed":
                total_not_executed += 1

    lines.append("─" * 50)
    lines.append(f"已发布: {total_published}  |  草稿: {total_draft}"
                 f"  |  待核验: {total_manual}  |  未执行: {total_not_executed}")
    lines.append("=" * 50)

    return "\n".join(lines)


def snapshot_to_structured(snapshot: dict) -> dict:
    """将快照转为结构化数据（供外部系统消费）。"""
    return {
        "articles": [
            {
                "article_id": art.get("article_id"),
                "title": art.get("title"),
                "article_stage": art.get("article_stage"),
                "platforms": [
                    {"platform": k, **v}
                    for k, v in sorted(art.get("platforms", {}).items())
                ],
            }
            for art in snapshot.get("articles", {}).values()
        ],
        "counts": {
            "total_articles": len(snapshot.get("articles", {})),
        },
    }
