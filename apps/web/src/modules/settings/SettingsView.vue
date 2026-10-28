<script setup lang="ts">
import { ref, watch, watchEffect } from 'vue'
import { store, state, persistSettings, setDevMode } from '../../shell/store'
import { PET_CARDS } from './spec'
import Icon from '../../shell/components/Icon.vue'
import ThemeToggle from '../../shell/components/ThemeToggle.vue'
import CardsView from '../cards/CardsView.vue'
import ConnectionSettings from './components/ConnectionSettings.vue'
import DebugSettings from './components/DebugSettings.vue'
import MemoryPanel from './panels/MemoryPanel.vue'
import PetDailyPanel from './components/PetDailyPanel.vue'
import PrivacySettings from './components/PrivacySettings.vue'
import SettingsForm from './components/SettingsForm.vue'

/**
 * 设置视图。
 *
 * 左栏分页 + 右侧内容区；「记忆」「调试」是开发者分页（见 store.devMode）。
 * 「记忆」由 MemoryPanel 承担，调试页的「看完整洞察」直接切到这个分页。
 * 主题切换在页头（跟随系统 + 昼夜），不再单独设「外观」分页。
 */

/**
 * 「关于」页里的素材来源外链。
 *
 * Electron 里走主进程的白名单桥（`gzhuEnv.openExternal`，只放行 github.com）；
 * 浏览器直连 dev server 时没有这个桥，回落到 `window.open`。
 */
const VPET_URL = 'https://github.com/LorisYounger/VPet'
function openVpet(): void {
  const bridge = (
    window as unknown as { gzhuEnv?: { openExternal?: (url: string) => Promise<boolean> } }
  ).gzhuEnv?.openExternal
  if (bridge) {
    void bridge(VPET_URL)
    return
  }
  window.open(VPET_URL, '_blank', 'noopener')
}

/* ---------- 左栏 tab,OpenWebUI 设置面板风格 ----------
   「记忆」「调试」归在「开发者模式」分支下,同样受 store.devMode 门控。 */
type TabKey =
  | 'me'
  | 'cards'
  | 'model'
  | 'pet'
  | 'about'
  | 'memory'
  | 'debug'
const activeTab = ref<TabKey>('me')

/** 常规分页:普通用户会看到的全部。 */
const MAIN_TABS: { key: TabKey; label: string; icon: string }[] = [
  // 「用户」放最前:它就是第一次见面问的三件事(称呼 / 喜欢 / 讨厌),
  // 隐私也并在这一页(数据存储位置与删除入口)。
  { key: 'me', label: '用户', icon: 'user' },
  // 角色卡:人格的建/选/改(开场白也由它决定)
  { key: 'cards', label: '角色卡', icon: 'user' },
  // 模型与 API 连接合并成一个分页:选模型与配连接本来就是同一件事的两半
  { key: 'model', label: '模型与连接', icon: 'chip' },
  { key: 'pet', label: '桌宠', icon: 'chat' },
  { key: 'about', label: '关于', icon: 'info' },
]

/** 开发者分页:普通模式下连分支头一起隐藏(见 store.devMode)。 */
const DEV_TABS: { key: TabKey; label: string; icon: string }[] = [
  // 记忆与调试成对:前者看结论(完整洞察),后者看计数与原始字段
  { key: 'memory', label: '记忆', icon: 'chart' },
  { key: 'debug', label: '调试', icon: 'info' },
]

// 退出开发者模式时若正停在开发者分页,退回「用户」—— 否则内容区会剩一片空白
watch(
  () => store.devMode,
  (on) => {
    if (!on && DEV_TABS.some((t) => t.key === activeTab.value)) activeTab.value = 'me'
  },
)

/* ---------- 模型 ---------- */
const modelDraft = ref(store.settings.model)
const tempDraft = ref(store.settings.temperature)

watchEffect(() => {
  modelDraft.value = store.settings.model
  tempDraft.value = store.settings.temperature
})

function commitModel() {
  const m = modelDraft.value.trim()
  state.settings.model = m // 允许留空 = 跟随后端 config 默认
  persistSettings()
}

function onTemp() {
  state.settings.temperature = tempDraft.value
  persistSettings()
}
</script>

<template>
  <div class="flex h-full min-h-0 w-full flex-col" style="background: var(--c-page)">
    <div
      class="flex h-full min-h-0 w-full flex-col overflow-hidden border"
      :style="{ background: 'var(--c-page)', borderColor: 'var(--c-border)' }"
    >
      <div class="flex items-center px-6 py-4">
        <h2 class="font-hand text-[20px] leading-none" style="color: var(--c-text)">设置</h2>
        <!-- 主题切换:跟随系统 + 昼夜,与对话/日历页同一组件 -->
        <ThemeToggle class="ml-auto" />
      </div>

      <div class="flex min-h-0 flex-1">
        <!-- 左栏导航：选中为奶黄卡片 + 图标点缀 -->
        <nav
          class="w-[176px] shrink-0 overflow-y-auto border-r px-2.5 py-3"
          style="border-color: var(--c-border)"
        >
          <!-- 常规分页 -->
          <button
            v-for="t in MAIN_TABS"
            :key="t.key"
            class="set-nav-item mb-1 flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[13px]"
            :class="{ 'is-on': activeTab === t.key }"
            @click="activeTab = t.key"
          >
            <span class="set-nav-ico flex h-6 w-6 shrink-0 items-center justify-center rounded-lg">
              <Icon :name="t.icon" :size="15" />
            </span>
            <span class="truncate">{{ t.label }}</span>
          </button>

          <!-- 开发者模式分支:开关常驻左栏底部(默认开启)。锁随开关开合:
               开 = 开锁,记忆/调试可见;关 = 闭锁,两者一并隐藏。 -->
          <div class="mt-3 border-t pt-2" style="border-color: var(--c-border)">
            <div class="flex items-center gap-2.5 px-2.5 py-2">
              <span
                class="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg"
                :style="{
                  background: store.devMode ? 'var(--chip-bg)' : 'transparent',
                  color: store.devMode ? 'var(--chip-ink)' : 'var(--c-text-3)',
                }"
              >
                <Icon :name="store.devMode ? 'lockOpen' : 'lock'" :size="15" />
              </span>
              <span class="flex-1 truncate text-[12px]" style="color: var(--c-text-3)">开发者模式</span>
              <span
                class="relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition"
                :style="{ background: store.devMode ? 'var(--c-accent)' : 'var(--c-border)' }"
                role="switch"
                :aria-checked="store.devMode"
                title="开关开发者模式（记忆 / 调试分页）"
                @click="setDevMode(!store.devMode)"
              >
                <span
                  class="inline-block h-4 w-4 transform rounded-full bg-white transition"
                  :style="{ marginLeft: store.devMode ? '18px' : '2px' }"
                />
              </span>
            </div>
            <template v-if="store.devMode">
              <button
                v-for="t in DEV_TABS"
                :key="t.key"
                class="set-nav-item mb-1 flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-[13px]"
                :class="{ 'is-on': activeTab === t.key }"
                @click="activeTab = t.key"
              >
                <span class="set-nav-ico flex h-6 w-6 shrink-0 items-center justify-center rounded-lg">
                  <Icon :name="t.icon" :size="15" />
                </span>
                <span class="truncate">{{ t.label }}</span>
              </button>
            </template>
          </div>
        </nav>

        <!-- 内容区：限宽居中，卡片不会被拉成一整条长表 -->
        <div class="min-w-0 flex-1 overflow-y-auto px-6 pb-8 pt-4 text-[14px]">
          <div class="mx-auto w-full max-w-[860px]">
            <!-- 模型与连接:选模型 + 配 API(原来两个分页,合并成一张页两块卡) -->
            <section v-show="activeTab === 'model'">
              <div class="hand-card mb-4">
                <div class="mb-4 flex items-center gap-2">
                  <span
                    class="flex h-6 w-6 items-center justify-center rounded-lg"
                    style="background: var(--chip-bg); color: var(--chip-ink)"
                  >
                    <Icon name="chip" :size="14" />
                  </span>
                  <h3
                    class="font-hand text-[19px] leading-none tracking-[-0.3px]"
                    style="color: var(--c-text)"
                  >
                    模型
                  </h3>
                </div>
                <!-- 模型 ID:自由输入,取值原样下发;留空 = 跟随后端默认 -->
                <input
                  v-model="modelDraft"
                  type="text"
                  spellcheck="false"
                  autocomplete="off"
                  class="w-full rounded-xl border-[1.5px] px-3 py-2.5 text-[13.5px] outline-none transition"
                  style="
                    border-color: var(--c-border);
                    background: var(--c-input);
                    color: var(--c-text);
                  "
                  placeholder="填写服务商提供的模型 ID，留空跟随后端默认"
                  @change="commitModel"
                />

                <div
                  class="mb-2 mt-5 flex items-center justify-between border-t pt-4"
                  style="border-color: var(--bg-subtle)"
                >
                  <span class="text-[14px]" style="color: var(--c-text)">温度</span>
                  <span
                    class="rounded-lg px-2 py-0.5 font-mono text-[12px]"
                    style="color: var(--chip-ink); background: var(--chip-bg)"
                  >
                    {{ Number(tempDraft).toFixed(2) }}
                  </span>
                </div>
                <input
                  v-model.number="tempDraft"
                  type="range"
                  min="0"
                  max="1.5"
                  step="0.05"
                  class="w-full"
                  style="accent-color: var(--color-accent)"
                  @input="onTemp"
                />
                <div class="mt-1 flex justify-between text-[11px]" style="color: var(--c-text-3)">
                  <span>严谨一致 0</span>
                  <span>0.7 推荐</span>
                  <span>发散创造 1.5</span>
                </div>
              </div>

              <!-- API 连接:与模型同页(选模型与配连接是同一件事的两半) -->
              <ConnectionSettings />
            </section>

            <!-- 用户:第一次见面问的三项(称呼 / 喜欢 / 讨厌),随时可改;
                 隐私并在这一页(数据存储位置与删除入口) -->
            <div v-show="activeTab === 'me'">
              <SettingsForm :group-ids="['profile']" />
              <div class="hand-card mb-4">
                <PrivacySettings />
              </div>
            </div>

            <!-- 角色卡:人格在这里建/选/改。嵌入时它自己不带页头与滚动条,
                 卡片化的留白由外层内容区给。 -->
            <section v-show="activeTab === 'cards'">
              <CardsView embedded />
            </section>

            <!-- 桌宠:今日记忆面板(显示/刷新/编辑 + 手动触发)在声明式表单之上 ——
                 它是内容面板不是键值设置,不进 spec.ts 的数据驱动表单 -->
            <PetDailyPanel v-show="activeTab === 'pet'" />

            <!-- 桌宠:三张手帐卡片(日常关怀 / 主动靠近 / 作息与防打扰;见 spec.ts 的 PET_CARDS) -->
            <SettingsForm v-show="activeTab === 'pet'" :cards="PET_CARDS" />

            <!-- 记忆:原独立视图,已并入设置(开发者分页)。
                 信息量大、只读,放在「调试」旁边成对出现;v-if 同 DebugSettings,
                 普通模式下不挂载,免得白请求 /api/memory/insight。 -->
            <section v-if="store.devMode" v-show="activeTab === 'memory'" class="hand-card">
              <MemoryPanel :active="activeTab === 'memory'" />
            </section>

            <!-- 调试:长期记忆排障视图(轻量;完整洞察见「记忆」分页)
                 开发者分页 —— v-if 而非只 v-show:普通模式下组件根本不挂载,
                 免得白白去请求 /api/memory/debug 这个排障接口。 -->
            <DebugSettings
              v-if="store.devMode"
              v-show="activeTab === 'debug'"
              @open-memory="activeTab = 'memory'"
            />

            <!-- 关于 -->
            <section v-show="activeTab === 'about'">
              <div class="hand-card mb-4 text-[12px] leading-relaxed">
                <div class="mb-1 flex items-center gap-1.5 font-medium" style="color: var(--c-text-2)">
                  <Icon name="chat" :size="15" />关于本应用
                </div>
                <p style="color: var(--c-text-3)">
                  心伴（XinBan）—— 桌宠陪伴演示应用。对话由 AI 经 OpenAI 兼容接口生成,
                  对话内容由 AI 生成,仅供参考,不构成医疗诊断。若遇紧急情况,请拨打
                  12356 心理援助热线。
                </p>

                <div
                  class="mt-4 border-t pt-4"
                  style="border-color: var(--bg-subtle)"
                >
                  <div
                    class="mb-1 flex items-center gap-1.5 font-medium"
                    style="color: var(--c-text-2)"
                  >
                    <Icon name="info" :size="15" />桌宠动画素材
                  </div>
                  <p style="color: var(--c-text-3)">
                    本应用的桌宠动画素材复刻自开源项目 VPet（虚拟桌宠模拟器）。VPet 源码以
                    Apache License 2.0 发布；其自带动画素材另有独立的版权声明。本项目仅在
                    课程 / 竞赛 / 演示中作非商业使用，素材的授权与再分发条件以原项目为准。
                  </p>
                  <button
                    type="button"
                    class="mt-2 break-all text-left underline underline-offset-2 transition hover:opacity-80"
                    style="color: var(--c-accent)"
                    @click="openVpet"
                  >
                    {{ VPET_URL }}
                  </button>
                </div>
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
