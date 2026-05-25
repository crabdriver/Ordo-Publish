from pathlib import Path

from tiandi_engine.platforms.registry import build_platform_registry

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
):
    registry = registry or build_platform_registry(Path(base_dir))
    results = []
    exit_code = 0

    for article_path in article_paths:
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

            if append_record:
                append_record(result)
            if printer:
                printer(result)

            if result["returncode"] != 0:
                exit_code = 1
                if not getattr(args, "continue_on_error", False):
                    return results, exit_code

    return results, exit_code
