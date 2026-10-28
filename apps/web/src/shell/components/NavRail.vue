<script lang="ts">
/** 主窗口的功能视图。放在普通 script 块里以便被 App.vue 引入。 */
export type MainView = 'chat' | 'calendar' | 'settings'
</script>

<script setup lang="ts">
import Icon from './Icon.vue'

/**
 * 主窗口右侧的功能列表：对话 / 日历 / 设置，按使用频率排序。
 *
 * v-model 绑定当前视图；切换只改视图，已挂载的页面不销毁（见 App.vue 的 v-show）。
 */
defineProps<{ modelValue: MainView }>()
const emit = defineEmits<{ 'update:modelValue': [value: MainView] }>()

const ITEMS: { key: MainView; label: string; icon: string }[] = [
  { key: 'chat', label: '对话', icon: 'chat' },
  { key: 'calendar', label: '日历', icon: 'calendar' },
  { key: 'settings', label: '设置', icon: 'settings' },
]
</script>

<template>
  <nav
    class="flex h-full w-[78px] shrink-0 flex-col items-center gap-1.5 border-l py-3"
    style="background: var(--c-sidebar); border-color: var(--c-border)"
  >
    <button
      v-for="it in ITEMS"
      :key="it.key"
      class="nav-item flex w-[62px] flex-col items-center gap-1 rounded-[12px] py-2.5 text-[11px]"
      :class="{ 'is-on': modelValue === it.key }"
      :style="
        modelValue === it.key
          ? { color: 'var(--c-nav-active-text)', background: 'var(--c-nav-active-bg)' }
          : { color: 'var(--c-text-2)' }
      "
      :title="it.label"
      @click="emit('update:modelValue', it.key)"
    >
      <Icon :name="it.icon" :size="20" />
      <span>{{ it.label }}</span>
    </button>
  </nav>
</template>
