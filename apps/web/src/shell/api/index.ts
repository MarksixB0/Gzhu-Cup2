import type {
  AppSettings,
  CareMessage,
  CareStatus,
  ConnectionDraft,
  ConnectionTestResult,
  ConnectionView,
  MemoryDebug,
  MemoryInsight,
  MemoryPrivacy,
  MemoryWipeResult,
  PetDaily,
  PetStatus,
  RetrievalTraceRecord,
} from '../../types'
import { request } from './http'

export const api = {
  /** 清空全部会话历史（**不可逆**；隐私页的全局操作，后端要求显式 confirm） */
  deleteAllThreads() {
    return request<{ ok: boolean; removed: number }>('/api/threads?confirm=1', {
      method: 'DELETE',
    })
  },

  /* ---- 设置面板 → API 连接(与后端 runtime_connection 对应) ---- */
  getConnection() {
    return request<ConnectionView>('/api/connection')
  },
  saveConnection(draft: ConnectionDraft) {
    return request<ConnectionView>('/api/connection', {
      method: 'PUT',
      body: JSON.stringify(draft),
    })
  },
  /** 用表单中的 url/key(可留空取已存)探测 {base_url}/models */
  testConnection(payload: { base_url?: string; api_key?: string }) {
    return request<ConnectionTestResult>('/api/connection/test', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  /* ================= 桌宠主动回复(通用能力,与桌宠前端共用) ================= */
  /** 关怀运行状态:开关 / 今日已发 / 最近主动时间 / 待读条数 */
  getCareStatus() {
    return request<CareStatus>('/api/care/status')
  },
  /** 拉取待读主动消息(新→旧) */
  listCarePending() {
    return request<{ messages: CareMessage[] }>('/api/care/pending')
  },
  /** 确认已读并移除一条主动消息 */
  dismissCare(id: string) {
    return request<{ ok: boolean; id: string }>(
      `/api/care/pending/${encodeURIComponent(id)}/dismiss`,
      { method: 'POST' },
    )
  },
  /** 手动触发一次关怀评估(开发/联调用;调度线程也会自动跑)。
   *  force=true(用户点「让桌宠现在说点什么」)绕过时机闸;勿扰等级下返回 blocked。 */
  triggerCareEvaluate(force = false) {
    return request<{ messages: CareMessage[]; blocked?: string }>('/api/care/evaluate', {
      method: 'POST',
      body: JSON.stringify({ force }),
    })
  },
  /** 每日生活叙事开关 */
  getPetStatus() {
    return request<PetStatus>('/api/pet/settings')
  },
  /** 今日叙事(未生成为 text: null) */
  getPetDaily() {
    return request<PetDaily>('/api/pet/daily')
  },
  /** 显式刷新今日叙事(受每日刷新上限;到顶 429 / 已停用 409,细节在错误消息里) */
  regeneratePetDaily() {
    return request<PetDaily>('/api/pet/daily/regenerate', { method: 'POST' })
  },
  /** 用户直接编辑今日叙事文本(source → user_edited;不调 LLM、不占刷新额度) */
  updatePetDaily(text: string) {
    return request<PetDaily>('/api/pet/daily', {
      method: 'PATCH',
      body: JSON.stringify({ text }),
    })
  },
  /** 检索留痕(调试视图;读侧组装决策的按次审计) */
  getRetrievalTrace(limit = 50) {
    return request<{ records: RetrievalTraceRecord[] }>(`/api/memory/trace?limit=${limit}`)
  },
  /** 角色卡开场白(first_mes;未搭载角色卡时为 null) */
  getPetGreeting() {
    return request<{ text: string | null; source: string | null }>('/api/pet/greeting')
  },
  /** 长期记忆调试视图(只读):排障 recall/probe 为什么不触发 */
  getMemoryDebug() {
    return request<MemoryDebug>('/api/memory/debug')
  },
  /** 长期记忆完整洞察(只读):时序 + 通道真值 + 配额入选 + 上下文预览 */
  getMemoryInsight(user?: string) {
    const q = user ? `?user=${encodeURIComponent(user)}` : ''
    return request<MemoryInsight>(`/api/memory/insight${q}`)
  },

  /* ==================== 统一设置面(见 PLAN_NEXT.md §7) ==================== */
  /** 全量设置(按域分组;各域仍是唯一真相,这里只是聚合视图) */
  getSettings() {
    return request<AppSettings>('/api/settings')
  },
  /**
   * 增量更新设置:只改传了的键。
   * 未知键后端会**明确 400**(不静默忽略),调用方不必"猜有没有生效"。
   */
  patchSettings(changes: Record<string, unknown>) {
    return request<AppSettings>('/api/settings', {
      method: 'PATCH',
      body: JSON.stringify(changes),
    })
  },

  /* ==================== 记忆隐私控制面(见 PLAN_NEXT.md §6) ==================== */
  /** 记录状态 + 数据落盘位置(不受 MEMORY_INSIGHT_ENABLED 约束:属知情权底线) */
  getMemoryPrivacy() {
    return request<MemoryPrivacy>('/api/memory/privacy')
  },
  /** 导出全部记忆(返回原文;受 insight 开关约束) */
  exportMemory() {
    return request<Record<string, unknown>>('/api/memory/export')
  },
  /** 清空全部记忆(**不可逆**;后端要求显式 confirm,并回执实际删了什么) */
  wipeMemory() {
    return request<MemoryWipeResult>('/api/memory/all?confirm=1', { method: 'DELETE' })
  },
}
