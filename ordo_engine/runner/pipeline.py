import sys
import traceback
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ordo_engine.platforms.registry import build_platform_registry
from ordo_engine.platforms.base import is_terminal_outcome
from ordo_engine.run_state import (
    article_key, is_done, mark_done, state_file_for,
    PlatformStage, ArticleStage, ArticleRecord, PlatformRecord,
    load_v2_state, save_v2_state, set_platform_record, stable_article_id,
    compute_package_hash, get_platform_record, is_article_completed,
    StateCorruptionError,
)
from ordo_engine.assignment.cover_contract import CoverContractError, resolve_wechat_cover
from ordo_engine.platforms.playwright.adapters import PlaywrightPlatformAdapter
from ordo_engine.platforms.playwright.engine import PlaywrightEngine


def _synthetic_skip(platform, article_path, mode):
    """幂等跳过时构造一条与正常结果结构兼容的记录"""
    return {
        "platform": platform,
        "article": str(article_path),
        "returncode": 0,
        "status": "skipped_existing",
        "page_state": "skipped_existing",
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
    "force_republish",
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
    force_republish=False,
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
    prepared["force_republish"] = bool(force_republish)
    if force_republish and platform == "wechat" and "command" in prepared:
        prepared["command"].append("--force-republish")
    process_result = adapter.publish(prepared)
    structured_result = adapter.collect_result(process_result, mode=mode)
    raw_returncode = process_result.get("returncode", 1)
    effective_returncode = (
        raw_returncode
        if raw_returncode != 0 or is_terminal_outcome(structured_result.status, mode)
        else 1
    )
    payload = {
        **process_result,
        "returncode": effective_returncode,
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
        getattr(args, "force_republish", False) or not is_done(
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
                if not getattr(args, "force_republish", False) and is_done(
                    akey, platform, args.mode, state_file=state_file
                ):
                    print(f"[SKIP] {platform} 《{Path(article_path).name}》已完成，跳过（幂等）")
                    result = _synthetic_skip(platform, article_path, args.mode)
                    result["article_key"] = akey
                    result["run_id"] = getattr(args, "run_id", None)
                    results.append(result)
                    if append_record:
                        append_record(result)
                    if printer:
                        printer(result)
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
                    force_republish=getattr(args, "force_republish", False),
                    registry=registry,
                )
                result["article"] = str(article_path)
                result["article_key"] = akey
                result["run_id"] = getattr(args, "run_id", None)
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
        close_error = None
        active_error = sys.exc_info()[1]
        if shared_engine is not None:
            try:
                shared_engine.close()
            except Exception as exc:
                close_error = exc
        for adapter in selected_browser_adapters:
            adapter.set_shared_engine(None)
        if close_error is not None:
            if active_error is None:
                raise close_error
            active_error.add_note(f"共享浏览器关闭失败: {close_error}")

    return results, exit_code


# ── BatchCoordinator ──────────────────────────────────────────────


WECHAT_PLATFORM = "wechat"
BROWSER_PLATFORMS_TUPLE = ("zhihu", "toutiao", "jianshu", "yidian", "bilibili")
AUTO_PLATFORM_ORDER = (WECHAT_PLATFORM, "zhihu", "jianshu", "toutiao", "yidian", "bilibili")


class BatchCoordinator:
    """单批次协调器 — 一个锁、一次状态加载、逐平台逐文章处理。"""

    def __init__(self, base_dir: Path, *, state_file=None, watch_dir=None,
                 registry=None, engine_factory=None, wechat_adapter=None,
                 record_callback=None):
        self.base_dir = Path(base_dir)
        self.state_file = state_file or state_file_for(self.base_dir)
        self.watch_dir = Path(watch_dir) if watch_dir else None
        self._registry = registry
        self.engine_factory = engine_factory or PlaywrightEngine
        self.wechat_adapter = wechat_adapter
        self.record_callback = record_callback
        self._articles: dict[str, ArticleRecord] = {}
        self._batch_identities: set[str] = set()
        self._touched_records: set[tuple[str, str, str]] = set()

    @property
    def registry(self):
        if self._registry is None:
            self._registry = build_platform_registry(self.base_dir)
        return self._registry

    def run_batch(self, article_paths: list[Path]) -> dict:
        self._touched_records.clear()
        self._load_or_init_state(article_paths)
        self._batch_identities = {
            stable_article_id(path, watch_dir=self.watch_dir)
            for path in article_paths
        }
        self._preflight_all(article_paths)
        self._run_wechat_batch(article_paths)
        for platform in BROWSER_PLATFORMS_TUPLE:
            if platform not in self.registry:
                continue
            self._run_browser_platform(platform, article_paths)
        self._refresh_article_stages()
        self._save_state()
        return self._build_summary()

    def _load_or_init_state(self, article_paths):
        self._articles = load_v2_state(self.state_file)
        sources: dict[Path, set[str]] = {}
        for existing_identity, record in self._articles.items():
            if not record.source_path:
                continue
            source = Path(record.source_path).expanduser().resolve()
            sources.setdefault(source, set()).add(existing_identity)
        for p in article_paths:
            identity = stable_article_id(p, watch_dir=self.watch_dir)
            source = Path(p).expanduser().resolve()
            conflicts = sources.get(source, set()) - {identity}
            if conflicts:
                protected_conflicts = {
                    conflict
                    for conflict in conflicts
                    if self._articles[conflict].platforms
                    or self._articles[conflict].article_stage != ArticleStage.pending
                }
                if protected_conflicts:
                    raise StateCorruptionError(
                        f"文章 identity 冲突: {source} 同时对应 "
                        f"{sorted(protected_conflicts)} 和 {identity}"
                    )
                for empty_alias in conflicts:
                    self._articles.pop(empty_alias, None)
                    sources[source].discard(empty_alias)
            if identity not in self._articles:
                self._articles[identity] = ArticleRecord(
                    article_id=identity, source_path=str(p),
                    article_stage=ArticleStage.pending)
            sources.setdefault(source, set()).add(identity)

    def _save_state(self):
        save_v2_state(self._articles, self.state_file)

    def _preflight_all(self, article_paths):
        for p in article_paths:
            try:
                self._preflight_one(p)
            except Exception as exc:
                identity = stable_article_id(p, watch_dir=self.watch_dir)
                a = self._articles.get(identity)
                if a:
                    a.article_block_reason = f"preflight: {exc}"

    def _preflight_one(self, article_path):
        identity = stable_article_id(article_path, watch_dir=self.watch_dir)
        a = self._articles.setdefault(identity, ArticleRecord(
            article_id=identity, source_path=str(article_path)))

        # ── 1. frontmatter 格式检查 ──
        text = article_path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            a.article_stage = ArticleStage.needs_review
            a.article_block_reason = "frontmatter 格式错误：必须以 --- 开头"
            return

        # 浏览器平台暂停封面；封面仅在微信分支单独验证。
        obsolete_cover_block = (
            "封面合同失败", "封面质量不合格", "缺少封面文件",
        )
        if a.article_block_reason and a.article_block_reason.startswith(obsolete_cover_block):
            a.article_block_reason = None
            if a.article_stage == ArticleStage.needs_review:
                a.article_stage = ArticleStage.pending
        a.package_hash = compute_package_hash(article_path, None)
        a.source_path = str(article_path)

    def _run_wechat_batch(self, article_paths):
        """微信批处理只委托 VPS adapter；本机不得运行微信 worker。"""
        adapter = self.wechat_adapter or self.registry.get(WECHAT_PLATFORM)
        try:
            for p in article_paths:
                identity = stable_article_id(p, watch_dir=self.watch_dir)
                a = self._articles.get(identity)
                if not a or not self._needs_processing(a, WECHAT_PLATFORM, "draft"):
                    continue
                try:
                    # VPS 无法访问本机润色目录；必须显式上传已校验封面。
                    cover = resolve_wechat_cover(p)
                    self._run_wechat_via_vps(p, cover)
                except CoverContractError as exc:
                    prec = PlatformRecord(
                        stage=PlatformStage.failed_before_draft,
                        error=str(exc),
                        error_type="cover_preflight_failed",
                    )
                    self._set_platform_record(
                        identity, WECHAT_PLATFORM, "draft", prec
                    )
                except Exception as exc:
                    prec = PlatformRecord(
                        stage=PlatformStage.failed_before_draft,
                        error=str(exc),
                        error_type="wechat_vps_adapter_failed",
                    )
                    self._set_platform_record(
                        identity, WECHAT_PLATFORM, "draft", prec
                    )
                self._save_state()
        finally:
            if adapter is not None and hasattr(adapter, "close_batch"):
                adapter.close_batch()

    def _run_wechat_via_vps(self, article_path, cover_path=None):
        """通过专用 adapter 在 VPS 执行微信 worker。"""
        adapter = self.wechat_adapter or self.registry.get(WECHAT_PLATFORM)
        if adapter is None:
            raise RuntimeError("缺少微信公众号 VPS adapter")

        prepare_args = {
            "markdown_file": article_path,
            "mode": "draft",
        }
        if cover_path is not None:
            prepare_args.update(
                cover_path=str(cover_path),
                cover_mode="force_on",
            )
        prepared = adapter.prepare(**prepare_args)
        result = adapter.publish(prepared)
        structured = adapter.collect_result(result, mode="draft")

        output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}".strip()
        identity = stable_article_id(article_path, watch_dir=self.watch_dir)
        match = re.search(r"已写入微信公众号草稿:\s*(\S+)", output)
        if match:
            stage = PlatformStage.draft_saved
            error_type = None
            error = None
        elif not result.get("remote_started", False):
            stage = PlatformStage.failed_before_draft
            error_type = (
                "wechat_vps_config_missing"
                if result.get("returncode") == 2
                else "wechat_vps_transport_failed"
            )
            error = output[-1000:] or "VPS 微信任务未启动"
        else:
            # VPS worker 已启动；无 media id 时无法证明草稿未创建。
            stage = PlatformStage.manual_verify
            error_type = (
                "wechat_vps_worker_timeout"
                if result.get("timed_out")
                else "wechat_vps_result_unverified"
            )
            error = output[-1000:] or str(getattr(structured, "summary", ""))
        prec = PlatformRecord(
            stage=stage,
            draft_ref=match.group(1) if match else None,
            error=error,
            error_type=error_type,
        )
        self._set_platform_record(identity, WECHAT_PLATFORM, "draft", prec)

    def _run_browser_platform(self, platform, article_paths):
        eligible = []
        for p in article_paths:
            identity = stable_article_id(p, watch_dir=self.watch_dir)
            article = self._articles.get(identity)
            if article and self._needs_processing(article, platform, "publish"):
                eligible.append(p)
        if not eligible:
            return

        engine = None
        try:
            engine = self.engine_factory(mode="standalone", headless=True, base_dir=self.base_dir)
            engine.connect()
        except Exception as exc:
            for p in eligible:
                identity = stable_article_id(p, watch_dir=self.watch_dir)
                article = self._articles.get(identity)
                if article and self._needs_processing(article, platform, "publish"):
                    prec = PlatformRecord(stage=PlatformStage.not_executed,
                                          error=str(exc), error_type="browser_start_failed")
                    self._set_platform_record(identity, platform, "publish", prec)
            self._save_state()
            return
        try:
            for p in eligible:
                identity = stable_article_id(p, watch_dir=self.watch_dir)
                a = self._articles.get(identity)
                if not a or not self._needs_processing(a, platform, "publish"):
                    continue
                try:
                    self._run_one_browser_article(p, platform, engine)
                except Exception as exc:
                    self._record_error(identity, platform, "publish", str(exc))
            self._save_state()
        finally:
            # ── close + verify（即使 close 异常也执行验证）──
            close_error = None
            try:
                engine.close()
            except Exception as e:
                close_error = e

            verify_result = None
            try:
                verify_result = engine.verify_cleanup()
            except Exception:
                verify_result = {"ok": False, "details": ["verify_cleanup 自身异常"]}

            cleanup_failed = close_error is not None or not (verify_result or {}).get("ok", False)
            if cleanup_failed:
                details = (verify_result or {}).get("details", [])
                if close_error:
                    details.append(f"close: {close_error}")
                remaining = [bp for bp in BROWSER_PLATFORMS_TUPLE
                             if BROWSER_PLATFORMS_TUPLE.index(bp) > BROWSER_PLATFORMS_TUPLE.index(platform)]
                for rp in remaining:
                    for p in article_paths:
                        identity = stable_article_id(p, watch_dir=self.watch_dir)
                        existing = get_platform_record(self._articles, identity, rp, "publish")
                        if existing and existing.stage != PlatformStage.pending:
                            continue
                        prec = PlatformRecord(
                            stage=PlatformStage.not_executed,
                            error="; ".join(details) if details else str(close_error or "cleanup failed"),
                            error_type="browser_cleanup_failed")
                        self._set_platform_record(identity, rp, "publish", prec)
                self._save_state()
                if close_error:
                    raise close_error

    def _run_one_browser_article(self, article_path, platform, engine):
        adapter = self.registry.get(platform)
        if adapter is None:
            raise RuntimeError(f"平台未注册: {platform}")
        if isinstance(adapter, PlaywrightPlatformAdapter):
            adapter.set_shared_engine(engine)
        try:
            identity = stable_article_id(article_path, watch_dir=self.watch_dir)
            payload = run_platform_task(
                base_dir=self.base_dir, platform=platform,
                markdown_file=str(article_path), mode="publish",
                cover_path=None,
                registry=self.registry)
            identity = stable_article_id(article_path, watch_dir=self.watch_dir)
            stage = _map_payload_stage(payload)
            prec = PlatformRecord(
                stage=stage,
                published_ref=payload.get("current_url") if stage == PlatformStage.published else None,
                error=payload.get("stderr"), error_type=payload.get("error_type"))
            self._set_platform_record(identity, platform, "publish", prec)
            # 记录审计
            if self.record_callback:
                self.record_callback(str(article_path), platform, "publish", payload)
        finally:
            if isinstance(adapter, PlaywrightPlatformAdapter):
                try:
                    engine.release_page_for_platform(platform)
                except Exception:
                    pass
                adapter.set_shared_engine(None)

    def _needs_processing(self, article, platform, mode):
        """判断文章+平台+mode 是否需要处理（恢复规则）。"""
        # 文章级阻断
        if article.article_stage == ArticleStage.needs_review:
            return False
        prec = article.platforms.get(platform, {}).get(mode)
        if prec is None:
            return True
        # retry_after 检查
        if prec.retry_after:
            now = _now_iso() if hasattr(self, '_now_iso') else datetime.now(timezone.utc).isoformat()
            if now < prec.retry_after:
                return False
        # 不应重试的终态
        skip_stages = {
            PlatformStage.published,
            PlatformStage.manual_verify,
            PlatformStage.blocked_no_draft,
            PlatformStage.publish_attempted,
            # 草稿恢复尚未接入 coordinator；重建会产生重复草稿。
            PlatformStage.draft_saved,
        }
        if prec.stage in skip_stages:
            return False
        # limited_after_draft: 有 retry_after 但已过期 → 允许重试
        # failed_before_draft: 允许重试
        # pending / preflight_ok / draft_prepared / draft_saved / publish_attempted → 允许
        return True

    def needs_any_processing(self, article: ArticleRecord) -> bool:
        if article.article_stage == ArticleStage.completed:
            return False
        platform_modes = [
            (WECHAT_PLATFORM, "draft"),
            *((platform, "publish") for platform in BROWSER_PLATFORMS_TUPLE),
        ]
        return any(
            self._needs_processing(article, platform, mode)
            for platform, mode in platform_modes
        )

    def _refresh_article_stages(self) -> None:
        for identity in self._batch_identities:
            article = self._articles.get(identity)
            if article is None or article.article_stage == ArticleStage.needs_review:
                continue
            if is_article_completed(article, list(BROWSER_PLATFORMS_TUPLE)):
                article.article_stage = ArticleStage.completed
                article.completed_at = datetime.now(timezone.utc).isoformat()

    def _record_error(self, identity, platform, mode, error):
        prec = PlatformRecord(stage=PlatformStage.failed_before_draft, error=error)
        self._set_platform_record(identity, platform, mode, prec)

    def _set_platform_record(self, identity, platform, mode, record):
        set_platform_record(self._articles, identity, platform, mode, record)
        self._touched_records.add((identity, platform, mode))

    def _build_summary(self):
        s = {"articles": {}}
        for identity, article in self._articles.items():
            if self._batch_identities and identity not in self._batch_identities:
                continue
            touched = {
                (platform, mode)
                for record_identity, platform, mode in self._touched_records
                if record_identity == identity
            }
            if not touched:
                continue
            pf = {}
            for pn, modes in article.platforms.items():
                for md, prec in modes.items():
                    if (pn, md) not in touched:
                        continue
                    pf[f"{pn}:{md}"] = {
                        "stage": prec.stage.value,
                        "error": prec.error, "error_type": prec.error_type,
                        "draft_ref": prec.draft_ref, "published_ref": prec.published_ref}
            s["articles"][identity] = {
                "article_id": article.article_id,
                "article_stage": article.article_stage.value,
                "title": article.title, "platforms": pf}
        return s


def _map_status(status):
    m = {"published": PlatformStage.published, "scheduled": PlatformStage.published,
         "draft_saved": PlatformStage.draft_saved, "draft_only": PlatformStage.draft_saved,
         "skipped_existing": PlatformStage.published,
         "limit_reached": PlatformStage.limited_after_draft,
         "rate_limited": PlatformStage.limited_after_draft,
         "submitted_unverified": PlatformStage.manual_verify,
         "failed": PlatformStage.failed_before_draft,
         "unknown": PlatformStage.manual_verify}
    return m.get(status, PlatformStage.failed_before_draft)


def _map_payload_stage(payload):
    if payload.get("error_type") == "publish_click_no_effect":
        return PlatformStage.manual_verify
    return _map_status(payload.get("status", "failed"))
