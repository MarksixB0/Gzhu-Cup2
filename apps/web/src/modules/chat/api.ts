/**
 * 对话模块的接口面：会话增删改查 + 流式对话。
 *
 * 只放对话自己用的接口；关怀轮询、桌宠叙事等跨模块接口仍在壳层 api 里。
 */
import type { ThreadMeta, Turn } from '../../types'
import { errorDetail, request, resolveUrl } from '../../shell/api/http'

export function listThreads() {
  return request<ThreadMeta[]>('/api/threads')
}

export function getThread(id: string) {
  return request<{ thread_id: string; title: string; turns: Turn[] }>(
    `/api/threads/${encodeURIComponent(id)}`,
  )
}

export function renameThread(id: string, title: string) {
  return request<{ ok: boolean }>(`/api/threads/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })
}

export function deleteThread(id: string) {
  return request<{ ok: boolean }>(`/api/threads/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

/* ================= SSE 流式对话(逐阶段事件) ================= */

export interface StreamHandlers {
  /** 后端阶段心跳:thinking(图中)、prepare */
  onPhase?: (phase: string, threadId: string) => void
  /** 模型逐 token 增量(真流式);reply 需自行累积 */
  onToken?: (content: string) => void
  /** 完整结果(整段回复;白板视图不需要 analysis) */
  onResult: (payload: { thread_id: string; reply: string }) => void
  /** 出错(HTTP 错误或 SSE error 事件) */
  onError: (message: string) => void
}

/** 解析 fetch body 中的 SSE 文本流,按 event/data 回调。返回用于取消的 AbortController。 */
export function chatStream(
  message: string,
  threadId: string | null,
  opts: { model?: string; temperature?: number },
  handlers: StreamHandlers,
): AbortController {
  const ctrl = new AbortController()
  void (async () => {
    try {
      const res = await fetch(resolveUrl('/api/chat/stream'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message, thread_id: threadId || null, ...opts }),
        signal: ctrl.signal,
      })
      if (!res.ok || !res.body) {
        handlers.onError(await errorDetail(res))
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buf = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        // SSE 帧以空行分隔
        let idx: number
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx)
          buf = buf.slice(idx + 2)
          handleFrame(frame, handlers)
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') handlers.onError((e as Error).message)
    }
  })()
  return ctrl
}

function handleFrame(frame: string, handlers: StreamHandlers) {
  const lines = frame.split('\n')
  let event = 'message'
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (dataLines.length === 0) return
  let data: any
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    return
  }
  if (event === 'phase') {
    handlers.onPhase?.(data.phase, data.thread_id)
  } else if (event === 'token') {
    handlers.onToken?.(data.content ?? '')
  } else if (event === 'result') {
    handlers.onResult({
      thread_id: data.thread_id,
      reply: data.reply ?? '',
    })
  } else if (event === 'error') {
    handlers.onError(data.detail || '生成失败')
  }
  // event=done 忽略
}
