<script setup lang="ts">
import { nextTick, ref } from 'vue'
import Icon from '../../../shell/components/Icon.vue'

const props = defineProps<{
  disabled: boolean
  sending: boolean
}>()

const emit = defineEmits<{ send: [text: string]; stop: [] }>()

const text = ref('')
const inputEl = ref<HTMLTextAreaElement | null>(null)

function submit() {
  const value = text.value.trim()
  if (!value || props.disabled || props.sending) return
  emit('send', value)
  text.value = ''
  autoResize()
  inputEl.value?.focus()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}

function autoResize() {
  if (!inputEl.value) return
  inputEl.value.style.height = 'auto'
  inputEl.value.style.height = `${Math.min(inputEl.value.scrollHeight, 180)}px`
}

function focus() {
  nextTick(() => inputEl.value?.focus())
}

/** 由外部(开场引导胶囊)把话术填进来:只填不发,用户还能改 */
function setText(v: string) {
  text.value = v
  nextTick(() => {
    autoResize()
    inputEl.value?.focus()
  })
}
defineExpose({ focus, setText })
</script>

<template>
  <div class="mx-auto w-full max-w-[820px] px-4 pb-4 md:px-6">
    <!-- 状态提示条 -->
    <div
      v-if="sending"
      class="flex items-center justify-between px-1.5 pb-1.5 text-[12px]"
      style="color: var(--c-text-2)"
    >
      <span class="flex items-center gap-2">
        <span class="flex gap-1">
          <span class="h-1.5 w-1.5 animate-bounce rounded-full [animation-delay:0ms]" style="background: var(--c-accent)" />
          <span class="h-1.5 w-1.5 animate-bounce rounded-full [animation-delay:150ms]" style="background: var(--c-accent)" />
          <span class="h-1.5 w-1.5 animate-bounce rounded-full [animation-delay:300ms]" style="background: var(--c-accent)" />
        </span>
        正在想怎么回你…
      </span>
    </div>

    <div
      class="composer flex items-end gap-2 rounded-[24px] border-[1.5px] p-2"
      style="border-color: var(--border-soft); background: var(--c-card)"
    >
      <textarea
        ref="inputEl"
        v-model="text"
        rows="1"
        class="max-h-[180px] flex-1 resize-none bg-transparent px-3.5 pt-2.5 text-[15px] leading-relaxed outline-none placeholder:opacity-70"
        style="color: var(--c-text)"
        placeholder="发送消息…(Enter 发送, Shift+Enter 换行)"
        :disabled="disabled"
        @keydown="onKeydown"
        @input="autoResize"
      />

      <!-- 发送 / 停止 -->
      <button
        v-if="sending"
        title="停止生成"
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white transition hover:opacity-85"
        style="background: var(--c-text-3)"
        @click="emit('stop')"
      >
        <Icon name="stop" :size="15" />
      </button>
      <button
        v-else
        title="发送"
        class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-35"
        style="background: var(--color-accent); box-shadow: 0 2px 0 rgba(76, 114, 142, 0.28)"
        :disabled="disabled || sending || !text.trim()"
        @click="submit"
      >
        <Icon name="send" :size="16" />
      </button>
    </div>
  </div>
</template>
