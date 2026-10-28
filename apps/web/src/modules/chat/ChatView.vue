<script setup lang="ts">
/**
 * 对话视图：侧栏（会话列表）+ 消息区 + 输入区。
 *
 * 数据与流程在 useChat（模块内），这里只留界面：滚动、聚焦、抽屉收起、主题开关。
 * 设置入口上抛给 App.vue。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { state, store, clearError } from '../../shell/store'
import Icon from '../../shell/components/Icon.vue'
import ThemeToggle from '../../shell/components/ThemeToggle.vue'
import Sidebar from './components/Sidebar.vue'
import WelcomeScreen from './components/WelcomeScreen.vue'
import MessageItem from './components/MessageItem.vue'
import MessageInput from './components/MessageInput.vue'
import { useChat } from './useChat'

const emit = defineEmits<{ openSettings: [] }>()

const chat = useChat()
const { pendingUser, streamText, tokensStarted } = chat

const messageListEl = ref<HTMLDivElement | null>(null)
const inputEl = ref<InstanceType<typeof MessageInput> | null>(null)

const isEmptyView = computed(() => store.activeId === null && store.turns.length === 0)
const activeTitle = computed(
  () => store.threads.find((t) => t.id === store.activeId)?.title ?? '新对话',
)

function scrollToBottom() {
  nextTick(() => {
    const el = messageListEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

/** 开场引导胶囊：把话术填进输入框并聚焦，不自动发送 */
function fillDraft(text: string) {
  inputEl.value?.setText(text)
}

function closeMobile() {
  if (window.matchMedia('(min-width: 768px)').matches) return
  state.mobileNav = false
}

/* ---------- 界面动作：包一层模块方法，补上聚焦与抽屉 ---------- */
async function onSelectThread(id: string) {
  await chat.selectThread(id)
  closeMobile()
  scrollToBottom()
  nextTick(() => inputEl.value?.focus())
}

function onNewChat() {
  chat.newChat()
  closeMobile()
  nextTick(() => inputEl.value?.focus())
}

function toggleSidebarOrNav() {
  if (window.matchMedia('(min-width: 768px)').matches) state.sidebarOpen = !state.sidebarOpen
  else state.mobileNav = !state.mobileNav
}

watch(
  () => [
    store.activeId,
    store.turns,
    pendingUser.value,
    streamText.value,
    tokensStarted.value,
    store.sending,
  ],
  () => scrollToBottom(),
)

onMounted(() => scrollToBottom())
</script>

<template>
  <div class="flex h-full overflow-hidden" style="background: var(--c-page)">
    <!-- ===== 侧栏(移动抽屉 + 桌面可收起,同一 DOM) ===== -->
    <div
      class="sidebar-root shadow-2xl md:shadow-none"
      :class="{ open: store.mobileNav, 'closed-desktop': !store.sidebarOpen }"
    >
      <Sidebar @new-chat="onNewChat" @open-settings="emit('openSettings')" @select="onSelectThread" />
    </div>

    <!-- 移动遮罩 -->
    <div
      v-if="store.mobileNav"
      class="fixed inset-0 z-30 bg-black/40 md:hidden"
      @click="state.mobileNav = false"
    />

    <!-- ===== 主区 ===== -->
    <main class="flex h-full min-w-0 flex-1 flex-col">
      <!-- 顶栏 -->
      <header
        class="flex h-12 shrink-0 items-center gap-2 border-b px-3 md:px-4"
        style="border-color: var(--c-border)"
      >
        <button
          class="rounded-lg p-2 ui-hover"
          style="color: var(--c-text-2)"
          title="切换侧栏"
          @click="toggleSidebarOrNav"
        >
          <Icon :name="store.sidebarOpen && !store.mobileNav ? 'chevronLeft' : 'menu'" :size="18" />
        </button>
        <div class="min-w-0 flex-1 truncate text-center text-[14px] font-medium md:text-left" style="color: var(--c-text)">
          <template v-if="store.activeId">{{ activeTitle }}</template>
          <template v-else>新对话</template>
        </div>

        <!-- 桌宠未读:点一下跳到它最近说话的那条会话(消息本体在对话流里) -->
        <button
          v-if="store.pendingCare.length"
          class="flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] transition ui-hover"
          style="color: var(--c-accent); background: var(--c-accent-soft); border-color: var(--c-accent)"
          :title="`桌宠有 ${store.pendingCare.length} 条新消息`"
          @click="chat.openLatestCare"
        >
          <Icon name="chat" :size="12" />
          {{ store.pendingCare.length }} 条新消息
        </button>
        <!-- 主题切换:昼夜二态,三视图共用同一组件(shell/components/ThemeToggle) -->
        <ThemeToggle />
        <button
          class="rounded-lg p-2 ui-hover"
          style="color: var(--c-text-2)"
          title="设置"
          @click="emit('openSettings')"
        >
          <Icon name="settings" :size="17" />
        </button>
      </header>

      <!-- 错误横幅 -->
      <div v-if="store.error" class="shrink-0 px-4 pt-3">
        <div
          class="mx-auto flex w-full max-w-[820px] items-start gap-2 rounded-xl border px-3.5 py-2.5 text-[13px]"
          style="border-color: rgba(220, 38, 38, 0.35); background: rgba(220, 38, 38, 0.08); color: #dc2626"
        >
          <span class="mt-0.5"><Icon name="alert" :size="14" /></span>
          <span class="flex-1 whitespace-pre-wrap">{{ store.error }}</span>
          <button class="opacity-70 hover:opacity-100" @click="clearError()">
            <Icon name="close" :size="14" />
          </button>
        </div>
      </div>

      <!-- 消息区 -->
      <div ref="messageListEl" class="flex-1 overflow-y-auto" style="background: var(--c-page)">
        <!-- 加载中 -->
        <div v-if="store.loadingThread" class="flex h-full items-center justify-center text-[13px]" style="color: var(--c-text-3)">
          正在加载对话…
        </div>

        <!-- 欢迎页 -->
        <WelcomeScreen v-else-if="isEmptyView && !store.sending" @pick="fillDraft" />

        <template v-else>
          <div class="py-3">
            <!-- 历史轮次 -->
            <MessageItem
              v-for="(t, i) in store.turns"
              :key="t.ts + '-' + i"
              :turn="t"
            />
          </div>

          <!-- 发送中:用户消息即时可见 + AI 流式气泡/等待占位 -->
          <div v-if="store.sending" class="mx-auto w-full max-w-[820px] px-4 md:px-6">
            <div v-if="pendingUser" class="flex justify-end py-1">
              <div
                class="max-w-[85%] whitespace-pre-wrap rounded-3xl rounded-br-lg px-4 py-2.5 text-[15px]"
                style="background: var(--c-bubble-user); color: var(--c-user-text)"
              >
                {{ pendingUser }}
              </div>
            </div>
            <!-- AI:首 token 后切流式气泡;之前只显示等待 -->
            <div v-if="tokensStarted" class="flex flex-col py-1">
              <div class="max-w-full whitespace-pre-wrap text-[15px] leading-relaxed" style="color: var(--c-text)">
                {{ streamText }}<span class="opacity-60">▍</span>
              </div>
            </div>
            <div v-else class="flex items-center gap-2 py-3 text-[14px]" style="color: var(--c-text-2)">
              <span class="flex gap-1">
                <span class="h-1.5 w-1.5 animate-bounce rounded-full [animation-delay:0ms]" style="background: var(--c-text-3)" />
                <span class="h-1.5 w-1.5 animate-bounce rounded-full [animation-delay:150ms]" style="background: var(--c-text-3)" />
                <span class="h-1.5 w-1.5 animate-bounce rounded-full [animation-delay:300ms]" style="background: var(--c-text-3)" />
              </span>
              正在想怎么回你…
            </div>
          </div>

          <div class="h-2" />
        </template>
      </div>

      <!-- 输入区 -->
      <div class="shrink-0 pt-2" style="background: var(--c-page)">
        <MessageInput
          ref="inputEl"
          :disabled="store.loadingThread"
          :sending="store.sending"
          @send="(t: string) => void chat.send(t)"
          @stop="chat.stopCurrent"
        />
      </div>
    </main>
  </div>
</template>
