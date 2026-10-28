<script setup lang="ts">
/**
 * 由 `SettingSpec` 渲染的设置表单（见 `settings/spec.ts` 与 `PLAN_NEXT.md` §7）。
 *
 * 本组件**不认识任何具体设置项**：控件类型、取值、保存全部来自声明。
 * 保存统一走 `useSettings().save()` —— 因此新增一个设置项不需要改这个文件。
 *
 * 两种排布，数据都来自声明：
 *   - 传 `cards`：按卡片渲染（一张卡 = 一组相关设置），用于「桌宠」分页；
 *   - 不传：按 `SETTING_GROUPS` 的分组渲染，每组一张卡（如「用户」分页）。
 */
import { computed, onMounted } from 'vue'

import {
  SETTING_GROUPS,
  findSpec,
  type SettingCard,
  type SettingSpec,
} from '../spec'
import { useSettings } from '../useSettings'
import Icon from '../../../shell/components/Icon.vue'

const props = defineProps<{ groupIds?: string[]; cards?: SettingCard[] }>()

/** 归一化后的渲染单元：不论是卡片还是分组，都变成「标题 + 若干设置项」。 */
interface RenderCard {
  id: string
  title: string
  icon?: string
  items: SettingSpec[]
}

const groups = computed(() =>
  props.groupIds?.length
    ? SETTING_GROUPS.filter((g) => props.groupIds?.includes(g.id))
    : SETTING_GROUPS,
)

const renderCards = computed<RenderCard[]>(() => {
  if (props.cards?.length) {
    return props.cards.map((c) => ({
      id: c.id,
      title: c.title,
      icon: c.icon,
      // 卡片的 key 若在 spec 里找不到就跳过，不渲染空行
      items: c.keys
        .map(findSpec)
        .filter((s): s is SettingSpec => s !== undefined),
    }))
  }
  return groups.value.map((g) => ({ id: g.id, title: g.label, items: g.items }))
})

const { snapshot, loading, saving, notice, load, save, valueOf } = useSettings()

/** 当前值：把后端快照按点号键取出来 */
function val(spec: SettingSpec): unknown {
  return valueOf(spec.key)
}

/** option 的值 → 用于和 `<select>` 比较的字符串 */
function encode(v: unknown): string {
  return typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v ?? '')
}

/**
 * 下拉框的选中值。
 *
 * 后端还没回来、或返回的值不在选项里（如临时覆盖带上了额外字段）时，
 * 直接绑原值会让下拉显示成空白 —— 所以匹配不上就回退到第一个选项。
 */
function selectValue(spec: SettingSpec): string {
  const cur = encode(val(spec))
  const opts = spec.options ?? []
  if (opts.some((o) => encode(o.value) === cur)) return cur
  return opts.length ? encode(opts[0].value) : ''
}

/**
 * 开关：**立即提交**。
 * 开关没有"保存"按钮，如果延后提交，用户会以为已经生效了 —— 立即提交 + 失败提示
 * 是唯一不会造成误判的做法。
 */
function toggle(spec: SettingSpec, next: boolean): void {
  void save({ [spec.key]: next })
}

/** 文本 / 下拉：值确定后提交（textarea 用 change，即失焦或 ⌘/Ctrl+Enter） */
function commit(spec: SettingSpec, event: Event): void {
  const target = event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
  const raw = target.value
  // select 的 option 值可能是对象（如临时覆盖要带时长）—— 用 data-value 携带 JSON
  const encoded = (target as HTMLSelectElement).selectedOptions?.[0]?.dataset?.value
  const value = encoded ? JSON.parse(encoded) : raw
  void save({ [spec.key]: value })
}

onMounted(load)
</script>

<template>
  <section>
    <!-- 顶部只留刷新：卡片自带标题，这里再写一个「设置」就重复了 -->
    <div class="mb-3 flex items-center justify-end">
      <button
        class="flex items-center gap-1 text-[11px] ui-hover"
        style="color: var(--c-text-3)"
        :disabled="loading"
        @click="load"
      >
        <Icon name="refresh" :size="12" :class="{ 'animate-spin': loading }" />
        刷新
      </button>
    </div>

    <div
      v-if="notice"
      class="mb-3 flex items-start gap-2 rounded-xl border px-3 py-2 text-[12px] leading-relaxed"
      :style="{
        borderColor: notice.kind === 'ok' ? 'var(--c-border-strong)' : '#e08b7f',
        color: notice.kind === 'ok' ? 'var(--c-text-2)' : '#c2543f',
        background: 'var(--c-input)',
      }"
    >
      <Icon :name="notice.kind === 'ok' ? 'check' : 'alert'" :size="14" class="mt-0.5 shrink-0" />
      <span>{{ notice.text }}</span>
    </div>

    <!-- 卡片：白卡纸 + 手帐小图标标题 -->
    <div v-for="card in renderCards" :key="card.id" class="hand-card mb-4">
      <div class="mb-1 flex items-center gap-2">
        <span
          v-if="card.icon"
          class="flex h-6 w-6 items-center justify-center rounded-lg"
          style="background: var(--chip-bg); color: var(--chip-ink)"
        >
          <Icon :name="card.icon" :size="14" />
        </span>
        <h3 class="font-hand text-[19px] leading-none tracking-[-0.3px]" style="color: var(--c-text)">
          {{ card.title }}
        </h3>
      </div>

      <div
        v-for="spec in card.items"
        :key="spec.key"
        class="hand-row"
        :class="{ 'is-stack': spec.kind === 'textarea' }"
      >
        <!-- 只读：运行参数与连接摘要，明确标出来免得用户找开关 -->
        <template v-if="spec.kind === 'readonly'">
          <span class="text-[14px]" style="color: var(--c-text)">{{ spec.label }}</span>
          <span class="truncate font-mono text-[12px]" style="color: var(--c-text-2)">
            {{ val(spec) || '—' }}
          </span>
        </template>

        <!-- 开关：整行可点，立即提交 -->
        <template v-else-if="spec.kind === 'switch'">
          <span class="text-[14px]" style="color: var(--c-text)">{{ spec.label }}</span>
          <span
            class="tog"
            :class="{ 'is-on': !!val(spec) }"
            role="switch"
            :aria-checked="!!val(spec)"
            :aria-label="spec.label"
            @click="toggle(spec, !val(spec))"
          />
        </template>

        <!-- 下拉：标签在左，控件在右 -->
        <template v-else-if="spec.kind === 'select'">
          <span class="text-[14px]" style="color: var(--c-text)">{{ spec.label }}</span>
          <span class="sel-wrap">
            <select
              class="sel"
              :value="selectValue(spec)"
              :disabled="saving"
              @change="commit(spec, $event)"
            >
              <option
                v-for="opt in spec.options"
                :key="opt.label"
                :value="typeof opt.value === 'object' ? JSON.stringify(opt.value) : opt.value"
                :data-value="typeof opt.value === 'object' ? JSON.stringify(opt.value) : undefined"
              >
                {{ opt.label }}
              </option>
            </select>
            <span class="sel-arrow"><Icon name="chevronDown" :size="14" /></span>
          </span>
        </template>

        <!-- 多行文本（开场白）：标签在上、框在下，手写体 -->
        <template v-else-if="spec.kind === 'textarea'">
          <div class="mb-2 text-[14px]" style="color: var(--c-text)">{{ spec.label }}</div>
          <textarea
            :rows="spec.rows ?? 3"
            class="txt-hand"
            :value="String(val(spec) ?? '')"
            :placeholder="spec.placeholder"
            :disabled="saving"
            @change="commit(spec, $event)"
          />
        </template>

        <!-- 单行文本 -->
        <template v-else>
          <span class="text-[14px]" style="color: var(--c-text)">{{ spec.label }}</span>
          <input
            class="sel"
            style="flex: 0 1 250px; padding-right: 12px; cursor: text"
            :value="String(val(spec) ?? '')"
            :placeholder="spec.placeholder"
            :disabled="saving"
            @change="commit(spec, $event)"
          />
        </template>
      </div>
    </div>
  </section>
</template>
