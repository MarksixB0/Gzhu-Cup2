<script setup lang="ts">
/**
 * 桌宠今日记忆面板:显示 / 刷新 / 编辑今日叙事 + 「让它现在说点什么」。
 *
 * 定位(为什么放在设置里):桌宠的"生活"不该是黑盒 —— 用户能看见它今天经历了什么、
 * 能改它(共创素材),也能在想要它开口时立刻让它开口。
 *
 * 手动触发的分寸:用户显式点击本身就是知情同意,因此后端绕过**时机闸**
 * (免扰时段/冷却/每日上限);但总开关、关怀开关与**打扰等级**仍被尊重 ——
 * 勿扰等级下按钮置灰并说明,而不是静默突破。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../../../shell/api'
import type { PetDaily } from '../../../types'
import Icon from '../../../shell/components/Icon.vue'

/** 与后端 PET_DAILY_MAX_LEN 同一口径(截断在后端做,这里只提示) */
const MAX_LEN = 220

const loading = ref(false)
const daily = ref<PetDaily | null>(null)
const dailyEnabled = ref(true)
const editing = ref(false)
const draft = ref('')
const regenerating = ref(false)
const saving = ref(false)
const sending = ref(false)
const notice = ref<{ kind: 'ok' | 'err' | 'info'; text: string } | null>(null)

const sourceLabel = computed(() => {
  const s = daily.value?.source
  if (s === 'user_edited') return '我已编辑'
  if (s === 'llm') return '桌宠的生活'
  if (s === 'unavailable') return '生成失败'
  return ''
})

const overLimit = computed(() => draft.value.length > MAX_LEN)

async function load(): Promise<void> {
  loading.value = true
  try {
    const [status, d] = await Promise.all([api.getPetStatus(), api.getPetDaily()])
    dailyEnabled.value = status.daily_enabled
    daily.value = d
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    loading.value = false
  }
}

async function regenerate(): Promise<void> {
  regenerating.value = true
  notice.value = null
  try {
    daily.value = await api.regeneratePetDaily()
    if (daily.value?.source === 'unavailable') {
      notice.value = { kind: 'info', text: '这次没有生成出来(模型不可用或输出为空),稍后再试一次。' }
    } else {
      notice.value = { kind: 'ok', text: '已重新生成今天的生活。' }
    }
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    regenerating.value = false
  }
}

function startEdit(): void {
  draft.value = daily.value?.text ?? ''
  editing.value = true
  notice.value = null
}

async function saveEdit(): Promise<void> {
  if (overLimit.value) return
  saving.value = true
  notice.value = null
  try {
    daily.value = await api.updatePetDaily(draft.value)
    editing.value = false
    notice.value = { kind: 'ok', text: '已保存,桌宠分享日常时会用它。' }
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    saving.value = false
  }
}

/** 手动触发:force 绕过时机闸;勿扰等级由后端拒并以 blocked 标记返回 */
async function poke(): Promise<void> {
  sending.value = true
  notice.value = null
  try {
    const r = await api.triggerCareEvaluate(true)
    if (r.blocked === 'quiet_mode') {
      notice.value = { kind: 'info', text: '当前处于勿扰等级,桌宠不会主动说话。可在下方「打扰等级」里调整。' }
    } else if (r.blocked === 'force_too_soon') {
      notice.value = { kind: 'info', text: '刚刚已经说过一句啦,等一分钟再点。' }
    } else if (r.messages.length) {
      notice.value = { kind: 'ok', text: '桌宠去说话了,去聊天窗口看看。' }
    } else {
      notice.value = {
        kind: 'info',
        text: '这次没生成出内容(模型这次空回复,或文案被安全过滤)。再点一次通常就好;'
          + '连续失败再去「API 连接」看看模型是否可用。',
      }
    }
  } catch (e) {
    notice.value = { kind: 'err', text: (e as Error).message }
  } finally {
    sending.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="mb-4 rounded-xl border p-3.5" style="border-color: var(--c-border)">
    <div class="mb-2 flex items-center justify-between">
      <span class="font-hand text-[16px] leading-none" style="color: var(--c-text-2)">桌宠今日记忆</span>
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

    <template v-if="dailyEnabled">
      <!-- 展示态 -->
      <div
        v-if="!editing"
        class="rounded-lg px-2.5 py-2 text-[12px] leading-relaxed"
        style="background: var(--c-input); color: var(--c-text)"
      >
        <span v-if="daily?.text">{{ daily.text }}</span>
        <span v-else style="color: var(--c-text-3)">
          今天的生活还没生成{{ daily?.source === 'unavailable' ? '(上次生成失败,点「刷新」再试一次)' : ',稍等调度线程或点「刷新」' }}。
        </span>
      </div>
      <!-- 编辑态 -->
      <div v-else>
        <textarea
          v-model="draft"
          rows="3"
          class="w-full resize-none rounded-lg border px-2.5 py-2 text-[12px] leading-relaxed outline-none transition focus:border-[var(--c-accent)]"
          style="border-color: var(--c-border); background: var(--c-input); color: var(--c-text)"
          :maxlength="MAX_LEN + 40"
        />
        <div class="mt-0.5 text-right text-[10px]" :style="{ color: overLimit ? '#e5484d' : 'var(--c-text-3)' }">
          {{ draft.length }} / {{ MAX_LEN }}
        </div>
      </div>

      <div class="mt-1.5 flex flex-wrap items-center gap-2">
        <span
          v-if="sourceLabel"
          class="rounded px-1.5 py-0.5 text-[10px]"
          :style="{
            background: 'var(--c-accent-soft)',
            color: daily?.source === 'unavailable' ? '#e5484d' : 'var(--c-accent)',
          }"
        >{{ sourceLabel }}</span>
        <span v-if="daily?.regen_count" class="text-[10px]" style="color: var(--c-text-3)">
          今日已刷新 {{ daily.regen_count }} 次
        </span>
      </div>

      <div class="mt-2.5 flex flex-wrap items-center gap-2">
        <button
          v-if="!editing"
          class="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
          style="border-color: var(--c-border); color: var(--c-text-2)"
          :disabled="regenerating"
          @click="regenerate"
        >
          <Icon name="refresh" :size="14" :class="{ 'animate-spin': regenerating }" />
          刷新今日生活
        </button>
        <template v-else>
          <button
            class="rounded-lg px-3 py-1.5 text-[12px] font-medium text-white transition hover:opacity-90"
            style="background: var(--c-accent)"
            :disabled="saving || overLimit"
            @click="saveEdit"
          >
            保存
          </button>
          <button
            class="rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
            style="border-color: var(--c-border); color: var(--c-text-2)"
            @click="editing = false"
          >
            取消
          </button>
        </template>
        <button
          v-if="!editing"
          class="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
          style="border-color: var(--c-border); color: var(--c-text-2)"
          :disabled="!daily?.text || editing"
          title="编辑今日的生活"
          @click="startEdit"
        >
          <Icon name="settings" :size="14" />
          编辑
        </button>
        <button
          class="ml-auto flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] transition ui-hover"
          style="border-color: var(--c-accent); color: var(--c-accent)"
          :disabled="sending"
          @click="poke"
        >
          <Icon name="chat" :size="14" :class="{ 'animate-spin': sending }" />
          让它现在说点什么
        </button>
      </div>
    </template>

    <div v-else class="text-[12px]" style="color: var(--c-text-3)">
      每日生活叙事已停用。打开下方「生成今日生活」开关后,桌宠才会有今天的生活可看。
    </div>

    <div
      v-if="notice"
      class="mt-2 text-[11px] leading-relaxed"
      :style="{
        color: notice.kind === 'ok' ? 'var(--c-accent)' : notice.kind === 'err' ? '#e5484d' : 'var(--c-text-3)',
      }"
    >
      {{ notice.text }}
    </div>
  </div>
</template>
