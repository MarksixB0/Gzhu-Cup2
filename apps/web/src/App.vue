<script setup lang="ts">
/**
 * 主窗口壳层：内容区 + **右侧功能列表**。
 *
 * 视图首次进入时才挂载，之后用 v-show 切换，切走再切回不丢状态
 * （对话列表与会话、日历翻到的月份、设置里填到一半的表单）。
 *
 * 桌面端可从桌宠的悬停图标直达指定页，走 `gzhuEnv.onSwitchView`。
 */
import { defineAsyncComponent, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import NavRail, { type MainView } from './shell/components/NavRail.vue'
import ChatView from './modules/chat/ChatView.vue'

const CalendarView = defineAsyncComponent(() => import('./modules/calendar/CalendarView.vue'))
const SettingsView = defineAsyncComponent(() => import('./modules/settings/SettingsView.vue'))

const view = ref<MainView>('chat')
/** 进入过的视图；没进过的不挂载、也不发它自己的请求 */
const visited = reactive<Record<MainView, boolean>>({
  chat: true,
  calendar: false,
  settings: false,
})
watch(view, (v) => {
  visited[v] = true
})

/**
 * 主进程发来的「切到某页」（桌宠悬停图标点开即直达）。
 * 只认白名单值：非 Electron 环境没有这个桥（浏览器直连 dev server），静默跳过。
 * 与 ConnectionSettings 里的 `gzhuEnv.openExternal` 同一套可选桥接写法。
 */
const bridge = (
  window as unknown as {
    gzhuEnv?: { onSwitchView?: (h: (v: string) => void) => () => void }
  }
).gzhuEnv
let offSwitchView: (() => void) | null = null
const VIEWS: MainView[] = ['chat', 'calendar', 'settings']

onMounted(() => {
  offSwitchView =
    bridge?.onSwitchView?.((v) => {
      if ((VIEWS as string[]).includes(v)) view.value = v as MainView
    }) ?? null
})

onUnmounted(() => {
  offSwitchView?.()
  offSwitchView = null
})
</script>

<template>
  <div class="flex h-full flex-col overflow-hidden" style="background: var(--c-page)">
    <div class="flex min-h-0 flex-1 overflow-hidden">
      <ChatView
        v-show="view === 'chat'"
        class="min-w-0 flex-1"
        @open-settings="view = 'settings'"
      />
      <CalendarView v-if="visited.calendar" v-show="view === 'calendar'" class="min-w-0 flex-1" />
      <SettingsView v-if="visited.settings" v-show="view === 'settings'" class="min-w-0 flex-1" />

      <NavRail v-model="view" />
    </div>
  </div>
</template>
