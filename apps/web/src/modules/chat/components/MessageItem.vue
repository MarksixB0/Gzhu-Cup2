<script setup lang="ts">
import { computed, ref } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { CARE_KIND_LABEL, type Turn } from '../../../types'
import Icon from '../../../shell/components/Icon.vue'

/** 完整一轮 */
const props = defineProps<{
  turn: Turn
}>()

const copied = ref(false)

/** 系统主动发起的消息:桌宠自己开口,没有用户那一侧 */
const isProactive = computed(() => props.turn.kind === 'proactive')

/**
 * 用户那一侧只在**真的有话**时渲染。
 * 历史数据里存在 `user=""` 的轮次(桌宠主动开口的消息),
 * 无条件渲染会多出一个空的用户气泡 —— 用户看到的就是对话流里凭空一个空格。
 */
const hasUser = computed(() => String(props.turn.user ?? '').trim().length > 0)

/** inline:用户正在聊天时它顺口接的一句 —— 用轻量标注,不摆卡片式文案 */
const isInline = computed(() => isProactive.value && !!props.turn.inline)

const proactiveLabel = computed(
  () => CARE_KIND_LABEL[props.turn.care_kind ?? ''] ?? '主动消息',
)

function renderMarkdown(content: string): string {
  return DOMPurify.sanitize(marked.parse(content, { async: false }) as string)
}

async function copyReply() {
  try {
    await navigator.clipboard.writeText(props.turn.ai)
    copied.value = true
    setTimeout(() => (copied.value = false), 1800)
  } catch {
    /* ignore */
  }
}
</script>

<template>
  <div class="mx-auto w-full max-w-[820px] px-4 md:px-6">
    <!-- ============ 用户消息:右对齐气泡(主动消息没有这一侧) ============ -->
    <div v-if="hasUser" class="flex justify-end py-1">
      <div
        class="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-lg px-4 py-2.5 text-[15px] leading-relaxed"
        style="background: var(--c-bubble-user); color: var(--c-user-text)"
      >
        {{ turn.user }}
      </div>
    </div>

    <!-- ============ AI 回复:贴页正文(白板:无评估卡,只显示回复本身) ============ -->
    <div class="group flex flex-col py-1">
      <!-- 主动消息:标注"是它先开口的",让它在对话流里可辨认 -->
      <div
        v-if="isProactive"
        class="mb-1 flex items-center gap-1 text-[11px]"
        :style="{ color: isInline ? 'var(--c-text-3)' : 'var(--c-accent)' }"
      >
        <Icon name="chat" :size="11" />
        <span v-if="isInline">主动搭话</span>
        <span v-else>主动消息 · {{ proactiveLabel }}</span>
        <span class="ml-1 font-mono" style="color: var(--c-text-3)">{{ turn.ts.slice(11, 16) }}</span>
      </div>

      <div class="flex items-center justify-end gap-1 pb-0.5 opacity-0 transition group-hover:opacity-100">
        <button
          title="复制回复"
          class="rounded-md p-1 ui-hover"
          style="color: var(--c-text-3)"
          @click="copyReply"
        >
          <Icon v-if="!copied" name="copy" :size="14" />
          <Icon v-else name="check" :size="14" style="color: var(--color-primary)" />
        </button>
      </div>

      <div
        class="md-body max-w-full"
        style="font-size: 15px"
        v-html="renderMarkdown(turn.ai)"
      />
    </div>
  </div>
</template>
