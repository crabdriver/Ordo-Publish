export type Platform = 'wechat' | 'zhihu' | 'toutiao' | 'jianshu' | 'yidian'
export type PublishMode = 'draft' | 'publish'
export type TemplateMode = 'default' | 'custom'
export type CoverMode = 'auto' | 'force_on' | 'force_off'
export type AiDeclarationMode = 'auto' | 'force_on' | 'force_off'

export interface ArticleDraft {
  article_id: string
  title: string
  body_markdown: string
  source_path: string | null
  source_kind: string
  image_paths: string[]
  word_count: number
  template_mode: string
  theme_name: string | null
  is_config_complete: boolean
}

export interface ImportFailure {
  source_path: string | null
  source_kind: string
  error_type: string
  message: string
}

export interface ImportJob {
  job_id: string
  import_mode: string
  source_path: string | null
  pasted_preview: string | null
  imported_at: string
  source_count: number
  manifest_path: string | null
  article_count: number
  failure_count: number
  failures: ImportFailure[]
  drafts: ArticleDraft[]
}

export interface ThemeEntry {
  theme_id: string
  display_name: string
}

export interface ThemePool {
  themes_dir: string
  theme_ids: string[]
  count: number
  entries: ThemeEntry[]
}

export interface CoverPool {
  ok: boolean
  cover_dir: string
  paths: string[]
  count: number
  error: string | null
}

export interface WechatConfigStatus {
  env_file_exists: boolean
  appid_ready: boolean
  secret_ready: boolean
  covers_ready: boolean
  cover_count: number
  ai_cover_ready: boolean
}

export interface WechatSettings {
  app_id: string
  secret: string
  author: string
}

export interface WechatSettingsPayload extends WechatSettings {
  status: WechatConfigStatus
}

export interface BrowserRequirements {
  browser_platforms: Platform[]
  remote_debugging_required: boolean
  login_required_platforms: Platform[]
  managed_session: BrowserManagedSession
  session_state: BrowserSessionState
}

export interface BrowserManagedSession {
  enabled: boolean
  remind_after_days: number
  profile_dir: string
  debug_port: number
}

export interface BrowserSessionPlatformState {
  status: string
  last_checked_at?: string | null
  last_healthy_at?: string | null
  last_relogin_required_at?: string | null
  last_reminded_at?: string | null
  current_url?: string | null
  page_state?: string | null
}

export interface BrowserSessionState {
  mode: string
  last_checked_at: string | null
  updated_at?: string | null
  platforms: Record<string, BrowserSessionPlatformState>
  expiring_platforms: Platform[]
  relogin_required_platforms: Platform[]
}

export interface RuntimeDiagnostics {
  repo_root: string
  python_executable: string
}

export interface BridgeResources {
  theme_pool: ThemePool
  cover_pool: CoverPool
  config_warning: string | null
  wechat: {
    settings: WechatSettings
    status: WechatConfigStatus
  }
  browser: BrowserRequirements
  runtime: RuntimeDiagnostics
  defaults: {
    template_mode: string
    cover_repeat_window: number
    cover_mode: CoverMode
    ai_declaration_mode: AiDeclarationMode
    scheduled_publish_at: string | null
  }
}

export interface TemplateAssignment {
  article_id: string
  template_mode: string
  theme_id: string | null
  theme_name: string | null
  is_random: boolean
  is_manual_override: boolean
  is_confirmed: boolean
}

export interface CoverAssignment {
  article_id: string
  platform: Platform
  cover_path: string | null
  cover_source: string
  is_random: boolean
  is_manual_override: boolean
}

export interface PublishJob {
  job_id: string
  article_ids: string[]
  platforms: Platform[]
  status: string
  current_step: string
  success_count: number
  failure_count: number
  skip_count: number
  recoverable: boolean
  error_summary: string
  scheduled_publish_at?: string | null
}

export interface StagedArticle {
  article_id: string
  markdown_path: string
}

export interface PublishContext {
  article_id: string
  platform: Platform
  markdown_path: string
  theme_name: string | null
  template_mode: string
  cover_path: string | null
  cover_mode: CoverMode
  ai_declaration_mode: AiDeclarationMode
  scheduled_publish_at?: string | null
}

export interface PublishPlan {
  publish_job: PublishJob
  mode: PublishMode
  continue_on_error: boolean
  cover_mode: CoverMode
  ai_declaration_mode: AiDeclarationMode
  scheduled_publish_at?: string | null
  drafts: ArticleDraft[]
  template_assignments: TemplateAssignment[]
  cover_assignments: CoverAssignment[]
  staged_articles: StagedArticle[]
  context_map: PublishContext[]
  resources: BridgeResources
}

export interface PublishResult {
  publish_job: PublishJob
  events: PublishEvent[]
  results: Array<Record<string, unknown>>
}

export interface HistoryRecovery {
  status: 'recoverable' | 'snapshot_corrupted' | 'result_missing' | 'session_only' | 'empty'
  issues: string[]
  missing_staged_articles: Array<{
    article_id: string
    markdown_path: string
  }>
  can_restore_plan: boolean
  can_restore_failures: boolean
}

export interface HistoryPayload {
  records: Array<Record<string, string>>
  session: Record<string, unknown> | null
  last_plan: PublishPlan | null
  last_result: PublishResult | null
  recovery: HistoryRecovery
}

export interface PublishEvent {
  type: string
  job_id: string
  article_id?: string
  platform?: Platform
  title?: string
  markdown_path?: string
  result?: Record<string, unknown>
  publish_job?: PublishJob
  result_count?: number
  article_ids?: string[]
  platforms?: Platform[]
  mode?: PublishMode
}
