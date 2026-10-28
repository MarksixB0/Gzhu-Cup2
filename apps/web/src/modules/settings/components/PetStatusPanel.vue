<script setup lang="ts">
/**
 * 桌宠运行状态（只读）与联调入口。
 *
 * 展示当日已发主动消息数、未读数与最近主动时间；「立即触发一次评估」为联调用途，
 * 绕过调度线程直接执行一轮关怀评估。运行状态属观察面而非设置项，故随调试视图呈现，
 * 不进入用户设置面板。
 */
import { onMounted, ref } from 'vue'

import { api } from '../../../shell/api'
import { state } from '../../../shell/store'
import Icon from '../../../shell/components/Icon.vue'

const busy = ref(false)
const loading = ref(false)
const notice = ref<{ kind: 'ok' | 'err'; text: string } | null>(null)
const dailyText = ref<string | null>(null)
const dailySource = ref<string | null>(null)

/** Unix 秒 → 'MM-DD HH:mm'；空值显示占位符。 */
function fmtTs(ts: number | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 拉取关怀状态与每日生活叙事；结果写入全局 state 供桌面端轮询复用。 */
async function load(): Promise<void> {
  loading.value = true
  try {
    const [care, daily] = await Promise.all([api.getCareStatus(), api.getPetDaily()])
    state.careStatus = care
    dailyText.value = daily.text
    dailySource.value = daily.source
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    loading.value = false
  }
}

/** 联调：立刻跑一次关怀评估，不必等调度线程。 */
async function evaluateNow(): Promise<void> {
  busy.value = true
  notice.value = null
  try {
    const r = await api.triggerCareEvaluate()
    await load()
    notice.value = r.messages.length
      ? { kind: 'ok', text: `生成了 ${r.messages.length} 条主动消息，已进入对话流` }
      : { kind: 'ok', text: '本次评估未触发主动消息（未满足触发条件，或文案被安全过滤丢弃）' }
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mt-3 rounded-xl border p-3.5" style="border-color: var(--c-border)">
    <div class="mb-2 flex items-center justify-between">
      <span class="text-[12px] font-medium" style="color: var(--c-text-2)">桌宠运行状态</span>
      <button
        class="flex items-center gap-1 text-[11px] ui-hover"
        style="color: var(--c-text-3)"
        :disabled="loading"
        @click="load"
      >
        <Icon name="refresh" :size="12" :class="{ 'animate-spin': loading }" />
        刷新
      </button>
    </div>

    <div class="grid grid-cols-3 gap-2 text-center">
      <div class="rounded-lg px-2 py-2" style="background: var(--c-input)">
        <div class="font-mono text-[15px]" style="color: var(--c-accent)">
          {{ state.careStatus?.date_count ?? '—' }}
        </div>
        <div class="text-[11px]" style="color: var(--c-text-3)">今日已发</div>
      </div>
      <div class="rounded-lg px-2 py-2" style="background: var(--c-input)">
        <div class="font-mono text-[15px]" style="color: var(--c-accent)">
          {{ state.careStatus?.pending_count ?? 0 }}
        </div>
        <div class="text-[11px]" style="color: var(--c-text-3)">未读</div>
      </div>
      <div class="rounded-lg px-2 py-2" style="background: var(--c-input)">
        <div class="font-mono text-[12px] leading-[22px]" style="color: var(--c-text-2)">
          {{ fmtTs(state.careStatus?.last_proactive_ts) }}
        </div>
        <div class="text-[11px]" style="color: var(--c-text-3)">最近主动</div>
      </div>
    </div>

    <div class="mt-3 text-[11px] leading-relaxed" style="color: var(--c-text-3)">
      <div v-if="dailyText">
        <span style="color: var(--c-text-2)">今日生活</span>（{{ dailySource }}）：{{ dailyText }}
      </div>
      <div v-else>今日生活：尚未生成{{ dailySource === 'unavailable' ? '（上次生成失败）' : '' }}</div>
    </div>

    <div
      v-if="notice"
      class="mt-2 text-[11px]"
      :style="{ color: notice.kind === 'ok' ? 'var(--c-accent)' : '#e5484d' }"
    >
      {{ notice.text }}
    </div>

    <div class="mt-3 flex items-center gap-2">
      <button
        class="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
        style="border-color: var(--c-border); color: var(--c-text-2)"
        :disabled="busy"
        @click="evaluateNow"
      >
        <Icon name="refresh" :size="14" :class="{ 'animate-spin': busy }" />
        立即触发一次评估
      </button>
      <span class="text-[11px]" style="color: var(--c-text-3)">联调用：不必等调度线程</span>
    </div>
  </div>
</template>
