import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'

import type {
  AiDeclarationMode,
  BridgeResources,
  CoverMode,
  HistoryPayload,
  ImportJob,
  Platform,
  PublishEvent,
  PublishPlan,
  PublishResult,
  WechatConfigStatus,
  WechatSettingsPayload,
} from './types'

const isTauriRuntime = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

let mockWechatSettings: WechatSettingsPayload = {
  app_id: '',
  secret: '',
  author: '',
  status: {
    env_file_exists: false,
    appid_ready: false,
    secret_ready: false,
    covers_ready: false,
    cover_count: 0,
    ai_cover_ready: false,
  },
}

function buildMockWechatStatus(settings: { app_id: string; secret: string }): WechatConfigStatus {
  return {
    env_file_exists: Boolean(settings.app_id || settings.secret),
    appid_ready: Boolean(settings.app_id),
    secret_ready: Boolean(settings.secret),
    covers_ready: false,
    cover_count: 0,
    ai_cover_ready: false,
  }
}

function mockResources(): BridgeResources {
  return {
    theme_pool: {
      themes_dir: 'themes',
      theme_ids: ['chinese', 'night', 'clean'],
      count: 3,
      entries: [
        { theme_id: 'chinese', display_name: 'Chinese' },
        { theme_id: 'night', display_name: 'Night' },
        { theme_id: 'clean', display_name: 'Clean' },
      ],
    },
    cover_pool: {
      ok: true,
      cover_dir: 'covers',
      paths: ['covers/cover_a.png', 'covers/cover_b.png', 'covers/cover_c.png'],
      count: 3,
      error: null,
    },
    wechat: {
      settings: {
        app_id: mockWechatSettings.app_id,
        secret: mockWechatSettings.secret,
        author: mockWechatSettings.author,
      },
      status: mockWechatSettings.status,
    },
    browser: {
      browser_platforms: ['zhihu', 'toutiao', 'jianshu', 'yidian'],
      remote_debugging_required: true,
      login_required_platforms: ['zhihu', 'toutiao', 'jianshu', 'yidian'],
      managed_session: {
        enabled: true,
        remind_after_days: 5,
        profile_dir: '.tiandidistribute/browser-session/profile',
        debug_port: 9333,
      },
      session_state: {
        mode: 'managed',
        last_checked_at: null,
        updated_at: null,
        platforms: {},
        expiring_platforms: [],
        relogin_required_platforms: [],
      },
    },
    runtime: {
      repo_root: '/mock/tiandidistribute',
      python_executable: 'python3',
    },
    config_warning: null,
    defaults: {
      template_mode: 'default',
      cover_repeat_window: 8,
      cover_mode: 'auto',
      ai_declaration_mode: 'auto',
      scheduled_publish_at: null,
    },
  }
}

async function mockBridgeRequest<T>(payload: Record<string, unknown>): Promise<T> {
  const command = payload.command
  if (command === 'discover_resources') {
    return mockResources() as T
  }
  if (command === 'read_wechat_settings') {
    return mockWechatSettings as T
  }
  if (command === 'save_wechat_settings') {
    mockWechatSettings = {
      app_id: String(payload.app_id ?? ''),
      secret: String(payload.secret ?? ''),
      author: String(payload.author ?? ''),
      status: buildMockWechatStatus({
        app_id: String(payload.app_id ?? ''),
        secret: String(payload.secret ?? ''),
      }),
    }
    return mockWechatSettings as T
  }
  if (command === 'read_recent_history') {
    return { records: [], session: null, last_plan: null, last_result: null } as T
  }
  if (command === 'import_sources') {
    const pastedText = String(payload.pasted_text ?? '')
    const lines = pastedText.trim().split(/\n+/)
    const title = lines[0] || 'Untitled'
    const body = lines.slice(1).join('\n\n')
    return {
      job: {
        job_id: 'mock-import',
        import_mode: String(payload.import_mode ?? 'paste'),
        source_path: typeof payload.source_path === 'string' ? payload.source_path : null,
        pasted_preview: pastedText.slice(0, 120),
        imported_at: new Date().toISOString(),
        source_count: 1,
        manifest_path: null,
        article_count: 1,
        failure_count: 0,
        failures: [],
        drafts: [
          {
            article_id: 'mock-article',
            title,
            body_markdown: body,
            source_path: null,
            source_kind: 'paste',
            image_paths: [],
            word_count: body.length,
            template_mode: 'default',
            theme_name: null,
            is_config_complete: false,
          },
        ],
      },
      resources: mockResources(),
    } as T
  }
  if (command === 'plan_publish_job') {
    const drafts = Array.isArray(payload.drafts) ? payload.drafts : []
    const platforms = (Array.isArray(payload.platforms) ? payload.platforms : []) as Platform[]
    return {
      publish_job: {
        job_id: 'mock-publish',
        article_ids: drafts.map((item) => String((item as { article_id: string }).article_id)),
        platforms,
        status: 'pending',
        current_step: '',
        success_count: 0,
        failure_count: 0,
        skip_count: 0,
        recoverable: true,
        error_summary: '',
        scheduled_publish_at: String(payload.scheduled_publish_at ?? '').trim() || null,
      },
      mode: payload.mode ?? 'draft',
      continue_on_error: Boolean(payload.continue_on_error),
      cover_mode: String(payload.cover_mode ?? 'auto') as CoverMode,
      ai_declaration_mode: String(payload.ai_declaration_mode ?? 'auto') as AiDeclarationMode,
      scheduled_publish_at: String(payload.scheduled_publish_at ?? '').trim() || null,
      drafts,
      template_assignments: [],
      cover_assignments: [],
      staged_articles: drafts.map((item) => ({
        article_id: String((item as { article_id: string }).article_id),
        markdown_path: `/tmp/${String((item as { article_id: string }).article_id)}.md`,
      })),
      context_map: platforms.flatMap((platform) =>
        drafts.map((item) => ({
          article_id: String((item as { article_id: string }).article_id),
          platform,
          markdown_path: `/tmp/${String((item as { article_id: string }).article_id)}.md`,
          theme_name: null,
          template_mode: 'default',
          cover_path: null,
          cover_mode: String(payload.cover_mode ?? 'auto') as CoverMode,
          ai_declaration_mode: String(payload.ai_declaration_mode ?? 'auto') as AiDeclarationMode,
          scheduled_publish_at:
            platform === 'toutiao' && payload.mode === 'publish'
              ? String(payload.scheduled_publish_at ?? '').trim() || null
              : null,
        })),
      ),
      resources: mockResources(),
    } as T
  }
  if (command === 'run_publish_job') {
    const plan = payload.plan as PublishPlan
    const events: PublishEvent[] = [
      {
        type: 'job_started',
        job_id: plan.publish_job.job_id,
        article_ids: plan.publish_job.article_ids,
        platforms: plan.publish_job.platforms,
        mode: plan.mode,
      },
    ]
    for (const articleId of plan.publish_job.article_ids) {
      events.push({ type: 'article_started', job_id: plan.publish_job.job_id, article_id: articleId })
      for (const platform of plan.publish_job.platforms) {
        events.push({ type: 'platform_started', job_id: plan.publish_job.job_id, article_id: articleId, platform })
        events.push({
          type: 'platform_finished',
          job_id: plan.publish_job.job_id,
          article_id: articleId,
          platform,
          result: { platform, status: 'draft_only', summary: 'mock ok', returncode: 0 },
        })
      }
    }
    return {
      publish_job: {
        ...plan.publish_job,
        status: 'completed',
        current_step: 'done',
        success_count: plan.publish_job.article_ids.length * plan.publish_job.platforms.length,
      },
      events,
      results: events
        .filter((event) => event.type === 'platform_finished')
        .map((event) => event.result ?? {}),
    } as T
  }
  throw new Error(`Browser mock does not support command: ${String(command)}`)
}

export async function bridgeRequest<T>(payload: Record<string, unknown>): Promise<T> {
  if (!isTauriRuntime) {
    return mockBridgeRequest<T>(payload)
  }
  return invoke<T>('bridge_request', { payload })
}

export async function discoverResources() {
  return bridgeRequest<BridgeResources>({ command: 'discover_resources' })
}

export async function readWechatSettings() {
  return bridgeRequest<WechatSettingsPayload>({ command: 'read_wechat_settings' })
}

export async function saveWechatSettings(payload: { appId: string; secret: string; author: string }) {
  return bridgeRequest<WechatSettingsPayload>({
    command: 'save_wechat_settings',
    app_id: payload.appId,
    secret: payload.secret,
    author: payload.author,
  })
}

export async function readRecentHistory(limit = 20) {
  return bridgeRequest<HistoryPayload>({ command: 'read_recent_history', limit })
}

export async function importSources(payload: {
  importMode: 'paste' | 'file' | 'folder'
  sourcePath?: string
  pastedText?: string
}) {
  return bridgeRequest<{ job: ImportJob; resources: BridgeResources }>({
    command: 'import_sources',
    import_mode: payload.importMode,
    source_path: payload.sourcePath,
    pasted_text: payload.pastedText,
  })
}

export async function planPublishJob(payload: {
  drafts: PublishPlan['drafts']
  platforms: Platform[]
  mode: PublishPlan['mode']
  continueOnError: boolean
  templateMode: string
  manualThemeByArticle: Record<string, string>
  manualCoverByArticlePlatform: Record<string, string>
  coverMode: CoverMode
  aiDeclarationMode: AiDeclarationMode
  scheduledPublishAt: string | null
}) {
  return bridgeRequest<PublishPlan>({
    command: 'plan_publish_job',
    drafts: payload.drafts,
    platforms: payload.platforms,
    mode: payload.mode,
    continue_on_error: payload.continueOnError,
    template_mode: payload.templateMode,
    manual_theme_by_article: payload.manualThemeByArticle,
    manual_cover_by_article_platform: payload.manualCoverByArticlePlatform,
    cover_mode: payload.coverMode,
    ai_declaration_mode: payload.aiDeclarationMode,
    scheduled_publish_at: payload.scheduledPublishAt,
  })
}

export async function runPublishJobStream(
  plan: PublishPlan,
  onEvent: (event: PublishEvent) => void,
): Promise<PublishResult> {
  if (!isTauriRuntime) {
    const result = await mockBridgeRequest<PublishResult>({ command: 'run_publish_job', plan })
    result.events.forEach(onEvent)
    return result
  }

  const unlisten = await listen<PublishEvent>('publish-event', (event) => {
    onEvent(event.payload)
  })
  try {
    return await invoke<PublishResult>('run_publish_job_stream', { plan })
  } finally {
    await unlisten()
  }
}

export async function openArticleFileDialog(): Promise<string | null> {
  const result = await open({
    multiple: false,
    directory: false,
    filters: [
      {
        name: 'Article',
        extensions: ['md', 'txt', 'docx', 'pdf', 'png', 'jpg', 'jpeg', 'webp', 'gif'],
      },
    ],
  })
  return typeof result === 'string' ? result : null
}

export async function openFolderDialog(): Promise<string | null> {
  const result = await open({
    multiple: false,
    directory: true,
  })
  return typeof result === 'string' ? result : null
}
