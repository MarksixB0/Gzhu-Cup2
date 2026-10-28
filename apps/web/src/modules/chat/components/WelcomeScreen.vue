<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../../../shell/api'
import { store } from '../../../shell/store'

/**
 * 开场白来自**角色卡**（`/api/pet/greeting` → first_mes）。
 *
 * 这里刻意不留任何硬编码问候语：人格文案只可能来自角色卡
 * （见 PET_POLICY.md「提示词方针：写活，不硬编码」）。
 *
 * 快捷引导胶囊是**输入引导**而不是自动发送：点一下只把话术填进输入框并聚焦，
 * 用户仍可改写后再发 —— 不让一键替用户把话说出去。
 */
const emit = defineEmits<{ pick: [text: string] }>()

const greeting = ref<string | null>(null)

/**
 * 白天看太阳、夜间看云朵。昼夜与主题是同一套状态：
 * 浅色=白天、深色=夜间，右上角那个图标按钮切的就是它。
 */
const isDay = computed(() => !store.isDark)

const CHIPS: { icon: string; label: string; fill: string }[] = [
  { icon: '☕', label: '感觉有点累了', fill: '今天感觉有点累，想和你说会儿话。' },
  { icon: '🌱', label: '遇到一件开心的事', fill: '今天遇到一件很开心的事，想讲给你听。' },
]

onMounted(async () => {
  try {
    const r = await api.getPetGreeting()
    greeting.value = r.text
  } catch {
    // 后端不可用：只显示标题，不编造人格文案
  }
})

</script>

<template>
  <div class="flex h-full flex-col items-center justify-center overflow-y-auto px-4 py-8">
    <div class="flex w-full max-w-[720px] flex-col items-center text-center">
      <!-- 欢迎徽章:白天是暖黄小太阳(单独一个),夜里是浅蓝云朵 + 右上角小星星,整组轻微倾斜当贴纸 -->
      <span class="relative mb-6 inline-flex" style="transform: rotate(-4deg)">
        <!-- 白天:暖黄太阳 -->
        <svg
          v-if="isDay"
          viewBox="0 0 24 24"
          class="block h-[58px] w-[58px]"
          style="filter: drop-shadow(0 3px 0 rgba(76, 114, 142, 0.16))"
          aria-hidden="true"
        >
          <!-- 八道光芒:圆头短棒,与圆盘同色 -->
          <g stroke="var(--color-warm-accent)" stroke-width="2" stroke-linecap="round">
            <line x1="12" y1="1.7" x2="12" y2="4.9" />
            <line x1="12" y1="19.1" x2="12" y2="22.3" />
            <line x1="1.7" y1="12" x2="4.9" y2="12" />
            <line x1="19.1" y1="12" x2="22.3" y2="12" />
            <line x1="4.72" y1="4.72" x2="6.98" y2="6.98" />
            <line x1="17.02" y1="17.02" x2="19.28" y2="19.28" />
            <line x1="4.72" y1="17.02" x2="6.98" y2="19.28" />
            <line x1="17.02" y1="4.72" x2="19.28" y2="6.98" />
          </g>
          <circle
            cx="12"
            cy="12"
            r="5.2"
            fill="var(--color-warm-accent)"
            stroke="#fff"
            stroke-width="1.4"
          />
        </svg>

        <!-- 夜里:浅蓝云朵 -->
        <svg
          v-else
          viewBox="0 0 24 24"
          class="block h-[58px] w-[58px]"
          style="filter: drop-shadow(0 3px 0 rgba(76, 114, 142, 0.16))"
          aria-hidden="true"
        >
          <path
            d="M2.25 15a4.5 4.5 0 0 0 4.5 4.5H18a3.75 3.75 0 0 0 1.332-7.257 3 3 0 0 0-3.758-3.848 5.25 5.25 0 0 0-10.233 2.33A4.502 4.502 0 0 0 2.25 15Z"
            fill="var(--color-accent)"
            stroke="#fff"
            stroke-width="1.5"
            stroke-linejoin="round"
          />
        </svg>

        <!-- 小星星只在夜里出现 -->
        <svg
          v-if="!isDay"
          viewBox="0 0 24 24"
          class="absolute -right-3 -top-2 h-[25px] w-[25px]"
          style="filter: drop-shadow(0 2px 0 rgba(76, 114, 142, 0.14))"
          aria-hidden="true"
        >
          <path
            d="M12 2.9l2.6 6.2 6.7.5-5.1 4.4 1.6 6.5L12 16.9l-5.8 3.6 1.6-6.5-5.1-4.4 6.7-.5z"
            fill="var(--color-warm-accent)"
            stroke="#fff"
            stroke-width="1.5"
            stroke-linejoin="round"
          />
        </svg>
      </span>

      <h1 class="font-hand text-[24px] leading-tight tracking-[0.02em]" style="color: var(--c-text)">
        今天想和我说些什么?
      </h1>

      <!-- 快捷引导胶囊:只填入输入框,不直接发送 -->
      <div class="mt-6 flex flex-wrap items-center justify-center gap-2.5">
        <button v-for="c in CHIPS" :key="c.label" class="chip-sticker" @click="emit('pick', c.fill)">
          <span aria-hidden="true">{{ c.icon }}</span>
          <span>{{ c.label }}</span>
        </button>
      </div>

      <!-- 角色卡开场白：未搭载角色卡时整段不显示 -->
      <p
        v-if="greeting"
        class="mt-6 max-w-md whitespace-pre-wrap text-[14px] leading-relaxed"
        style="color: var(--c-text-2)"
      >
        {{ greeting }}
      </p>
    </div>
  </div>
</template>
