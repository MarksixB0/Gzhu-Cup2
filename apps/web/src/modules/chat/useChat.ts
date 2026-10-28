/**
 * 对话状态机：会话增删选、流式发送、桌宠主动消息轮询。
 *
 * 滚动、聚焦、抽屉收起等界面动作留在视图里。
 */
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../../shell/api'
import { clearError, state, store } from '../../shell/store'
import { chatStream, getThread, listThreads } from './api'

/** 主动消息轮询间隔（毫秒） */
const CARE_POLL_MS = 3000

export function useChat() {
  /* ---------- 桌宠主动消息 ----------
     两条通道,分工明确(见 PET_POLICY.md):
       - **会话历史**:带 kind="proactive" 的轮次就是主动消息的**内容** ——
         和普通对话一样持久化,刷新/切会话都不丢,也**不需要用户手动确认**;
       - **outbox**(/api/care/pending):只负责**未读** ——
         消息一旦在对话流里出现过就标记已读,角标随之清零。 */
  let careTimer: number | null = null
  /** 已见过的主动消息 id,用于判断"这一轮有没有新消息" */
  const seenCareIds = new Set<string>()

  /** 当前流式控制 */
  let abortCtrl: AbortController | null = null
  const pendingUser = ref<string | null>(null)
  const streamText = ref<string>('')
  const tokensStarted = ref(false)

  async function refreshThreads() {
    try {
      state.threads = await listThreads()
    } catch (e) {
      state.error = (e as Error).message
    }
  }

  /** 重新拉取当前会话(不闪空列表,故不复用 selectThread) */
  async function reloadActiveThread() {
    if (!store.activeId) return
    try {
      const detail = await getThread(store.activeId)
      state.turns = detail.turns
    } catch {
      /* 静默 */
    }
  }

  async function refreshCare() {
    try {
      const [pending, status] = await Promise.all([api.listCarePending(), api.getCareStatus()])
      const fresh = pending.messages.filter((m) => !seenCareIds.has(m.id))
      state.pendingCare = pending.messages
      state.careStatus = status
      if (fresh.length) {
        fresh.forEach((m) => seenCareIds.add(m.id))
        // 新消息已落进会话历史 → 刷新对话区,让它自然出现在消息流里
        await refreshThreads()
        await reloadActiveThread()
      }
      await markVisibleCareRead()
    } catch {
      /* 后台线程未启动或接口异常:静默忽略,绝不影响正常对话 */
    }
  }

  /** 把"已经在对话流里出现过"的主动消息标记为已读(未读角标随之清零) */
  async function markVisibleCareRead() {
    const visible = new Set(
      state.turns.map((t) => t.care_msg_id).filter((id): id is string => !!id),
    )
    const toClear = state.pendingCare.filter((m) => visible.has(m.id))
    if (!toClear.length) return
    const ids = new Set(toClear.map((m) => m.id))
    state.pendingCare = state.pendingCare.filter((m) => !ids.has(m.id))
    for (const id of ids) {
      try {
        await api.dismissCare(id)
      } catch {
        /* 下次轮询再试 */
      }
    }
  }

  /** 点未读角标:跳到"桌宠最近说话"的那条会话 */
  async function openLatestCare() {
    await refreshThreads()
    const latest = store.threads[0]
    if (!latest) return
    if (store.activeId === latest.id) await reloadActiveThread()
    else await selectThread(latest.id)
  }

  function stopCurrent() {
    abortCtrl?.abort()
    abortCtrl = null
    state.sending = false
    pendingUser.value = null
    streamText.value = ''
    tokensStarted.value = false
  }

  async function selectThread(id: string) {
    if (store.activeId === id) return
    if (state.sending) stopCurrent()
    state.activeId = id
    clearError()
    state.loadingThread = true
    state.turns = []
    try {
      const detail = await getThread(id)
      state.turns = detail.turns
    } catch (e) {
      state.error = (e as Error).message
    } finally {
      state.loadingThread = false
    }
  }

  function newChat() {
    if (state.sending) stopCurrent()
    state.activeId = null
    state.turns = []
    clearError()
  }

  function nowLabel(): string {
    const d = new Date()
    const p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }

  /* ---------- 发送(SSE 流式 + 前端打字机) ---------- */
  async function send(text: string) {
    if (state.sending) return
    clearError()
    pendingUser.value = text
    state.sending = true
    const wasNew = store.activeId === null
    const model = store.settings.model || undefined // 留空 = 跟随后端 config 默认,不传 model 覆盖
    const temperature = store.settings.temperature

    abortCtrl = chatStream(
      text,
      store.activeId,
      { model, temperature },
      {
        onToken: (content) => {
          if (!tokensStarted.value) tokensStarted.value = true
          streamText.value += content
        },
        onResult: ({ thread_id, reply }) => {
          const ts = nowLabel()
          state.activeId = thread_id
          state.turns.push({ ts, user: text, ai: reply })
          state.sending = false
          pendingUser.value = null
          streamText.value = ''
          tokensStarted.value = false
          if (wasNew) void refreshThreads()
        },
        onError: (msg) => {
          state.error = msg
          state.sending = false
          pendingUser.value = null
          streamText.value = ''
          tokensStarted.value = false
        },
      },
    )
  }

  onMounted(() => {
    void refreshThreads()
    void refreshCare()
    careTimer = window.setInterval(() => void refreshCare(), CARE_POLL_MS)
  })

  onUnmounted(() => {
    if (careTimer !== null) window.clearInterval(careTimer)
    careTimer = null
  })

  return {
    pendingUser,
    streamText,
    tokensStarted,
    refreshThreads,
    reloadActiveThread,
    openLatestCare,
    selectThread,
    newChat,
    send,
    stopCurrent,
  }
}
