from pathlib import Path

from ordo_engine.platforms.registry import build_platform_registry
from ordo_engine.run_state import article_key, is_done, mark_done, state_file_for
from ordo_engine.platforms.playwright.adapters import PlaywrightPlatformAdapter
from ordo_engine.platforms.playwright.engine import PlaywrightEngine


def _synthetic_skip(platform, article_path, mode):
    """幂等跳过时构造一条与正常结果结构兼容的记录"""
    return {
        "platform": platform,
        "article": str(article_path),
        "returncode": 0,
        "status": "skipped",
        "page_state": "skipped",
        "mode": mode,
        "summary": "已完成，按幂等策略跳过",
        "stage": "publish",
        "current_url": "",
        "smoke_step": "skipped",
        "retryable": False,
        "error_type": "duplicate_or_skipped",
        "stdout": "",
        "stderr": "",
        "script": "",
        "article_id": None,
        "theme_name": None,
        "template_mode": None,
        "cover_path": None,
        "cover_mode": None,
        "ai_declaration_mode": None,
        "scheduled_publish_at": None,
    }

_CONTEXT_PAYLOAD_KEYS = (
    "theme_name",
    "cover_path",
    "template_mode",
    "article_id",
    "cover_mode",
    "ai_declaration_mode",
    "scheduled_publish_at",
)


def run_platform_task(
    base_dir,
    platform,
    markdown_file,
    mode,
    theme_name=None,
    cover_path=None,
    template_mode=None,
    article_id=None,
    cover_mode=None,
    ai_declaration_mode=None,
    scheduled_publish_at=None,
    registry=None,
):
    registry = registry or build_platform_registry(Path(base_dir))
    adapter = registry[platform]
    cover_arg = str(cover_path) if cover_path else None
    prepared = adapter.prepare(
        markdown_file=markdown_file,
        mode=mode,
        theme_name=theme_name,
        cover_path=cover_arg,
        template_mode=template_mode,
        article_id=article_id,
        cover_mode=cover_mode,
        ai_declaration_mode=ai_declaration_mode,
        scheduled_publish_at=scheduled_publish_at,
    )
    process_result = adapter.publish(prepared)
    structured_result = adapter.collect_result(process_result, mode=mode)
    payload = {
        **process_result,
        "mode": mode,
        "status": structured_result.status,
        "summary": structured_result.summary,
        "stage": structured_result.stage,
        "current_url": structured_result.current_url,
        "page_state": structured_result.page_state,
        "smoke_step": structured_result.smoke_step,
        "retryable": structured_result.retryable,
        "error_type": structured_result.error_type.value if structured_result.error_type else None,
    }
    for key in _CONTEXT_PAYLOAD_KEYS:
        if key in prepared:
            payload[key] = prepared[key]
    for key in _CONTEXT_PAYLOAD_KEYS:
        payload.setdefault(key, None)
    return payload


def run_publish_pipeline(
    base_dir,
    args,
    article_paths,
    platforms,
    registry=None,
    theme_resolver=None,
    context_resolver=None,
    append_record=None,
    printer=None,
    engine_factory=None,
):
    registry = registry or build_platform_registry(Path(base_dir))
    state_file = state_file_for(base_dir)
    results = []
    exit_code = 0

    # 仅浏览器适配器需要引擎。一个 pipeline 只允许一个独立 context。
    shared_engine = None
    browser_platforms = [
        platform
        for platform in platforms
        if isinstance(registry[platform], PlaywrightPlatformAdapter)
    ]
    has_browser_work = any(
        not is_done(
            article_key(article_path),
            platform,
            args.mode,
            state_file=state_file,
        )
        for article_path in article_paths
        for platform in browser_platforms
    )
    selected_browser_adapters = (
        [registry[platform] for platform in browser_platforms]
        if has_browser_work
        else []
    )
    if selected_browser_adapters:
        factory = engine_factory or PlaywrightEngine
        try:
            shared_engine = factory(
                mode="standalone",
                headless=not getattr(args, "headed", False),
                base_dir=Path(base_dir),
            )
            shared_engine.connect()
            for adapter in selected_browser_adapters:
                adapter.set_shared_engine(shared_engine)
            print("[pipeline] 已启用共享引擎（单 context 多 tab）")
        except Exception as exc:  # noqa: BLE001
            if shared_engine is not None:
                try:
                    shared_engine.close()
                except Exception:
                    pass
            shared_engine = None
            for adapter in selected_browser_adapters:
                adapter.set_shared_engine(None)
            raise RuntimeError(f"独立浏览器启动失败: {exc}") from exc

    try:
        for article_path in article_paths:
            akey = article_key(article_path)
            for platform in platforms:
                theme_name = None
                cover_path = None
                template_mode = None
                article_id = None
                cover_mode = None
                ai_declaration_mode = None
                scheduled_publish_at = None

                if context_resolver:
                    blob = context_resolver(article_path, platform)
                    if blob:
                        theme_name = blob.get("theme_name")
                        cover_path = blob.get("cover_path")
                        template_mode = blob.get("template_mode")
                        article_id = blob.get("article_id")
                        cover_mode = blob.get("cover_mode")
                        ai_declaration_mode = blob.get("ai_declaration_mode")
                        scheduled_publish_at = blob.get("scheduled_publish_at")

                if platform == "wechat" and theme_resolver and theme_name is None:
                    theme_name = theme_resolver(article_path)

                # ── 幂等：已完成则跳过，避免重跑堆积重复草稿 ──
                if is_done(akey, platform, args.mode, state_file=state_file):
                    print(f"[SKIP] {platform} 《{Path(article_path).name}》已完成，跳过（幂等）")
                    results.append(_synthetic_skip(platform, article_path, args.mode))
                    continue

                result = run_platform_task(
                    base_dir=base_dir,
                    platform=platform,
                    markdown_file=str(article_path),
                    mode=args.mode,
                    theme_name=theme_name,
                    cover_path=cover_path,
                    template_mode=template_mode,
                    article_id=article_id,
                    cover_mode=cover_mode,
                    ai_declaration_mode=ai_declaration_mode,
                    scheduled_publish_at=scheduled_publish_at,
                    registry=registry,
                )
                result["article"] = str(article_path)
                results.append(result)

                # ── 成功后记录幂等状态，重跑不再重复 ──
                if result["returncode"] == 0:
                    final_status = result.get("page_state") or result.get("status") or "draft_only"
                    mark_done(
                        akey,
                        platform,
                        final_status,
                        args.mode,
                        result.get("current_url", ""),
                        state_file=state_file,
                    )

                if append_record:
                    append_record(result)
                if printer:
                    printer(result)

                if result["returncode"] != 0:
                    exit_code = 1
                    if not getattr(args, "continue_on_error", False):
                        return results, exit_code
    finally:
        if shared_engine is not None:
            try:
                shared_engine.close()
            except Exception:
                pass
        for adapter in selected_browser_adapters:
            adapter.set_shared_engine(None)

    return results, exit_code
