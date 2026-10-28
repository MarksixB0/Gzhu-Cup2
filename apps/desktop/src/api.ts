/**
 * 桌宠对后端的访问。
 *
 * **只调用既有 API**——不新增任何桌宠专用接口。这是 PET_POLICY.md 的硬约束：
 * 网页端与此桌宠端共用同一套后端能力，桌宠端不得出现旁路逻辑。
 */
import type { PetModeState } from './pet/mode'

/** 浏览器里直接跑（没有 Electron 注入）时的回落地址 */
export const DEFAULT_BASE_URL = 'http://127.0.0.1:8000'

/**
 * 后端地址。Electron 主进程托管后端进程，端口可能因冲突而不是 8000，
 * 因此必须优先读它注入的地址，而不是写死默认值。
 */
export function resolveBaseUrl(): string {
  return window.gzhuEnv?.backendUrl || DEFAULT_BASE_URL
}

/** 一条待读的主动关怀消息（对应 `care/runtime.py::mark_sent` 写入的结构）。 */
export interface PendingMessage {
  id: string
  user: string
  kind: string
  topic: string | null
  text: string
  created_ts: number
}

/** 后端每轮返回的分析摘要（只声明前端用得到的字段）。 */
export interface ChatAnalysis {
  /** 对话基调：playful / normal / gentle / serious（见 pet/expression.ts） */
  tone?: string
}

/**
 * 用户自述资料 + 首启引导状态（对应 `core/user_profile.py`）。
 *
 * `onboarding.needed` 是**后端判定**的（完成 / 跳过 / 已问满 3 次都为 false）——
 * 前端不自己算次数，否则两边各算一份必然漂移。
 */
export interface UserProfile {
  display_name: string
  likes: string
  dislikes: string
  onboarding: {
    seen: number
    completed: boolean
    dismissed: boolean
    needed: boolean
  }
}

export interface ChatStreamHandlers {
  onPhase?: (phase: string) => void
  onToken?: (text: string) => void
  /**
   * 整段回复。带上 thread_id（桌宠据此保持会话连续）与本轮 analysis。
   *
   * analysis 里的 `tone` 供桌宠选待机表情 —— 只是**透传**，客户端不做任何情绪判定：
   * 判定归后端，前端消费结果。
   */
  onResult?: (reply: string, threadId: string, analysis?: ChatAnalysis) => void
  onError?: (detail: string) => void
}

/** 逐块解析 SSE：事件之间以空行分隔。 */
async function readSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep = buffer.indexOf('\n\n')
    while (sep >= 0) {
      const chunk = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      let event = 'message'
      const dataLines: string[] = []
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (dataLines.length > 0) {
        try {
          onEvent(event, JSON.parse(dataLines.join('\n')) as Record<string, unknown>)
        } catch {
          // 忽略不完整/坏帧，不打断整轮对话
        }
      }
      sep = buffer.indexOf('\n\n')
    }
  }
}

export class BackendClient {
  constructor(public baseUrl: string = resolveBaseUrl()) {}

  private url(path: string): string {
    return `${this.baseUrl.replace(/\/+$/, '')}${path}`
  }

  /** 拉取待读主动消息（新→旧）。 */
  async pending(): Promise<PendingMessage[]> {
    const res = await fetch(this.url('/api/care/pending'))
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = (await res.json()) as { messages?: PendingMessage[] }
    return data.messages ?? []
  }

  /** 确认已读（展示后调用，避免重复弹出）。 */
  async dismiss(id: string): Promise<void> {
    await fetch(this.url(`/api/care/pending/${encodeURIComponent(id)}/dismiss`), {
      method: 'POST',
    })
  }

  /**
   * 触发一次主动关怀评估（桌宠空闲时调用，让桌宠主动开口）。
   * 后端决定是否该说（受免扰时段/冷却/每日上限约束），沉默时返回 null。
   */
  async evaluateCare(): Promise<PendingMessage | null> {
    const res = await fetch(this.url('/api/care/evaluate'), { method: 'POST' })
    if (!res.ok) return null
    const data = (await res.json()) as { messages?: PendingMessage[] }
    return data.messages?.[0] ?? null
  }

  /** 开场白（角色卡 first_mes）；未搭载角色卡或后端不可用时返回 null。 */
  async greeting(): Promise<string | null> {
    const res = await fetch(this.url('/api/pet/greeting'))
    if (!res.ok) return null
    const data = (await res.json()) as { text?: string | null }
    return data.text ?? null
  }

  /** 用户自述资料 + 首启引导状态；后端不可用时返回 null（引导静默不发生）。 */
  async userProfile(): Promise<UserProfile | null> {
    const res = await fetch(this.url('/api/user/profile'))
    if (!res.ok) return null
    return (await res.json()) as UserProfile
  }

  /**
   * 记录首启引导进展（seen / complete / skip）。
   *
   * 必须落盘：只存前端内存的话，「跳过后不再问」「最多问 3 次」都会在重启后失效。
   */
  async onboarding(action: 'seen' | 'complete' | 'skip'): Promise<void> {
    await fetch(this.url('/api/user/profile/onboarding'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    })
  }

  /**
   * 写一项用户资料。
   *
   * 走**统一设置面** `PATCH /api/settings`（键名 `profile.<field>`），
   * 不新开桌宠专用写接口 —— 与设置页共用同一个写入口（PET_POLICY.md 的约束）。
   */
  async setProfileField(
    field: 'display_name' | 'likes' | 'dislikes',
    value: string,
  ): Promise<boolean> {
    const res = await fetch(this.url('/api/settings'), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [`profile.${field}`]: value }),
    })
    return res.ok
  }

  /**
   * 当前打扰等级（见 PLAN_NEXT.md §2）。
   *
   * 后端是唯一真相：这里只取结果。前端**不自己算**夜间窗口与到期判定 ——
   * 那两件事一旦两边各算一份，就必然漂移。
   */
  async petMode(): Promise<PetModeState | null> {
    const res = await fetch(this.url('/api/pet/mode'))
    if (!res.ok) return null
    return (await res.json()) as PetModeState
  }

  /**
   * 改打扰等级（部分更新：传哪些字段就改哪些）。
   *
   * `manualLevel: ''` 表示**清除**临时覆盖（回到 自动 + 默认）；
   * `manualMinutes` 给了就是「到点自动恢复」，不给则长期有效直到被改。
   */
  async setPetMode(patch: {
    defaultLevel?: string
    nightSleep?: boolean
    manualLevel?: string
    manualMinutes?: number
  }): Promise<PetModeState | null> {
    const body: Record<string, unknown> = {}
    if (patch.defaultLevel !== undefined) body.default_level = patch.defaultLevel
    if (patch.nightSleep !== undefined) body.night_sleep = patch.nightSleep
    if (patch.manualLevel !== undefined) body.manual_level = patch.manualLevel
    if (patch.manualMinutes !== undefined) body.manual_minutes = patch.manualMinutes
    const res = await fetch(this.url('/api/pet/mode'), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) return null
    return (await res.json()) as PetModeState
  }

  /**
   * 一轮流式对话。
   *
   * `threadId` 必须沿用上一轮的返回值：后端按 thread 累积历史，若每轮都传 null，
   * 它就会每次都新建会话 —— 桌宠会「聊着聊着失忆」，回复退化成通用话术。
   */
  async chat(
    message: string,
    handlers: ChatStreamHandlers,
    threadId?: string | null,
  ): Promise<void> {
    const res = await fetch(this.url('/api/chat/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, thread_id: threadId ?? null }),
    })
    if (!res.ok || !res.body) {
      handlers.onError?.(`HTTP ${res.status}`)
      return
    }
    await readSse(res.body, (event, data) => {
      switch (event) {
        case 'phase':
          handlers.onPhase?.(String(data.phase ?? ''))
          break
        case 'token':
          handlers.onToken?.(String(data.content ?? ''))
          break
        case 'result':
          handlers.onResult?.(
            String(data.reply ?? ''),
            String(data.thread_id ?? ''),
            (data.analysis as ChatAnalysis | undefined) ?? undefined,
          )
          break
        case 'error':
          handlers.onError?.(String(data.detail ?? '未知错误'))
          break
      }
    })
  }
}
