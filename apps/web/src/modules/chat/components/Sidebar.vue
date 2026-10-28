<script setup lang="ts">
import { computed, ref } from 'vue'
import { state, store } from '../../../shell/store'
import { deleteThread, renameThread } from '../api'
import Icon from '../../../shell/components/Icon.vue'

const emit = defineEmits<{
  newChat: []
  openSettings: []
  select: [id: string]
}>()

const query = ref('')
/** 处于内联操作面板的会话 id(null = 无) */
const menuFor = ref<string | null>(null)
/** 处于重命名编辑的会话 id */
const renamingId = ref<string | null>(null)
const renameDraft = ref('')

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return store.threads
  return store.threads.filter(
    (t) => t.title.toLowerCase().includes(q) || (t.last_preview || '').toLowerCase().includes(q),
  )
})

function fmtDate(iso: string): string {
  const d = new Date(iso.replace(' ', 'T'))
  const now = new Date()
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diffDays = Math.floor((startOfDay(now) - startOfDay(d)) / 86400000)
  if (diffDays <= 0) return d.toTimeString().slice(0, 5)
  if (diffDays === 1) return '昨天'
  if (diffDays < 7) return `${d.getMonth() + 1}/${d.getDate()}`
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}

function onSelect(id: string) {
  if (renamingId.value === id) return
  menuFor.value = null
  emit('select', id)
}

function toggleMenu(id: string) {
  menuFor.value = menuFor.value === id ? null : id
}

function startRename(id: string, title: string) {
  renamingId.value = id
  renameDraft.value = title
  menuFor.value = null
}

async function commitRename(id: string) {
  const title = renameDraft.value.trim()
  renamingId.value = null
  if (!title) return
  try {
    await renameThread(id, title)
    const t = state.threads.find((x) => x.id === id)
    if (t) t.title = title
  } catch (e) {
    state.error = (e as Error).message
  }
}

async function removeThread(id: string) {
  menuFor.value = null
  try {
    await deleteThread(id)
    const i = state.threads.findIndex((t) => t.id === id)
    if (i >= 0) state.threads.splice(i, 1)
    if (state.activeId === id) {
      state.activeId = null
      state.turns = []
    }
  } catch (e) {
    state.error = (e as Error).message
  }
}
</script>

<template>
  <aside
    class="flex h-full w-[264px] shrink-0 flex-col border-r"
    style="background: var(--c-sidebar); border-color: var(--c-border)"
  >
    <!-- 新建对话 -->
    <div class="px-3 pt-3 pb-2">
      <button
        class="btn-wood flex w-full items-center justify-center gap-2 py-2.5 text-[14px] font-medium"
        @click="emit('newChat')"
      >
        <Icon name="plus" :size="16" />
        新对话
      </button>
    </div>

    <!-- 搜索 -->
    <div class="px-3 pb-1.5">
      <div
        class="flex items-center gap-2 rounded-[12px] border px-2.5 py-1.5 transition focus-within:border-[var(--c-peach)]"
        style="border-color: var(--c-border); background: var(--c-input)"
      >
        <Icon name="search" :size="15" style="color: var(--c-text-3)" />
        <input
          v-model="query"
          type="text"
          placeholder="搜索对话…"
          class="w-full bg-transparent text-[13px] outline-none"
          style="color: var(--c-text)"
        />
      </div>
    </div>

    <!-- 会话列表 -->
    <div class="mt-1 flex-1 overflow-y-auto px-2 pb-2">
      <div class="px-2 pt-1.5 pb-0.5 text-[11px] font-medium tracking-wide" style="color: var(--c-text-3)">
        全部对话
      </div>

      <div
        v-for="t in filtered"
        :key="t.id"
        class="side-item flex items-center gap-2 px-2.5 py-2"
        :class="{ active: store.activeId === t.id }"
        @click="onSelect(t.id)"
      >
        <span class="flex h-4 w-4 shrink-0" style="color: var(--c-text-2)">
          <Icon name="chat" :size="16" />
        </span>

        <!-- 重命名编辑态 -->
        <input
          v-if="renamingId === t.id"
          v-model="renameDraft"
          class="min-w-0 flex-1 rounded border bg-transparent px-1 py-0.5 text-[13px] outline-none"
          style="border-color: var(--c-accent); color: var(--c-text)"
          @click.stop
          @keydown.enter="commitRename(t.id)"
          @keydown.esc="renamingId = null"
          @blur="commitRename(t.id)"
        />
        <div v-else class="min-w-0 flex-1">
          <div
            class="truncate text-[13px] font-medium"
            :style="{ color: store.activeId === t.id ? 'var(--c-text)' : 'var(--c-text-2)' }"
          >
            {{ t.title }}
          </div>
          <div class="truncate text-[11px]" style="color: var(--c-text-3)">
            {{ fmtDate(t.updated_at) }} · {{ t.message_count }} 条
          </div>
        </div>

        <!-- 行尾操作 -->
        <div class="flex shrink-0 items-center">
          <template v-if="menuFor === t.id">
            <button
              title="重命名"
              class="rounded-md p-1.5 ui-hover"
              style="color: var(--c-text-2)"
              @click.stop="startRename(t.id, t.title)"
            >
              <Icon name="edit" :size="14" />
            </button>
            <button
              title="删除"
              class="rounded-md p-1.5 ui-hover ui-hover-danger"
              style="color: var(--c-text-2)"
              @click.stop="removeThread(t.id)"
            >
              <Icon name="trash" :size="14" />
            </button>
          </template>
          <button
            v-else
            title="更多操作"
            class="dot-more rounded-md p-1.5 ui-hover focus:opacity-100"
            style="color: var(--c-text-2)"
            @click.stop="toggleMenu(t.id)"
          >
            <Icon name="dots" :size="16" />
          </button>
        </div>
      </div>

      <div v-if="filtered.length === 0" class="px-3 py-8 text-center text-[12px]" style="color: var(--c-text-3)">
        {{ store.threads.length === 0 ? '还没有对话,点上方「新对话」开始' : '未找到匹配的对话' }}
      </div>
    </div>

    <!-- 底部:用户 + 设置入口 -->
    <div class="border-t px-2 py-2" style="border-color: var(--c-border)">
      <button
        class="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 ui-hover"
        @click="emit('openSettings')"
      >
        <span
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[13px] font-medium text-white"
          style="background: var(--c-logo)"
        >
          我
        </span>
        <span class="min-w-0 flex-1 text-left">
          <span class="block truncate text-[13px] font-medium" style="color: var(--c-text)">访客</span>
          <span class="block text-[11px]" style="color: var(--c-text-3)">AI 陪伴助手</span>
        </span>
        <Icon name="settings" :size="16" style="color: var(--c-text-2)" />
      </button>
    </div>
  </aside>
</template>
