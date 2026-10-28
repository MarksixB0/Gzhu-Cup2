<script setup lang="ts">
/**
 * 主题切换钮：太阳/月亮在浅色 ↔ 深色之间切换。
 *
 * 对话 / 日历 / 设置三个视图共用本组件，保证三处入口行为一致。
 */
import Icon from './Icon.vue'
import { store, setTheme } from '../store'

function toggleLightDark() {
  setTheme(store.isDark ? 'light' : 'dark')
}
</script>

<template>
  <span class="theme-toggle">
    <button
      class="tt-btn"
      :title="store.isDark ? '切换到白天（浅色）' : '切换到夜间（深色）'"
      @click="toggleLightDark"
    >
      <Icon :name="store.isDark ? 'moon' : 'sun'" :size="17" />
    </button>
  </span>
</template>

<style scoped>
.theme-toggle {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.tt-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  padding: 7px;
  background: transparent;
  color: var(--c-text-2);
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
}

.tt-btn:hover {
  background: var(--c-hover, rgba(0, 0, 0, 0.06));
}
</style>
