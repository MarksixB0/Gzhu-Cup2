/**
 * 设置项声明（见 `PLAN_NEXT.md` §7）。
 *
 * 把**设置项本身**变成数据：键、控件类型、分组、是否只读、说明文字。
 * 表单由它渲染，保存由统一的 `patchSettings` 做。
 * **新增一个设置项 = 在这里加一条**；后端只需在 `routes_settings._APPLIERS` 加一行。
 *
 * ## 与后端的唯一契约
 *
 * `key` 必须与后端点号命名空间**逐字一致**（`care.enabled`）。
 * 拼错不会静默失效 —— 后端对未知键返回 400 并列出可用键，界面会直接报出来。
 *
 * ## 关于 `help` / `hint`
 *
 * 字段保留，但**界面不再呈现**：设置页只显示标签与控件，不留解释文案。
 * 某项确实需要解释时，写进代码注释或文档，不要再往这里塞。
 */

/** 控件类型 */
export type SettingKind = 'switch' | 'text' | 'textarea' | 'select' | 'readonly'

export interface SettingOption {
  /** 传给后端的值。允许对象：如临时覆盖要一次带上「档位 + 时长」 */
  value: unknown
  label: string
  /** 选项级说明（如「到点自动恢复」） */
  hint?: string
}

export interface SettingSpec {
  /** 与后端 `/api/settings` 完全一致的键，如 `care.enabled` */
  key: string
  kind: SettingKind
  label: string
  /** 为什么要这个设置、改了会怎样 */
  help?: string
  /** `select` 用 */
  options?: SettingOption[]
  /** 输入类控件的占位提示（纯展示，不参与保存） */
  placeholder?: string
  /** `textarea` 的行数，默认 3 */
  rows?: number
}

export interface SettingGroup {
  id: string
  label: string
  help?: string
  items: SettingSpec[]
}

/**
 * 分组顺序与后端 `routes_settings.snapshot()` 的六域一致 ——
 * 这样前端加一个分区不必动后端，后端加一域前端也不必重排。
 */
export const SETTING_GROUPS: SettingGroup[] = [
  // 「用户」放最前:它就是第一次见面问的三件事(称呼 / 喜欢 / 讨厌)。
  // 三个 key 与后端 snapshot() 的 profile 域逐字一致(core/user_profile.py)。
  {
    id: 'profile',
    label: '关于你',
    items: [
      { key: 'profile.display_name', kind: 'text', label: '想让桌宠怎么称呼你？' },
      { key: 'profile.likes', kind: 'textarea', label: '偏好' },
      { key: 'profile.dislikes', kind: 'textarea', label: '不想让桌宠提及' },
    ],
  },
  {
    id: 'care',
    label: '主动关怀',
    items: [
      {
        key: 'care.enabled',
        kind: 'switch',
        label: '允许桌宠主动找我',
      },
    ],
  },
  {
    id: 'pet',
    label: '桌宠',
    items: [
      {
        key: 'pet.daily_enabled',
        kind: 'switch',
        label: '生成每日生活',
      },
      {
        key: 'pet.approach_enabled',
        kind: 'switch',
        label: '空闲时主动靠近光标',
      },
      {
        key: 'pet.approach_interval_min',
        kind: 'select',
        label: '靠近间隔',
        options: [
          { value: 5, label: '5 分钟' },
          { value: 10, label: '10 分钟' },
          { value: 15, label: '15 分钟' },
          { value: 30, label: '30 分钟' },
          { value: 60, label: '1 小时' },
        ],
      },
    ],
  },
  {
    id: 'mode',
    label: '打扰等级',
    items: [
      {
        key: 'mode.default_level',
        kind: 'select',
        label: '默认等级',
        options: [
          { value: 'active', label: '全开' },
          { value: 'quiet', label: '安静' },
          { value: 'dnd', label: '勿扰' },
        ],
      },
      {
        key: 'mode.night_sleep',
        kind: 'switch',
        label: '夜间自动休眠',
      },
      {
        key: 'mode.manual_level',
        kind: 'select',
        label: '临时覆盖',
        options: [
          { value: '', label: '不覆盖（跟随默认与夜间规则）' },
          { value: { level: 'dnd', minutes: 60 }, label: '勿扰 1 小时' },
          { value: { level: 'dnd', minutes: 180 }, label: '勿扰 3 小时' },
          { value: { level: 'quiet', minutes: 60 }, label: '安静 1 小时' },
        ],
      },
    ],
  },
]

/** 按 `key` 取出设置项（用于渲染与取值）。 */
export function findSpec(key: string): SettingSpec | undefined {
  for (const group of SETTING_GROUPS) {
    const hit = group.items.find((it) => it.key === key)
    if (hit) return hit
  }
  return undefined
}

/** 把 `care.enabled` 这类点号键从快照里取值。 */
export function readByPath(snapshot: Record<string, unknown> | null, key: string): unknown {
  if (!snapshot) return undefined
  let cur: unknown = snapshot
  for (const part of key.split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[part]
  }
  return cur
}

/**
 * 展示用的「卡片」分组 —— 与后端六域是**另一套视角**。
 *
 * 分组（`SETTING_GROUPS`）面向落库，卡片面向阅读：把散落的表单项收进
 * 「日常关怀 / 主动靠近 / 作息与防打扰」三张手帐卡片。
 * 开场白不在这里：它由**角色卡**的 first_mes 决定（人格唯一来源），
 * 设置页不再提供第二入口，避免两处可改却互相同步不清。
 * 卡片只声明 `key` 的顺序，控件类型仍由 `SettingSpec` 决定 ——
 * 因此调整页面布局不需要动后端，也不需要改表单组件。
 */
export interface SettingCard {
  id: string
  title: string
  /** 卡片标题前的手帐小图标（Icon 名） */
  icon?: string
  /** 卡片内的设置项，顺序即渲染顺序 */
  keys: string[]
}

/** 「桌宠」分页的三张卡片（keys 均来自 care / pet / mode 三组）。 */
export const PET_CARDS: SettingCard[] = [
  {
    id: 'daily',
    title: '日常关怀',
    icon: 'paw',
    keys: ['care.enabled', 'pet.daily_enabled'],
  },
  {
    id: 'approach',
    title: '主动靠近',
    icon: 'paw',
    keys: ['pet.approach_enabled', 'pet.approach_interval_min'],
  },
  {
    id: 'rest',
    title: '作息与防打扰',
    icon: 'moon',
    keys: ['mode.default_level', 'mode.night_sleep', 'mode.manual_level'],
  },
]
