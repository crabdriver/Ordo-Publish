import './style.css'

import {
  discoverResources,
  importSources,
  openArticleFileDialog,
  openFolderDialog,
  planPublishJob,
  readRecentHistory,
  readWechatSettings,
  runPublishJobStream,
  saveWechatSettings,
} from './bridge'
import type {
  AiDeclarationMode,
  ArticleDraft,
  BridgeResources,
  CoverMode,
  HistoryPayload,
  ImportJob,
  Platform,
  PublishEvent,
  PublishMode,
  PublishPlan,
  PublishResult,
  ThemeEntry,
  WechatConfigStatus,
} from './types'
import { compactHistoryPayload, compactPublishResult } from './publishResultMemory'
import { buildRetryPlanFromFailures, hasFailedResults, hasRetryableFailures, listFailedResults } from './recovery'
import { openUrl } from '@tauri-apps/plugin-opener'

import { PLATFORM_EDITOR_LINKS } from './platformLinks'
import { buildWechatBlockingMessage } from './wechatStatus'
import { describeBrowserSessionSummary, describePublishResult } from './workbenchFeedback'

const app = document.querySelector<HTMLDivElement>('#app')!

const PLATFORM_LABELS: Record<Platform, string> = {
  wechat: '微信',
  zhihu: '知乎',
  toutiao: '头条号',
  jianshu: '简书',
  yidian: '一点号',
}

const ALL_PLATFORMS: Platform[] = ['wechat', 'zhihu', 'toutiao', 'jianshu', 'yidian']
const COVER_CAPABLE_PLATFORMS = new Set<Platform>(['zhihu', 'toutiao', 'yidian'])
const TAURI_RUNTIME = '__TAURI_INTERNALS__' in window

type BusyAction = 'import' | 'plan' | 'publish' | 'refresh' | null

const EMPTY_WECHAT_STATUS: WechatConfigStatus = {
  env_file_exists: false,
  appid_ready: false,
  secret_ready: false,
  covers_ready: false,
  cover_count: 0,
  ai_cover_ready: false,
}

interface AppState {
  resources: BridgeResources | null
  history: HistoryPayload | null
  importJob: ImportJob | null
  drafts: ArticleDraft[]
  selectedArticleId: string | null
  platformSelection: Record<Platform, boolean>
  publishMode: PublishMode
  continueOnError: boolean
  templateMode: string
  coverMode: CoverMode
  aiDeclarationMode: AiDeclarationMode
  scheduledPublishAt: string | null
  manualThemeByArticle: Record<string, string>
  manualCoverByArticlePlatform: Record<string, string>
  plan: PublishPlan | null
  publishResult: PublishResult | null
  logs: string[]
  busy: BusyAction
  error: string | null
  status: string
  pasteModalOpen: boolean
  themeModalOpen: boolean
  settingsModalOpen: boolean
  pasteText: string
  wechatSettings: {
    appId: string
    secret: string
    author: string
  }
  wechatSettingsLoading: boolean
  wechatSettingsSaving: boolean
  /** 设置弹窗内标签：微信 / 浏览器登录 / 使用指引 */
  settingsTab: 'wechat' | 'browser' | 'guide'
}

const state: AppState = {
  resources: null,
  history: null,
  importJob: null,
  drafts: [],
  selectedArticleId: null,
  platformSelection: {
    wechat: true,
    zhihu: true,
    toutiao: true,
    jianshu: false,
    yidian: true,
  },
  publishMode: 'draft',
  continueOnError: false,
  templateMode: 'default',
  coverMode: 'auto',
  aiDeclarationMode: 'auto',
  scheduledPublishAt: null,
  manualThemeByArticle: {},
  manualCoverByArticlePlatform: {},
  plan: null,
  publishResult: null,
  logs: [],
  busy: null,
  error: null,
  status: '正在加载桌面工作台资源…',
  pasteModalOpen: false,
  themeModalOpen: false,
  settingsModalOpen: false,
  pasteText: '',
  wechatSettings: {
    appId: '',
    secret: '',
    author: '',
  },
  wechatSettingsLoading: false,
  wechatSettingsSaving: false,
  settingsTab: 'wechat',
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

function selectedArticle(): ArticleDraft | null {
  return state.drafts.find((item) => item.article_id === state.selectedArticleId) ?? null
}

function selectedPlatforms(): Platform[] {
  return ALL_PLATFORMS.filter((platform) => state.platformSelection[platform])
}

function themeOptions(): ThemeEntry[] {
  return state.resources?.theme_pool.entries ?? []
}

function wechatStatus(): WechatConfigStatus {
  return state.resources?.wechat.status ?? EMPTY_WECHAT_STATUS
}

function logLine(text: string) {
  state.logs = [...state.logs, text].slice(-120)
}

function updateStatus(text: string) {
  state.status = text
  render()
}

function showToast(message: string, type: 'info' | 'warning' | 'success' | 'error' = 'info') {
  let container = document.getElementById('toast-container')
  if (!container) {
    container = document.createElement('div')
    container.id = 'toast-container'
    document.body.appendChild(container)
  }
  const toast = document.createElement('div')
  toast.className = `toast toast--${type}`
  toast.textContent = message
  container.appendChild(toast)

  // Trigger reflow
  void toast.offsetWidth
  toast.classList.add('toast--visible')

  setTimeout(() => {
    toast.classList.remove('toast--visible')
    setTimeout(() => toast.remove(), 300)
  }, 3000)
}

async function openExternalUrl(url: string) {
  try {
    if (TAURI_RUNTIME) {
      await openUrl(url)
    } else {
      window.open(url, '_blank', 'noopener,noreferrer')
    }
  } catch (e) {
    showToast(e instanceof Error ? e.message : String(e), 'error')
  }
}

async function copyUrlToClipboard(url: string) {
  try {
    await navigator.clipboard.writeText(url)
    showToast('链接已复制到剪贴板', 'success')
  } catch (e) {
    showToast('复制失败，请手动复制', 'warning')
  }
}

/** 用于微信公众平台 IP 白名单：本机访问 api.weixin.qq.com 时的公网出口 IP（随网络变化）。 */
async function fetchAndCopyPublicIp() {
  try {
    const res = await fetch('https://api.ipify.org?format=json')
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }
    const data = (await res.json()) as { ip?: string }
    const ip = data.ip?.trim()
    if (!ip) {
      throw new Error('未解析到 IP')
    }
    await navigator.clipboard.writeText(ip)
    showToast(`已复制公网 IP：${ip}（请到公众平台 → IP 白名单添加）`, 'success')
  } catch (e) {
    showToast(
      e instanceof Error ? e.message : '获取公网 IP 失败，请在本机浏览器搜索「我的 IP」或查看微信 40164 报错中的 IP',
      'warning',
    )
  }
}

function renderBrowserLoginRows(): string {
  return PLATFORM_EDITOR_LINKS.map(
    (row) => `
        <div class="guide-platform-row">
          <span class="guide-platform-row__label">${escapeHtml(row.label)}</span>
          <div class="guide-platform-row__actions">
            <button type="button" class="ghost-button ghost-button--small" data-action="open-external-url" data-url="${escapeHtml(row.url)}">在浏览器打开</button>
            <button type="button" class="ghost-button ghost-button--small" data-action="copy-url" data-url="${escapeHtml(row.url)}">复制链接</button>
          </div>
        </div>
      `,
  ).join('')
}

function renderSettingsTabContent(): string {
  if (state.settingsTab === 'browser') {
    return `
      <div class="settings-tab-panel">
        <p>知乎、头条号、简书、一点号需要在 <strong>Ordo 使用的那台 Chrome（托管浏览器，带远程调试）</strong>里完成登录。</p>
        <p class="guide-tip">本应用不会内嵌各站网页。若「在浏览器打开」走的是系统默认浏览器，而发布用的是<strong>托管 Chrome</strong>，请用<strong>复制链接</strong>粘贴到托管 Chrome 地址栏再登录。</p>
        <h4 class="settings-subheading">各站写作页</h4>
        <div class="guide-platform-list">${renderBrowserLoginRows()}</div>
      </div>
    `
  }
  if (state.settingsTab === 'guide') {
    return `
      <div class="settings-tab-panel">
        <section class="guide-section">
          <h4>微信</h4>
          <p>本应用不在窗口内嵌微信网页。在「设置 → 微信」中填写 AppID、Secret、作者，用于写入<strong>草稿箱</strong>。</p>
        </section>
        <section class="guide-section">
          <h4>浏览器平台</h4>
          <p>知乎、头条、简书、一点需在 <strong>托管 Chrome</strong> 中登录；切换到「浏览器」标签可打开各站写作页链接。</p>
        </section>
        <section class="guide-section">
          <h4>各站写作页快捷方式</h4>
          <div class="guide-platform-list">${renderBrowserLoginRows()}</div>
        </section>
      </div>
    `
  }
  return `
    <div class="settings-tab-panel">
      <p>把微信发布到草稿箱需要的 AppID、Secret、Author 填在下方，保存后工作台会立即刷新状态。</p>
      <div class="wechat-ip-hint">
        <p class="wechat-ip-hint__title">IP 白名单（公众平台）</p>
        <p>在 <strong>微信公众平台 → 开发 → 基本配置 → IP 白名单</strong> 中，添加<strong>本机访问微信服务器时的公网出口 IP</strong>。换 Wi‑Fi、宽带重拨后 IP 可能变化。</p>
        <p>若接口返回 <code>40164</code>（invalid ip），以报错中写明的 IP 为准。</p>
        <button type="button" class="ghost-button" data-action="fetch-public-ip">获取并复制当前公网 IP</button>
      </div>
      ${
        state.wechatSettingsLoading
          ? '<div class="empty-inline">正在读取当前微信配置…</div>'
          : `
            <label class="field">
              <span>微信 AppID</span>
              <input id="wechat-appid-input" type="text" value="${escapeHtml(state.wechatSettings.appId)}" placeholder="例如 wx1234567890" />
            </label>
            <label class="field">
              <span>微信 Secret</span>
              <input id="wechat-secret-input" type="password" value="${escapeHtml(state.wechatSettings.secret)}" placeholder="请输入微信 Secret" />
            </label>
            <label class="field">
              <span>作者名</span>
              <input id="wechat-author-input" type="text" value="${escapeHtml(state.wechatSettings.author)}" placeholder="发布时默认作者名" />
            </label>
          `
      }
    </div>
  `
}

async function refreshWorkbenchData() {
  state.busy = 'refresh'
  render()
  try {
    const [resources, history] = await Promise.all([discoverResources(), readRecentHistory(10)])
    const compactedHistory = history ? compactHistoryPayload(history) : history
    state.resources = resources
    state.history = compactedHistory
    if (!state.templateMode) {
      state.templateMode = resources.defaults.template_mode
    }
    state.coverMode = state.coverMode || resources.defaults.cover_mode
    state.aiDeclarationMode = state.aiDeclarationMode || resources.defaults.ai_declaration_mode
    state.scheduledPublishAt = state.scheduledPublishAt || resources.defaults.scheduled_publish_at
    state.error = null
    
    // Show transient toasts for statuses
    const wechat = resources.wechat.status
    if (!wechat.appid_ready || !wechat.secret_ready) {
      showToast('微信配置未完成', 'warning')
    }
    const session = describeBrowserSessionSummary(resources)
    if (session.tone !== 'ready') {
      showToast(session.text, session.tone === 'danger' ? 'error' : 'warning')
    } else {
      showToast(session.text, 'success')
    }

    updateStatus(TAURI_RUNTIME ? '桌面桥已连接，等待导入内容。' : '当前在浏览器 Mock 模式下，可预览工作台结构。')
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
    updateStatus('加载桌面工作台资源失败')
  } finally {
    state.busy = null
    render()
  }
}

async function openSettingsModal(tab: 'wechat' | 'browser' | 'guide' = 'wechat') {
  state.settingsTab = tab
  state.settingsModalOpen = true
  state.wechatSettingsLoading = true
  render()
  try {
    const payload = await readWechatSettings()
    state.wechatSettings = {
      appId: payload.app_id,
      secret: payload.secret,
      author: payload.author,
    }
    state.error = null
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
  } finally {
    state.wechatSettingsLoading = false
    render()
  }
}

async function handleSaveWechatSettings() {
  state.wechatSettingsSaving = true
  render()
  try {
    await saveWechatSettings({
      appId: state.wechatSettings.appId.trim(),
      secret: state.wechatSettings.secret.trim(),
      author: state.wechatSettings.author.trim(),
    })
    state.settingsModalOpen = false
    state.settingsTab = 'wechat'
    state.error = null
    logLine('微信配置已保存')
    await refreshWorkbenchData()
    updateStatus('微信配置已保存，可直接继续发布到微信草稿箱。')
    if (state.drafts.length > 0) {
      await replanPublishJob()
    }
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
  } finally {
    state.wechatSettingsSaving = false
    render()
  }
}

async function replanPublishJob() {
  if (state.drafts.length === 0) {
    state.plan = null
    render()
    return
  }
  state.busy = 'plan'
  render()
  try {
    const plan = await planPublishJob({
      drafts: state.drafts,
      platforms: selectedPlatforms(),
      mode: state.publishMode,
      continueOnError: state.continueOnError,
      templateMode: state.templateMode,
      coverMode: state.coverMode,
      aiDeclarationMode: state.aiDeclarationMode,
      scheduledPublishAt: state.publishMode === 'publish' ? state.scheduledPublishAt : null,
      manualThemeByArticle: state.manualThemeByArticle,
      manualCoverByArticlePlatform: state.manualCoverByArticlePlatform,
    })
    state.plan = plan
    state.drafts = plan.drafts
    state.resources = plan.resources
    state.error = null
    updateStatus(`已规划 ${plan.publish_job.article_ids.length} 篇文章、${plan.publish_job.platforms.length} 个平台。`)
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
    updateStatus('发布规划失败')
  } finally {
    state.busy = null
    render()
  }
}

function handleRetryFailedPublish() {
  if (!state.plan || !state.publishResult || !hasFailedResults(state.publishResult)) {
    return
  }
  state.plan = buildRetryPlanFromFailures(state.plan, state.publishResult)
  logLine('已切换到失败项续跑计划')
  updateStatus(
    `已切换为失败项续跑：${state.plan.publish_job.article_ids.length} 篇文章 / ${state.plan.publish_job.platforms.length} 个平台。`,
  )
}

async function handlePasteImport() {
  if (!state.pasteText.trim()) {
    state.error = '请先粘贴正文内容'
    render()
    return
  }
  state.busy = 'import'
  render()
  try {
    const payload = await importSources({
      importMode: 'paste',
      pastedText: state.pasteText,
    })
    applyImportedJob(payload.job, payload.resources)
    state.pasteModalOpen = false
    state.pasteText = ''
    state.themeModalOpen = true
    await replanPublishJob()
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
  } finally {
    state.busy = null
    render()
  }
}

function applyImportedJob(job: ImportJob, resources: BridgeResources) {
  state.importJob = job
  state.resources = resources
  state.drafts = job.drafts
  state.selectedArticleId = job.drafts[0]?.article_id ?? null
  state.plan = null
  state.publishResult = null
  state.logs = []
  state.error = null
  state.manualThemeByArticle = {}
  state.manualCoverByArticlePlatform = {}
  if (job.failure_count > 0) {
    logLine(`导入完成：成功 ${job.article_count} 篇，失败 ${job.failure_count} 篇。`)
    for (const item of job.failures.slice(0, 10)) {
      logLine(`导入失败 [${item.source_kind}] ${item.source_path ?? '未知来源'}: ${item.message}`)
    }
    updateStatus(`已导入 ${job.article_count} 篇内容，另有 ${job.failure_count} 篇导入失败，请检查日志与 manifest。`)
    return
  }
  updateStatus(`已导入 ${job.article_count} 篇内容，等待模板与封面确认。`)
}

function restorePlanFromHistory(failuresOnly: boolean) {
  const lastPlan = state.history?.last_plan
  if (!lastPlan) {
    return
  }
  const lastResult = state.history?.last_result ?? null
  const nextPlan = failuresOnly && lastResult ? buildRetryPlanFromFailures(lastPlan, lastResult) : lastPlan

  state.importJob = {
    job_id: `restore-${nextPlan.publish_job.job_id}`,
    import_mode: 'restore',
    source_path: null,
    pasted_preview: null,
    imported_at: new Date().toISOString(),
    source_count: nextPlan.drafts.length,
    manifest_path: null,
    article_count: nextPlan.drafts.length,
    failure_count: 0,
    failures: [],
    drafts: nextPlan.drafts,
  }
  state.resources = nextPlan.resources
  state.drafts = nextPlan.drafts
  state.selectedArticleId = nextPlan.drafts[0]?.article_id ?? null
  state.publishMode = nextPlan.mode
  state.continueOnError = nextPlan.continue_on_error
  state.templateMode = nextPlan.drafts[0]?.template_mode ?? nextPlan.resources.defaults.template_mode
  state.coverMode = nextPlan.cover_mode
  state.aiDeclarationMode = nextPlan.ai_declaration_mode
  state.scheduledPublishAt = nextPlan.scheduled_publish_at ?? nextPlan.resources.defaults.scheduled_publish_at
  state.plan = nextPlan
  state.publishResult = lastResult
  state.logs = []
  state.error = null
  state.manualThemeByArticle = Object.fromEntries(
    nextPlan.template_assignments
      .filter((item) => item.is_manual_override && item.theme_id)
      .map((item) => [item.article_id, item.theme_id as string]),
  )
  state.manualCoverByArticlePlatform = Object.fromEntries(
    nextPlan.cover_assignments
      .filter((item) => item.is_manual_override && item.cover_path)
      .map((item) => [`${item.article_id}:${item.platform}`, item.cover_path as string]),
  )
  logLine(failuresOnly ? '已恢复上次失败项续跑计划' : '已恢复上次任务计划')
  updateStatus(
    failuresOnly
      ? `已恢复上次失败项：${nextPlan.publish_job.article_ids.length} 篇文章 / ${nextPlan.publish_job.platforms.length} 个平台。`
      : `已恢复上次任务：${nextPlan.publish_job.article_ids.length} 篇文章 / ${nextPlan.publish_job.platforms.length} 个平台。`,
  )
}

async function handleFileImport() {
  const selected = await openArticleFileDialog()
  if (!selected) {
    return
  }
  state.busy = 'import'
  render()
  try {
    const payload = await importSources({
      importMode: 'file',
      sourcePath: selected,
    })
    applyImportedJob(payload.job, payload.resources)
    state.themeModalOpen = true
    await replanPublishJob()
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
  } finally {
    state.busy = null
    render()
  }
}

async function handleFolderImport() {
  const selected = await openFolderDialog()
  if (!selected) {
    return
  }
  state.busy = 'import'
  render()
  try {
    const payload = await importSources({
      importMode: 'folder',
      sourcePath: selected,
    })
    applyImportedJob(payload.job, payload.resources)
    state.themeModalOpen = true
    await replanPublishJob()
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
  } finally {
    state.busy = null
    render()
  }
}

function eventSummary(event: PublishEvent): string {
  switch (event.type) {
    case 'job_started':
      return '发布任务已启动'
    case 'article_started':
      return `开始处理文章 ${event.article_id ?? ''}`
    case 'platform_started':
      return `开始发布到 ${PLATFORM_LABELS[event.platform ?? 'wechat'] ?? event.platform ?? ''}`
    case 'platform_finished':
      return `${PLATFORM_LABELS[event.platform ?? 'wechat'] ?? event.platform ?? ''} 完成，状态 ${String(event.result?.status ?? 'unknown')}`
    case 'job_finished':
      return '发布任务结束'
    default:
      return event.type
  }
}

async function handleStartPublish() {
  if (!state.plan) {
    await replanPublishJob()
  }
  if (!state.plan) {
    return
  }

  state.busy = 'publish'
  state.logs = []
  state.publishResult = null
  render()
  logLine('开始执行发布任务…')

  try {
    const result = await runPublishJobStream(state.plan, (event) => {
      logLine(eventSummary(event))
      render()
    })
    state.publishResult = compactPublishResult(result)
    state.plan = {
      ...state.plan,
      publish_job: result.publish_job,
    }
    state.error = null
    await refreshWorkbenchData()
    updateStatus(`发布完成：成功 ${result.publish_job.success_count}，失败 ${result.publish_job.failure_count}。`)
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
    logLine(`发布失败：${state.error}`)
    updateStatus('发布任务执行失败')
  } finally {
    state.busy = null
    render()
  }
}

function renderArticleList() {
  if (state.drafts.length === 0) {
    return `
      <div class="empty-block">
        <span class="empty-inline">尚未导入文章</span>
      </div>
    `
  }

  return `
    <div class="article-list">
      ${state.drafts
        .map((draft) => {
          const active = draft.article_id === state.selectedArticleId ? 'is-active' : ''
          return `
            <button class="article-card ${active}" data-action="select-article" data-article-id="${draft.article_id}">
              <span class="article-card__title">${escapeHtml(draft.title)}</span>
              <span class="article-card__meta">${escapeHtml(draft.source_kind)} · ${draft.word_count} 字</span>
            </button>
          `
        })
        .join('')}
    </div>
  `
}

function renderHistoryPanel() {
  const records = state.history?.records ?? []
  const lastPlan = state.history?.last_plan
  const lastResult = state.history?.last_result
  const sessionSummary = (state.history?.session?.summary as { total_articles?: number } | undefined) ?? null
  const recovery = state.history?.recovery
  const canRestorePlan = Boolean(lastPlan && (recovery?.can_restore_plan ?? true))
  const canRestoreFailures = Boolean(
    lastPlan && lastResult && hasFailedResults(lastResult) && (recovery?.can_restore_failures ?? true),
  )

  if (!lastPlan && records.length === 0 && !sessionSummary) {
    return ''
  }

  const recoveryWarning =
    recovery && recovery.issues && recovery.issues.length > 0
      ? `<span class="recovery-warning" title="${escapeHtml(recovery.issues.join('; '))}">${escapeHtml(recovery.status)}</span>`
      : ''

  const latestTask = lastPlan
    ? `
        <div class="history-item">
          <strong>上次任务 ${escapeHtml(lastPlan.publish_job.job_id)}</strong>
          <span>${lastPlan.publish_job.article_ids.length} 篇 / ${lastPlan.publish_job.platforms.length} 平台</span>
          <span>${
            lastResult
              ? escapeHtml(`最近结果：成功 ${lastResult.publish_job.success_count}，失败 ${lastResult.publish_job.failure_count}`)
              : escapeHtml(`控制台 session：${sessionSummary?.total_articles ?? 0} 篇`)
          }</span>
          ${recoveryWarning}
        </div>
        <div class="publish-actions">
          <button class="ghost-button" data-action="restore-latest-plan" ${canRestorePlan ? '' : 'disabled'}>恢复上次任务</button>
          <button class="ghost-button" data-action="restore-failed-plan" ${canRestoreFailures ? '' : 'disabled'}>恢复失败项</button>
        </div>
      `
    : sessionSummary
      ? `
          <div class="history-item">
            <strong>最近 session</strong>
            <span>${sessionSummary.total_articles ?? 0} 篇文章</span>
            <span>可在重新导入后结合最近结果继续恢复。</span>
          </div>
        `
      : ''

  const recentRecords = records
    .map(
      (record) => `
        <div class="history-item">
          <strong>${escapeHtml(record.platform ?? '')}</strong>
          <span>${escapeHtml(record.article_id ?? record.article ?? '')}</span>
          <span>${escapeHtml(record.status ?? '')}</span>
        </div>
      `,
    )
    .join('')

  return `${latestTask}${recentRecords}`
}

function renderTemplatePanel(article: ArticleDraft | null) {
  const options = themeOptions()
  const manualTheme = article ? state.manualThemeByArticle[article.article_id] ?? '' : ''
  return `
    <section class="panel">
      <div class="panel__head">
        <div>
          <h3>模板配置</h3>
        </div>
        <button class="ghost-button" data-action="open-theme-modal">批量设置</button>
      </div>
      <div class="field">
        <span>全局模板模式</span>
        <div class="segmented-control" id="template-mode-segment">
          <label class="segmented-control__item">
            <input type="radio" name="template-mode" value="default" ${state.templateMode === 'default' ? 'checked' : ''}>
            <span>默认随机</span>
          </label>
          <label class="segmented-control__item">
            <input type="radio" name="template-mode" value="custom" ${state.templateMode === 'custom' ? 'checked' : ''}>
            <span>逐篇自定义</span>
          </label>
        </div>
      </div>
      ${
        article
          ? `
            <label class="field">
              <span>当前文章主题</span>
              <select id="theme-select" ${options.length === 0 ? 'disabled' : ''}>
                <option value="">${options.length === 0 ? '当前没有主题池' : '按系统默认分配'}</option>
                ${options
                  .map(
                    (entry) => `
                      <option value="${entry.theme_id}" ${manualTheme === entry.theme_id ? 'selected' : ''}>
                        ${escapeHtml(entry.display_name)}
                      </option>
                    `,
                  )
                  .join('')}
              </select>
            </label>
          `
          : '<div class="empty-inline">还没有选中的文章。</div>'
      }
    </section>
  `
}

function renderPreview(article: ArticleDraft | null) {
  if (!article) {
    return `
      <section class="panel panel--preview">
        <h3>正文预览</h3>
        <div class="empty-inline">暂无预览</div>
      </section>
    `
  }
  return `
    <section class="panel panel--preview">
      <div class="panel__head">
        <div>
          <h3>${escapeHtml(article.title)}</h3>
          <p>${article.word_count} 字 · ${escapeHtml(article.source_kind)}</p>
        </div>
      </div>
      <pre class="markdown-preview">${escapeHtml(article.body_markdown)}</pre>
    </section>
  `
}

function renderCoverPanel(article: ArticleDraft | null) {
  const coverPool = state.resources?.cover_pool
  const selected = selectedPlatforms()
  return `
    <section class="panel panel--covers">
      <h3>封面预览</h3>
      <div class="platform-grid">
        ${ALL_PLATFORMS.map((platform) => {
          const key = article ? `${article.article_id}:${platform}` : ''
          const value = key ? state.manualCoverByArticlePlatform[key] ?? '' : ''
          return `
            <div class="platform-row platform-row--card">
              <div class="platform-row__line1">
                <label class="platform-toggle">
                  <input type="checkbox" data-platform-toggle="${platform}" ${state.platformSelection[platform] ? 'checked' : ''}>
                  <span class="platform-icon platform-icon--${platform}"></span>
                  <span class="platform-name">${PLATFORM_LABELS[platform]}</span>
                </label>
              </div>
              <div class="platform-row__line2">
              ${
                COVER_CAPABLE_PLATFORMS.has(platform)
                  ? `
                    <select class="cover-select" data-cover-select="${platform}" ${!article || !coverPool?.ok || !selected.includes(platform) ? 'disabled' : ''}>
                      <option value="">${coverPool?.ok ? '自动分配封面' : '默认'}</option>
                      ${(coverPool?.paths ?? [])
                        .map(
                          (path) => `
                            <option value="${path}" ${value === path ? 'selected' : ''}>${escapeHtml(path.split('/').pop() ?? path)}</option>
                          `,
                        )
                        .join('')}
                    </select>
                  `
                  : `<span class="platform-empty-slot">此平台不走浏览器封面</span>`
              }
              </div>
            </div>
          `
        }).join('')}
      </div>
    </section>
  `
}

function renderExecutionPanel() {
  const publishJob = state.plan?.publish_job
  const wechatBlock = buildWechatBlockingMessage(selectedPlatforms(), wechatStatus())
  const failedResults = listFailedResults(state.publishResult)
  const canRetryFailures = Boolean(state.plan) && hasFailedResults(state.publishResult)
  const hasDirectRetryHint = hasRetryableFailures(state.publishResult)
  const showsToutiaoSchedule = state.publishMode === 'publish' && selectedPlatforms().includes('toutiao')
  const disabled = state.drafts.length === 0 || state.busy === 'publish' || Boolean(wechatBlock)
  return `
    <section class="panel">
      <div class="panel__head">
        <div>
          <h3>执行区</h3>
          ${publishJob ? `<p class="subtle">任务 ${publishJob.job_id}</p>` : ''}
        </div>
      </div>
      <div class="publish-actions">
        <select id="publish-mode-select">
          <option value="draft" ${state.publishMode === 'draft' ? 'selected' : ''}>存草稿</option>
          <option value="publish" ${state.publishMode === 'publish' ? 'selected' : ''}>直接发布</option>
        </select>
        <select id="cover-mode-select">
          <option value="auto" ${state.coverMode === 'auto' ? 'selected' : ''}>封面: 自动</option>
          <option value="force_on" ${state.coverMode === 'force_on' ? 'selected' : ''}>封面: 强制开启</option>
          <option value="force_off" ${state.coverMode === 'force_off' ? 'selected' : ''}>封面: 关闭</option>
        </select>
        <select id="ai-declaration-mode-select">
          <option value="auto" ${state.aiDeclarationMode === 'auto' ? 'selected' : ''}>AI 声明: 自动</option>
          <option value="force_on" ${state.aiDeclarationMode === 'force_on' ? 'selected' : ''}>AI 声明: 强制开启</option>
          <option value="force_off" ${state.aiDeclarationMode === 'force_off' ? 'selected' : ''}>AI 声明: 关闭</option>
        </select>
        ${
          showsToutiaoSchedule
            ? `<input id="scheduled-publish-at-input" type="datetime-local" value="${state.scheduledPublishAt ?? ''}" title="头条号定时发布时间">`
            : ''
        }
        <label class="switch-field">
          <input id="continue-on-error-checkbox" type="checkbox" ${state.continueOnError ? 'checked' : ''}>
          <span>遇错继续</span>
        </label>
      </div>
      <div class="job-summary">
        <div><strong>${publishJob?.article_ids.length ?? 0}</strong><span>文章</span></div>
        <div><strong>${publishJob?.platforms.length ?? 0}</strong><span>平台</span></div>
        <div><strong>${publishJob?.success_count ?? 0}</strong><span>成功</span></div>
        <div><strong>${publishJob?.failure_count ?? 0}</strong><span>失败</span></div>
      </div>
      ${wechatBlock ? `<div class="warn-banner">${escapeHtml(wechatBlock)}</div>` : ''}
      ${
        failedResults.length
          ? `
            <div class="warn-banner">
              最近一次发布有 ${failedResults.length} 个失败项。${
                canRetryFailures
                  ? hasDirectRetryHint
                    ? '可直接切换为失败项续跑。'
                    : '可切换为失败项续跑，建议先检查登录态、页面环境或平台状态。'
                  : '当前没有可恢复的失败项。'
              }
            </div>
            <div class="result-list">
              ${failedResults
                .map(
                  (item) => `
                    <div class="result-item">
                      <strong>${escapeHtml(PLATFORM_LABELS[item.platform] ?? item.platform)}</strong>
                      <span>${escapeHtml(item.article_id)}</span>
                      <span>${escapeHtml(item.summary || (item.retryable ? '可重试失败' : '失败'))}</span>
                    </div>
                  `,
                )
                .join('')}
            </div>
            <button class="ghost-button" data-action="retry-failed-publish" ${canRetryFailures ? '' : 'disabled'}>
              仅重试失败项
            </button>
          `
          : ''
      }
      <button class="primary-button primary-button--large" data-action="start-publish" ${disabled ? 'disabled' : ''}>
        ${state.busy === 'publish' ? '发布中…' : '开始发布'}
      </button>
    </section>
  `
}

function renderLogs() {
  const resultDetails = (state.publishResult?.results ?? []) as Array<Record<string, unknown>>
  return `
    <section class="panel panel--logs">
      <div class="panel__head">
        <div>
          <h3>日志与状态</h3>
        </div>
      </div>
      <div class="log-list">
        ${(state.logs.length ? state.logs : ['等待执行日志…'])
          .map((line) => `<div class="log-line">${escapeHtml(line)}</div>`)
          .join('')}
      </div>
      ${
        resultDetails.length
          ? `
            <div class="result-list">
              ${resultDetails
                .map(
                  (result) => `
                    <div class="result-item">
                      <strong>${escapeHtml(PLATFORM_LABELS[(result.platform as Platform | undefined) ?? 'wechat'] ?? String(result.platform ?? 'unknown'))}</strong>
                      <span>${escapeHtml(String(result.article_id ?? result.article ?? ''))}</span>
                      <span>${escapeHtml(describePublishResult(result))}</span>
                    </div>
                  `,
                )
                .join('')}
            </div>
          `
          : ''
      }
    </section>
  `
}

function renderModal() {
  if (state.pasteModalOpen) {
    return `
      <div class="modal-backdrop">
        <div class="modal">
          <div class="modal__head">
            <h3>粘贴导入</h3>
            <button class="ghost-button" data-action="close-paste-modal">关闭</button>
          </div>
          <div class="modal__body">
            <textarea id="paste-input" placeholder="把正文粘贴到这里，第一行会被识别为标题。">${escapeHtml(state.pasteText)}</textarea>
          </div>
          <div class="modal__actions">
            <button class="ghost-button" data-action="close-paste-modal">取消</button>
            <button class="primary-button" data-action="confirm-paste-import">导入</button>
          </div>
        </div>
      </div>
    `
  }

  if (state.themeModalOpen) {
    return `
      <div class="modal-backdrop">
        <div class="modal">
          <div class="modal__head">
            <h3>全局模板选择</h3>
            <button class="ghost-button" data-action="close-theme-modal">完成</button>
          </div>
          <div class="modal__body">
            <p>先确定全局模板模式，再在主界面逐篇覆盖。</p>
            <label class="field">
              <span>模板模式</span>
          <div class="segmented-control">
            <label>
              <input type="radio" name="modal-template-mode" value="default" ${state.templateMode === 'default' ? 'checked' : ''}>
              <span>默认随机</span>
            </label>
            <label>
              <input type="radio" name="modal-template-mode" value="custom" ${state.templateMode === 'custom' ? 'checked' : ''}>
              <span>逐篇自定义</span>
            </label>
          </div>
            </label>
          </div>
          <div class="modal__actions">
            <button class="primary-button" data-action="close-theme-modal">确认</button>
          </div>
        </div>
      </div>
    `
  }

  if (state.settingsModalOpen) {
    const wechatTab = state.settingsTab === 'wechat'
    const footerWechat = `
          <div class="modal__actions">
            <button class="ghost-button" data-action="close-settings-modal" ${state.wechatSettingsSaving ? 'disabled' : ''}>关闭</button>
            <button class="primary-button" data-action="save-wechat-settings" ${state.wechatSettingsSaving || state.wechatSettingsLoading ? 'disabled' : ''}>
              ${state.wechatSettingsSaving ? '保存中…' : '保存'}
            </button>
          </div>`
    const footerOther = `
          <div class="modal__actions">
            <button class="primary-button" data-action="close-settings-modal">关闭</button>
          </div>`
    return `
      <div class="modal-backdrop">
        <div class="modal modal--wide modal--settings">
          <div class="modal__head">
            <h3>设置</h3>
            <button class="ghost-button" data-action="close-settings-modal">关闭</button>
          </div>
          <div class="modal__body modal__body--settings">
            <div class="segmented-control settings-tabs">
              <label class="segmented-control__item">
                <input type="radio" name="settings-tab" value="wechat" ${state.settingsTab === 'wechat' ? 'checked' : ''}>
                <span>微信</span>
              </label>
              <label class="segmented-control__item">
                <input type="radio" name="settings-tab" value="browser" ${state.settingsTab === 'browser' ? 'checked' : ''}>
                <span>浏览器</span>
              </label>
              <label class="segmented-control__item">
                <input type="radio" name="settings-tab" value="guide" ${state.settingsTab === 'guide' ? 'checked' : ''}>
                <span>指引</span>
              </label>
            </div>
            ${renderSettingsTabContent()}
          </div>
          ${wechatTab ? footerWechat : footerOther}
        </div>
      </div>
    `
  }

  return ''
}

function render() {
  const article = selectedArticle()
  app.innerHTML = `
    <div class="shell">
      <header class="topbar">
        <div class="topbar__brand">
          <img class="topbar__logo" src="/logo.svg" width="24" height="24" alt="" />
          <strong>Ordo</strong>
          ${state.status ? `<span class="topbar__status">${escapeHtml(state.status)}</span>` : ''}
        </div>
        <div class="topbar__actions">
          <button class="icon-button" data-action="open-settings-modal" title="设置">
            <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
          </button>
          <button class="icon-button" data-action="open-paste-modal" title="粘贴导入">
            <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"></path><rect x="8" y="2" width="8" height="4" rx="1" ry="1"></rect></svg>
          </button>
          <div class="dropdown">
            <button class="icon-button" title="导入文件/文件夹">
              <svg viewBox="0 0 24 24" width="20" height="20" stroke="currentColor" stroke-width="2" fill="none"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
            </button>
            <div class="dropdown-menu">
              <button class="dropdown-item" data-action="import-file">导入单文件</button>
              <button class="dropdown-item" data-action="import-folder">导入文件夹</button>
            </div>
          </div>
        </div>
      </header>

      ${state.error ? `<div class="error-banner">${escapeHtml(state.error)}</div>` : ''}

      <main class="workbench">
        <aside class="column column--left">
          <section class="panel panel--articles">
            <div class="panel__head">
              <div>
                <h3>文章列表</h3>
              </div>
              <button class="icon-button" data-action="refresh" title="刷新资源">
                <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2" fill="none"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
              </button>
            </div>
            ${renderArticleList()}
          </section>
          <section class="panel panel--history">
            <h3>最近结果</h3>
            ${renderHistoryPanel()}
          </section>
          ${renderLogs()}
        </aside>

        <section class="column column--center">
          ${renderTemplatePanel(article)}
          ${renderPreview(article)}
        </section>

        <aside class="column column--right">
          ${renderCoverPanel(article)}
          ${renderExecutionPanel()}
        </aside>
      </main>

      ${renderModal()}
    </div>
  `

  bindEvents()
}

function bindEvents() {
  app.querySelectorAll<HTMLElement>('[data-action]').forEach((element) => {
    element.onclick = async () => {
      const action = element.dataset.action
      switch (action) {
        case 'open-paste-modal':
          state.pasteModalOpen = true
          render()
          break
        case 'close-paste-modal':
          state.pasteModalOpen = false
          render()
          break
        case 'confirm-paste-import':
          await handlePasteImport()
          break
        case 'import-file':
          await handleFileImport()
          break
        case 'import-folder':
          await handleFolderImport()
          break
        case 'refresh':
          await refreshWorkbenchData()
          if (state.drafts.length > 0) {
            await replanPublishJob()
          }
          break
        case 'open-settings-modal':
          await openSettingsModal()
          break
        case 'fetch-public-ip':
          await fetchAndCopyPublicIp()
          break
        case 'open-external-url': {
          const url = element.dataset.url ?? ''
          if (url) {
            await openExternalUrl(url)
          }
          break
        }
        case 'copy-url': {
          const url = element.dataset.url ?? ''
          if (url) {
            await copyUrlToClipboard(url)
          }
          break
        }
        case 'close-settings-modal':
          state.settingsModalOpen = false
          state.settingsTab = 'wechat'
          render()
          break
        case 'save-wechat-settings':
          await handleSaveWechatSettings()
          break
        case 'open-theme-modal':
          state.themeModalOpen = true
          render()
          break
        case 'close-theme-modal':
          state.themeModalOpen = false
          render()
          break
        case 'start-publish':
          await handleStartPublish()
          break
        case 'retry-failed-publish':
          handleRetryFailedPublish()
          break
        case 'restore-latest-plan':
          restorePlanFromHistory(false)
          break
        case 'restore-failed-plan':
          restorePlanFromHistory(true)
          break
        case 'select-article':
          state.selectedArticleId = element.dataset.articleId ?? null
          render()
          break
        default:
          break
      }
    }
  })

  const pasteInput = app.querySelector<HTMLTextAreaElement>('#paste-input')
  if (pasteInput) {
    pasteInput.oninput = () => {
      state.pasteText = pasteInput.value
    }
  }

  const wechatAppIdInput = app.querySelector<HTMLInputElement>('#wechat-appid-input')
  if (wechatAppIdInput) {
    wechatAppIdInput.oninput = () => {
      state.wechatSettings.appId = wechatAppIdInput.value
    }
  }

  const wechatSecretInput = app.querySelector<HTMLInputElement>('#wechat-secret-input')
  if (wechatSecretInput) {
    wechatSecretInput.oninput = () => {
      state.wechatSettings.secret = wechatSecretInput.value
    }
  }

  const wechatAuthorInput = app.querySelector<HTMLInputElement>('#wechat-author-input')
  if (wechatAuthorInput) {
    wechatAuthorInput.oninput = () => {
      state.wechatSettings.author = wechatAuthorInput.value
    }
  }

  app.querySelectorAll<HTMLInputElement>('input[name="settings-tab"]').forEach((input) => {
    input.onchange = () => {
      if (!input.checked) {
        return
      }
      const value = input.value as 'wechat' | 'browser' | 'guide'
      if (value === state.settingsTab) {
        return
      }
      state.settingsTab = value
      render()
    }
  })

  app.querySelectorAll<HTMLInputElement>('input[name="template-mode"]').forEach((input) => {
    input.onchange = async () => {
      if (input.checked) {
        state.templateMode = input.value
        await replanPublishJob()
      }
    }
  })

  app.querySelectorAll<HTMLInputElement>('input[name="modal-template-mode"]').forEach((input) => {
    input.onchange = async () => {
      if (input.checked) {
        state.templateMode = input.value
        await replanPublishJob()
        render()
      }
    }
  })

  const themeSelect = app.querySelector<HTMLSelectElement>('#theme-select')
  if (themeSelect && state.selectedArticleId) {
    themeSelect.onchange = async () => {
      if (themeSelect.value) {
        state.manualThemeByArticle[state.selectedArticleId!] = themeSelect.value
      } else {
        delete state.manualThemeByArticle[state.selectedArticleId!]
      }
      await replanPublishJob()
    }
  }

  const publishModeSelect = app.querySelector<HTMLSelectElement>('#publish-mode-select')
  if (publishModeSelect) {
    publishModeSelect.onchange = async () => {
      state.publishMode = publishModeSelect.value as PublishMode
      await replanPublishJob()
    }
  }

  const coverModeSelect = app.querySelector<HTMLSelectElement>('#cover-mode-select')
  if (coverModeSelect) {
    coverModeSelect.onchange = async () => {
      state.coverMode = coverModeSelect.value as CoverMode
      await replanPublishJob()
    }
  }

  const aiDeclarationModeSelect = app.querySelector<HTMLSelectElement>('#ai-declaration-mode-select')
  if (aiDeclarationModeSelect) {
    aiDeclarationModeSelect.onchange = async () => {
      state.aiDeclarationMode = aiDeclarationModeSelect.value as AiDeclarationMode
      await replanPublishJob()
    }
  }

  const scheduledPublishAtInput = app.querySelector<HTMLInputElement>('#scheduled-publish-at-input')
  if (scheduledPublishAtInput) {
    scheduledPublishAtInput.onchange = async () => {
      state.scheduledPublishAt = scheduledPublishAtInput.value.trim() || null
      await replanPublishJob()
    }
  }

  const continueOnErrorCheckbox = app.querySelector<HTMLInputElement>('#continue-on-error-checkbox')
  if (continueOnErrorCheckbox) {
    continueOnErrorCheckbox.onchange = async () => {
      state.continueOnError = continueOnErrorCheckbox.checked
      await replanPublishJob()
    }
  }

  app.querySelectorAll<HTMLInputElement>('[data-platform-toggle]').forEach((input) => {
    input.onchange = async () => {
      const platform = input.dataset.platformToggle as Platform
      state.platformSelection[platform] = input.checked
      await replanPublishJob()
    }
  })

  app.querySelectorAll<HTMLSelectElement>('[data-cover-select]').forEach((select) => {
    select.onchange = async () => {
      if (!state.selectedArticleId) {
        return
      }
      const platform = select.dataset.coverSelect as Platform
      const key = `${state.selectedArticleId}:${platform}`
      if (select.value) {
        state.manualCoverByArticlePlatform[key] = select.value
      } else {
        delete state.manualCoverByArticlePlatform[key]
      }
      await replanPublishJob()
    }
  })
}

render()
void refreshWorkbenchData()
