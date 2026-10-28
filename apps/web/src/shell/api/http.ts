/**
 * HTTP 基础设施：后端地址解析、JSON 请求、错误信息提取。
 *
 * 所有模块的 `api.ts` 统一从这里取 `request`。
 */

/**
 * 后端地址前缀。
 *
 * - **Electron 主窗口／桌宠窗口**：主进程托管后端进程，端口可能因冲突而不是 8000，
 *   地址由 preload 注入到 `window.gzhuEnv.backendUrl`；
 * - **浏览器直连 dev server**：注入不存在，返回空串 → 用相对路径走 Vite 的 `/api` 代理。
 */
export function apiBase(): string {
  const injected = (window as unknown as { gzhuEnv?: { backendUrl?: string } }).gzhuEnv?.backendUrl
  return injected ?? ''
}

/** 只给 `/api` 开头的路径拼后端前缀，其余（外链、data URI）原样返回。 */
export function resolveUrl(url: string): string {
  return url.startsWith('/api') ? `${apiBase()}${url}` : url
}

/** 从失败响应里取后端 `detail`，取不到就退回状态码文案。 */
export async function errorDetail(res: Response): Promise<string> {
  let detail = `请求失败(${res.status})`
  try {
    const body = await res.json()
    if (body?.detail) {
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    }
  } catch {
    /* 非 JSON 响应 */
  }
  return detail
}

/** JSON 请求。非 2xx 抛 Error（message 为后端 detail），调用方直接展示即可。 */
export async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(resolveUrl(url), {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) throw new Error(await errorDetail(res))
  return res.json() as Promise<T>
}
