<script setup lang="ts">
/**
 * 隐私分区（并入「用户」页；见 `PLAN_NEXT.md` §6）。
 *
 * 四件事：导出、清空全部记忆、清空对话历史（全部在本页完成）。
 * 「记录记忆」开关已移除 —— 记忆默认恒开，不再提供暂停入口。
 *
 * 两个刻意的设计：
 *  1. **把「数据存储位置」直接摆出来**（目录 + 文件清单）。「知道数据在哪」本身就是
 *     隐私能力，不该藏在开发者工具或文档里；
 *  2. **清空要弹窗二次确认**（取消 / 确定），并且回执**实际删了什么**（命名空间 → 条数）。
 *     不可逆操作给一个含糊的「成功」是不负责任的。
 */
import { onMounted, ref } from 'vue'

import { api } from '../../../shell/api'
import { state } from '../../../shell/store'
import type { MemoryPrivacy } from '../../../types'
import Icon from '../../../shell/components/Icon.vue'

const privacy = ref<MemoryPrivacy | null>(null)
const busy = ref(false)
const notice = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)
/**
 * 二次确认弹窗：'wipe' = 清空记忆，'threads' = 清空对话历史。
 * null = 无弹窗。两个不可逆操作共用一个弹窗壳，文案按类型切换。
 */
const confirming = ref<'wipe' | 'threads' | null>(null)

const CONFIRM_TEXT = {
  wipe: '这会清除桌宠所有对话包括记忆，确定进行吗',
  threads: '这只会清除对话记录，不会影响桌宠记忆，确认进行吗',
} as const

async function load(): Promise<void> {
  try {
    privacy.value = await api.getMemoryPrivacy()
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  }
}

/** 导出为 JSON 文件（内容含记忆原文，由用户自己保存）。 */
async function downloadExport(): Promise<void> {
  busy.value = true
  try {
    const data = await api.exportMemory()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `gzhu-memory-${new Date().toISOString().slice(0, 10)}.json`
    anchor.click()
    URL.revokeObjectURL(url)
    notice.value = { kind: 'ok', text: '已导出为 JSON 文件' }
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    busy.value = false
  }
}

/** 清空全部记忆：把后端回执的「删了什么」原样呈现。 */
async function wipe(): Promise<void> {
  busy.value = true
  try {
    const res = await api.wipeMemory()
    const parts = Object.entries(res.removed).map(([kind, count]) => `${kind} ${count} 条`)
    notice.value = {
      kind: 'ok',
      text: parts.length ? `已删除：${parts.join('、')}` : '没有可删除的记忆',
    }
    confirming.value = null
    await load()
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    busy.value = false
  }
}

/** 清空全部对话历史：后端回执条数，并同步清掉内存里的会话状态让界面立刻反映。 */
async function clearThreads(): Promise<void> {
  busy.value = true
  try {
    const res = await api.deleteAllThreads()
    state.threads = []
    state.activeId = null
    state.turns = []
    notice.value = {
      kind: 'ok',
      text: res.removed > 0 ? `已删除：对话历史 ${res.removed} 条` : '没有可删除的对话',
    }
    confirming.value = null
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    busy.value = false
  }
}

/** 弹窗里点「确定」：按类型分发到对应操作。 */
function confirmYes(): void {
  if (confirming.value === 'wipe') void wipe()
  else if (confirming.value === 'threads') void clearThreads()
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

onMounted(load)
</script>

<template>
  <section>
    <div class="mb-1 flex items-center justify-between">
      <h3 class="text-[13px] font-medium" style="color: var(--c-text-2)">隐私</h3>
      <button
        class="flex items-center gap-1 text-[11px] ui-hover"
        style="color: var(--c-text-3)"
        :disabled="busy"
        @click="load"
      >
        <Icon name="refresh" :size="12" />
        刷新
      </button>
    </div>

    <div
      v-if="notice"
      class="mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-[12px] leading-relaxed"
      :style="{
        borderColor: notice.kind === 'ok' ? 'var(--c-accent)' : '#e5484d',
        color: notice.kind === 'ok' ? 'var(--c-accent)' : '#e5484d',
        background: 'var(--c-input)',
      }"
    >
      <Icon :name="notice.kind === 'ok' ? 'check' : 'alert'" :size="14" class="mt-0.5 shrink-0" />
      <span>{{ notice.text }}</span>
    </div>

    <!-- 数据存储位置：只读展示 -->
    <div class="rounded-xl border p-3.5" style="border-color: var(--c-border)">
      <div class="mb-2 text-[12px] font-medium" style="color: var(--c-text-2)">
        数据存储位置
      </div>
      <div class="mb-2 font-mono text-[11px] break-all" style="color: var(--c-text-3)">
        {{ privacy?.memory_dir || '—' }}
      </div>
      <div v-if="privacy?.files?.length" class="space-y-1">
        <div
          v-for="f in privacy.files"
          :key="f.name"
          class="flex items-center justify-between text-[11px]"
          style="color: var(--c-text-2)"
        >
          <span class="font-mono truncate">{{ f.name }}</span>
          <span style="color: var(--c-text-3)">{{ fmtBytes(f.bytes) }}</span>
        </div>
      </div>
      <div v-else class="text-[11px]" style="color: var(--c-text-3)">（还没有任何数据文件）</div>
    </div>

    <!-- 导出 / 清空。清空对话历史用黄色：提示谨慎，但语义轻于「清空记忆」的红。 -->
    <div class="mt-3 flex flex-wrap items-center gap-2">
      <button
        class="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
        style="border-color: var(--c-border); color: var(--c-text-2)"
        :disabled="busy"
        @click="downloadExport"
      >
        <Icon name="copy" :size="14" />
        导出全部记忆
      </button>

      <button
        class="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
        style="border-color: #e5484d; color: #e5484d"
        :disabled="busy"
        @click="confirming = 'wipe'"
      >
        <Icon name="alert" :size="14" />
        清空全部记忆
      </button>

      <button
        class="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
        style="border-color: #a16207; color: #a16207"
        :disabled="busy"
        @click="confirming = 'threads'"
      >
        <Icon name="alert" :size="14" />
        清空对话历史
      </button>
    </div>

    <!-- 二次确认弹窗：取消 / 确定 -->
    <div v-if="confirming" class="confirm-mask" @click.self="confirming = null">
      <div class="confirm-card">
        <p class="confirm-text">{{ CONFIRM_TEXT[confirming] }}</p>
        <div class="confirm-btns">
          <button
            class="rounded-lg border px-4 py-1.5 text-[13px]"
            style="border-color: var(--c-border); color: var(--c-text-2)"
            @click="confirming = null"
          >
            取消
          </button>
          <button
            class="rounded-lg px-4 py-1.5 text-[13px] font-medium text-white"
            style="background: #e5484d"
            :disabled="busy"
            @click="confirmYes"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.confirm-mask {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.32);
}

.confirm-card {
  width: 320px;
  max-width: calc(100vw - 48px);
  padding: 20px 22px 16px;
  border-radius: 16px;
  border: 1.5px solid var(--c-border);
  background: var(--c-card, #fff);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18);
}

.confirm-text {
  margin: 0 0 16px;
  font-size: 13.5px;
  line-height: 1.65;
  color: var(--c-text);
}

.confirm-btns {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>
