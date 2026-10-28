/**
 * 设置的统一读写（见 `PLAN_NEXT.md` §7）。
 *
 * 两个动作 —— `load()` 拉全量、`save(changes)` 增量提交；
 * 而「有哪些键、什么类型、怎么说明」由 `settings/spec.ts` 声明。
 *
 * 失败语义：后端对未知键返回 400（**不静默忽略**），错误文案已写明
 * 「哪个键不认识、可用的有哪些」，所以这里不再二次包装。
 */
import { ref } from 'vue'

import { api } from '../../shell/api'
import { readByPath } from './spec'

export interface Notice {
  kind: 'ok' | 'err'
  text: string
}

export function useSettings() {
  const snapshot = ref<Record<string, unknown> | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const notice = ref<Notice | null>(null)

  /** 拉全量快照。失败时保留上一次的值，只把错误写进 notice。 */
  async function load(): Promise<void> {
    loading.value = true
    try {
      snapshot.value = (await api.getSettings()) as unknown as Record<string, unknown>
    } catch (e) {
      notice.value = { kind: 'err', text: (e as Error).message }
    } finally {
      loading.value = false
    }
  }

  /**
   * 增量提交：只改传了的键。成功返回 true，并把后端返回的最新全量写回快照。
   *
   * 不做本地乐观更新：快照只以后端返回值为准，失败时无需回滚。
   * 调用方只需 `await save({ 'care.enabled': false })`。
   */
  async function save(changes: Record<string, unknown>): Promise<boolean> {
    saving.value = true
    try {
      snapshot.value = (await api.patchSettings(changes)) as unknown as Record<string, unknown>
      notice.value = { kind: 'ok', text: '已保存' }
      return true
    } catch (e) {
      notice.value = { kind: 'err', text: (e as Error).message }
      return false
    } finally {
      saving.value = false
    }
  }

  /** 从快照里取某个点号键的值（如 `mode.default_level`）。 */
  function valueOf(key: string): unknown {
    return readByPath(snapshot.value, key)
  }

  return { snapshot, loading, saving, notice, load, save, valueOf }
}
