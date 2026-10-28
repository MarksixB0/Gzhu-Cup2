import { reactive, readonly } from 'vue'
import type { CareMessage, CareStatus, Settings, ThreadMeta, Turn } from '../types'
import { DEFAULT_SETTINGS } from '../types'

/* ================= 全局轻量 store(无额外依赖) ================= */

const LS_SETTINGS = 'gzhu2-settings'

/** 默认主题由深色改浅色的一次性迁移标记(与 index.html 首帧脚本共用同一键)。 */
const LS_THEME_LIGHT_MIGRATED = 'gzhu2-theme-default-light'

function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(LS_SETTINGS)
    const saved = raw ? (JSON.parse(raw) as Partial<Settings>) : null
    const merged: Settings = { ...DEFAULT_SETTINGS, ...(saved ?? {}) }

    // 老版本把 'dark' 当作默认值存进了本地存储，无法区分「用户主动选深色」。
    // 首次迁移时统一回到浅色，此后用户的选择照常保存。
    if (!localStorage.getItem(LS_THEME_LIGHT_MIGRATED)) {
      merged.theme = 'light'
      localStorage.setItem(LS_THEME_LIGHT_MIGRATED, '1')
      localStorage.setItem(LS_SETTINGS, JSON.stringify(merged))
    }

    // 「跟随系统」档已移除：旧存储里的 'system' 归一为浅色，避免类型外的脏值进状态。
    if (merged.theme !== 'light' && merged.theme !== 'dark') {
      merged.theme = 'light'
      localStorage.setItem(LS_SETTINGS, JSON.stringify(merged))
    }

    return merged
  } catch {
    /* ignore */
  }
  return { ...DEFAULT_SETTINGS }
}

export const state = reactive({
  settings: loadSettings(),
  threads: [] as ThreadMeta[],
  activeId: null as string | null,
  turns: [] as Turn[],
  loadingThread: false,
  sending: false,
  sidebarOpen: true,
  mobileNav: false,
  error: null as string | null,
  /* ---- 桌宠主动消息(outbox 待读;轮询拉取,确认后逐条移除) ---- */
  pendingCare: [] as CareMessage[],
  careStatus: null as CareStatus | null,
  /* ---- 昼夜/主题的响应式读面:true=夜间(深色),false=白天(浅色) ---- */
  isDark: false,
  /* ---- 开发者模式（默认开启：演示/评审直接可见记忆与调试试视图） ---- */
  devMode: true,
})

export function persistSettings() {
  localStorage.setItem(LS_SETTINGS, JSON.stringify(state.settings))
}

export function setTheme(theme: Settings['theme']) {
  state.settings.theme = theme
  persistSettings()
  applyTheme(theme)
}

/**
 * 把主题落到 DOM，并同步 `isDark`。
 *
 * 昼夜只有一套状态：夜间就是深色、白天就是浅色，界面上的太阳/云朵徽章
 * 直接读 `isDark` 派生，不再按系统时钟另算一套。
 */
function applyTheme(theme: Settings['theme']) {
  const dark = theme === 'dark'
  document.documentElement.classList.toggle('dark', dark)
  state.isDark = dark
}

export function initTheme() {
  applyTheme(state.settings.theme)
}

export function clearError() {
  state.error = null
}

/* ================= 开发者模式 ================= */

/**
 * 开关开发者模式。开启时右侧功能列表出现「记忆」、设置里出现「调试」分页。
 *
 * **默认开启**：记忆与调试面是演示/评审要直接看到的东西，不再藏在口令后面。
 * 状态只存在内存里、不落 localStorage，刷新回到默认开启。
 */
export function setDevMode(on: boolean) {
  state.devMode = on
}

export const store = readonly(state)
