<script setup lang="ts">
/**
 * 角色卡视图 —— 人格在这里被创建、选中与修改。
 *
 * ⚠ 应用**不自带任何预设人格**:仓库里没有卡文件,安装包也不带。所以对刚装好的用户
 * 来说,本页的第一等大事不是"切换",而是**先把卡建出来** —— 空态必须给出明确入口。
 *
 * 三条边界:
 *   - 只经 /api/pet/cards 读写卡文件,不碰对话与记忆;
 *   - 「使用」是运行时的选择(存数据目录的 active_card.json),不改 config;
 *   - 删除要二次确认 —— 卡是人格的唯一副本,删了就真没了。
 */
import { computed, onMounted, ref } from 'vue'
import { createCard, deleteCard, listCards, setActiveCard, updateCard } from './api'
import type { CardItem } from './types'
import Icon from '../../shell/components/Icon.vue'

/**
 * `embedded` = 作为设置里的分页内嵌：此时外层已有滚动容器与卡片留白，
 * 本组件不再自带整页背景、页头与滚动条。
 */
const props = defineProps<{ embedded?: boolean }>()

const cards = ref<CardItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
/** 正在处理的那张卡(改名/启用/删除),用来只锁住它自己而不是整页 */
const busy = ref<string | null>(null)
/** 待确认删除的卡名 */
const confirming = ref<string | null>(null)

/* ---------- 新建 / 编辑表单 ---------- */
interface CardForm {
  mode: 'create' | 'edit'
  name: string
  description: string
  greeting: string
}
const editing = ref<CardForm | null>(null)
const formError = ref<string | null>(null)
const saving = ref(false)

function apply(r: { cards: CardItem[]; active: string | null }) {
  cards.value = r.cards
}

async function load() {
  loading.value = true
  error.value = null
  try {
    apply(await listCards())
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

/** 建/改/删/换用的统一收敛点:错误进 error,忙碌标记一定清掉。 */
async function run(name: string, fn: () => Promise<{ cards: CardItem[]; active: string | null }>) {
  busy.value = name
  error.value = null
  try {
    apply(await fn())
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = null
  }
}

function openCreate() {
  formError.value = null
  editing.value = { mode: 'create', name: '', description: '', greeting: '' }
}

function openEdit(c: CardItem) {
  formError.value = null
  editing.value = {
    mode: 'edit',
    name: c.name,
    description: c.description,
    greeting: c.greeting,
  }
}

async function submit() {
  const form = editing.value
  if (!form) return
  // 先在前端挡一道,少一次往返;后端仍会再校验(它才是权威)
  if (!form.name.trim()) {
    formError.value = '角色名不能为空'
    return
  }
  if (!form.description.trim()) {
    formError.value = '提示词正文不能为空'
    return
  }

  saving.value = true
  formError.value = null
  error.value = null   // 与 run() 一致:新建/编辑成功后也要清掉页面级错误
  try {
    if (form.mode === 'create') {
      apply(
        await createCard({
          name: form.name.trim(),
          description: form.description,
          first_mes: form.greeting,
        }),
      )
    } else {
      apply(
        await updateCard(form.name, {
          description: form.description,
          first_mes: form.greeting,
        }),
      )
    }
    editing.value = null
  } catch (e) {
    formError.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

/** 点「切换」= 启用这张卡;已启用的那张再点是无操作(不允许从卡片页静默卸卡)。 */
function activate(c: CardItem) {
  if (c.active) return
  void run(c.name, () => setActiveCard(c.name))
}

function remove(c: CardItem) {
  confirming.value = null
  void run(c.name, () => deleteCard(c.name))
}

/* ---------- 拍立得外观：色卡、首字、贴纸 ---------- */

/**
 * 相纸色块。先按卡名散列定位，若这一档已被前面的卡占用就顺延一格 ——
 * 同一张卡不会因为列表顺序变化而随机换色，同屏又不会撞色。
 */
const TINTS = [
  { bg: '#d9eaf6', bgDark: '#22394a', ink: '#45708c', inkDark: '#a9cbe2', tape: '#bfddf2' },
  { bg: '#fff3d6', bgDark: '#463c23', ink: '#b08a3e', inkDark: '#ecd39a', tape: '#f9e3a2' },
  { bg: '#e3f0ec', bgDark: '#26403c', ink: '#5f8d86', inkDark: '#a9d0c8', tape: '#cfe7e0' },
  { bg: '#fde9e9', bgDark: '#472a2d', ink: '#c08080', inkDark: '#f2b8bd', tape: '#f8d3d3' },
  { bg: '#eae6f4', bgDark: '#322c46', ink: '#7e76a6', inkDark: '#c3bae4', tape: '#dcd5ee' },
]

function hashOf(name: string): number {
  let h = 0
  for (const ch of name) h = (h * 31 + (ch.codePointAt(0) ?? 0)) % 9973
  return h
}

const tintIndex = computed(() => {
  const taken: number[] = []
  const map = new Map<string, number>()
  for (const c of cards.value) {
    let i = hashOf(c.name) % TINTS.length
    for (let step = 0; step < TINTS.length && taken.includes(i); step++) {
      i = (i + 1) % TINTS.length
    }
    taken.push(i)
    map.set(c.name, i)
  }
  return map
})

function tintOf(name: string) {
  return TINTS[tintIndex.value.get(name) ?? hashOf(name) % TINTS.length]
}

/** 立绘位没有图：用卡名首字当"手写字母贴纸" */
function initialOf(name: string): string {
  return [...name.trim()][0] ?? '?'
}

/** 相纸下方的一行摘要：压掉换行，交给 CSS 截两行 */
function excerptOf(c: CardItem): string {
  return c.description.replace(/\s+/g, ' ').trim()
}

onMounted(load)
</script>

<template>
  <div :class="props.embedded ? '' : 'flex h-full flex-col overflow-hidden'" :style="props.embedded ? undefined : { background: 'var(--c-page)' }">
    <header v-if="!props.embedded" class="flex h-[52px] shrink-0 items-center px-5">
      <div class="mx-auto flex w-full max-w-[1120px] items-center gap-2.5">
        <span
          class="flex h-7 w-7 shrink-0 items-center justify-center rounded-[9px]"
          style="background: var(--chip-bg); color: var(--chip-ink)"
        >
          <Icon name="user" :size="15" />
        </span>
        <div
          class="flex-1 font-hand text-[20px] leading-none tracking-[0.3px]"
          style="color: var(--c-text)"
        >
          角色卡
        </div>
        <button class="mini-btn" :disabled="loading" @click="load">
          <Icon name="refresh" :size="13" :class="{ 'animate-spin': loading }" />
          刷新
        </button>
        <button
          class="btn-wood flex items-center gap-1.5 px-3.5 py-2 text-[13px] font-medium"
          @click="openCreate"
        >
          <Icon name="plus" :size="14" />
          新建
        </button>
      </div>
    </header>

    <div :class="props.embedded ? '' : 'flex-1 overflow-y-auto px-5 pb-8 pt-4'">
      <div class="mx-auto flex min-h-full w-full max-w-[1120px] flex-col">
        <div
          v-if="error"
          class="mb-4 rounded-xl border px-3.5 py-2.5 text-[13px]"
          style="border-color: rgba(220, 38, 38, 0.35); background: rgba(220, 38, 38, 0.08); color: #dc2626"
        >
          {{ error }}
        </div>

        <div v-if="cards.length" class="polaroid-grid">
          <article
            v-for="c in cards"
            :key="c.name"
            class="polaroid"
            :class="{ 'is-on': c.active }"
          >
            <!-- 选中的那张:左上角钉一个「当前」方框(只有多张时才有标识意义) -->
            <span v-if="c.active && cards.length > 1" class="polaroid-current">当前</span>

            <!-- 右上角纸胶带：颜色跟着这张卡的色档走 -->
            <span class="polaroid-tape" :style="{ backgroundColor: tintOf(c.name).tape }" />

            <!-- 立绘卡槽：没有图，用卡名首字当手写字母贴纸 -->
            <div
              class="polaroid-photo"
              :style="{
                '--tint': tintOf(c.name).bg,
                '--tint-d': tintOf(c.name).bgDark,
                '--ink': tintOf(c.name).ink,
                '--ink-d': tintOf(c.name).inkDark,
              }"
            >
              <span class="polaroid-initial">{{ initialOf(c.name) }}</span>
              <Icon name="paw" :size="15" class="polaroid-paw" />
            </div>

            <h3 class="polaroid-name">{{ c.name }}</h3>

            <p v-if="!c.error" class="polaroid-desc">{{ excerptOf(c) }}</p>
            <p v-else class="polaroid-desc" style="color: #d14548">{{ c.error }}</p>

            <div class="polaroid-foot">
              <template v-if="confirming === c.name">
                <span class="mr-auto text-[11.5px]" style="color: #d14548">确认删除？</span>
                <button class="mini-btn" @click="confirming = null">取消</button>
                <button
                  class="flex h-7 items-center rounded-[9px] px-2.5 text-[12px] font-medium text-white disabled:opacity-60"
                  style="background: #e5484d"
                  :disabled="busy === c.name"
                  @click="remove(c)"
                >
                  删除
                </button>
              </template>
              <template v-else>
                <button
                  v-if="!c.error"
                  class="flex h-7 items-center gap-1 rounded-[9px] px-3 text-[12px] font-medium transition disabled:opacity-60"
                  :style="
                    c.active
                      ? 'background: var(--bg-subtle); color: var(--color-primary)'
                      : 'background: var(--c-nav-active-bg); color: var(--c-nav-active-text)'
                  "
                  :disabled="busy === c.name || c.active"
                  @click="activate(c)"
                >
                  {{ c.active ? '使用中' : '切换' }}
                </button>
                <button
                  v-if="!c.error"
                  class="mini-btn ml-auto"
                  :disabled="busy === c.name"
                  @click="openEdit(c)"
                >
                  <Icon name="edit" :size="12" />
                  编辑
                </button>
                <button
                  class="mini-btn is-danger"
                  :class="{ 'ml-auto': !!c.error }"
                  :disabled="busy === c.name"
                  @click="confirming = c.name"
                >
                  <Icon name="trash" :size="12" />
                  删除
                </button>
              </template>
            </div>
          </article>
        </div>

        <!-- 空态：新装的 / 便携拷来的应用一律走到这里 -->
        <div
          v-if="!loading && !error && cards.length === 0"
          class="flex flex-1 flex-col items-center justify-center pb-20 text-center"
        >
          <svg
            class="empty-frame"
            width="132"
            height="132"
            viewBox="0 0 104 104"
            fill="none"
            aria-hidden="true"
          >
            <circle cx="30" cy="12" r="3.2" stroke="currentColor" stroke-width="2.4" />
            <circle cx="74" cy="12" r="3.2" stroke="currentColor" stroke-width="2.4" />
            <rect
              x="13"
              y="20"
              width="78"
              height="68"
              rx="9"
              stroke="currentColor"
              stroke-width="3"
            />
            <rect
              x="23"
              y="30"
              width="58"
              height="48"
              rx="5"
              stroke="currentColor"
              stroke-width="2"
              stroke-dasharray="5 5"
            />
            <path
              d="M52 65c-7.4-5.4-11.4-9.5-11.4-13.9 0-3.1 2.3-5.5 5.3-5.5 2.3 0 4.5 1.4 6.1 3.9 1.6-2.5 3.8-3.9 6.1-3.9 3 0 5.3 2.4 5.3 5.5 0 4.4-4 8.5-11.4 13.9Z"
              fill="currentColor"
              opacity="0.5"
            />
          </svg>
          <p
            class="font-hand mt-7 max-w-[440px] text-[21px] leading-[1.9]"
            style="color: var(--text-sub)"
          >
            这里空空如也呢～<br />
            快点击右上角的「新建」，<br />
            捏一个专属于你的桌宠小伙伴吧！
          </p>
        </div>
      </div>
    </div>

    <!-- 新建 / 编辑表单 -->
    <div
      v-if="editing"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-[2px]"
      @click.self="editing = null"
    >
      <div
        class="fade-in flex max-h-[86vh] w-full max-w-[560px] flex-col overflow-hidden rounded-2xl border shadow-2xl"
        :style="{ background: 'var(--c-card)', borderColor: 'var(--c-border)' }"
      >
        <div class="flex items-center justify-between px-5 py-4">
          <h2 class="text-[15px] font-semibold" style="color: var(--c-text)">
            {{ editing.mode === 'create' ? '新建角色卡' : `编辑「${editing.name}」` }}
          </h2>
          <button
            class="rounded-lg p-1.5 ui-hover"
            style="color: var(--c-text-2)"
            @click="editing = null"
          >
            <Icon name="close" :size="18" />
          </button>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto px-5 pb-2 text-[14px]">
          <label class="mb-1 block text-[12px] font-medium" style="color: var(--c-text-2)">
            角色名
          </label>
          <input
            v-model="editing.name"
            type="text"
            :disabled="editing.mode === 'edit'"
            class="w-full rounded-xl border px-3 py-2.5 text-[14px] outline-none transition focus:border-[var(--c-accent)] disabled:opacity-60"
            style="border-color: var(--c-border); background: var(--c-input); color: var(--c-text)"
            spellcheck="false"
            placeholder="例如：小雨"
          />
          <p class="mt-1 text-[11px]" style="color: var(--c-text-3)">
            {{ editing.mode === 'edit'
              ? '角色名同时是这张卡的文件标识，建好后不可改。'
              : '不能用 \\ / : * ? " &lt; &gt; | 这些字符。' }}
          </p>

          <label class="mb-1 mt-4 block text-[12px] font-medium" style="color: var(--c-text-2)">
            提示词正文
          </label>
          <textarea
            v-model="editing.description"
            rows="10"
            class="w-full resize-none rounded-xl border px-3 py-2.5 text-[13px] leading-relaxed outline-none transition focus:border-[var(--c-accent)]"
            style="border-color: var(--c-border); background: var(--c-input); color: var(--c-text)"
            spellcheck="false"
            placeholder="你希望它是怎样的人？身份、性格、说话方式、边界……写得越具体，它越稳定。"
          />

          <label class="mb-1 mt-4 block text-[12px] font-medium" style="color: var(--c-text-2)">
            开场白
          </label>
          <textarea
            v-model="editing.greeting"
            rows="3"
            class="w-full resize-none rounded-xl border px-3 py-2.5 text-[13px] leading-relaxed outline-none transition focus:border-[var(--c-accent)]"
            style="border-color: var(--c-border); background: var(--c-input); color: var(--c-text)"
            spellcheck="false"
            placeholder="启动时它说的第一句话；留空则不主动开口。"
          />
        </div>

        <div
          v-if="formError"
          class="mx-5 mb-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-[12px]"
          style="border-color: #e5484d; color: #e5484d; background: var(--c-input)"
        >
          <Icon name="alert" :size="14" class="mt-0.5 shrink-0" />
          <span>{{ formError }}</span>
        </div>

        <div
          class="flex items-center justify-end gap-2 border-t px-5 py-3"
          style="border-color: var(--c-border)"
        >
          <button
            class="rounded-xl border px-4 py-2 text-[13px] transition ui-hover"
            style="border-color: var(--c-border); color: var(--c-text-2)"
            @click="editing = null"
          >
            取消
          </button>
          <button
            class="rounded-xl px-4 py-2 text-[13px] font-medium text-white transition hover:opacity-90 disabled:opacity-60"
            style="background: var(--c-accent)"
            :disabled="saving"
            @click="submit"
          >
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
