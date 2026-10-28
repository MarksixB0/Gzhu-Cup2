/**
 * 打扰等级的前端口径（见 PLAN_NEXT.md §2）。
 *
 * 与后端 `src/gzhu_cup2/agent/pet/mode.py` 的 LEVELS 一一对应。**后端是唯一真相** ——
 * 等级由它算、门控由它执行；这里只做两件前端必须自己完成的事：
 *   1. 把「等级」翻译成**渲染层该不该做某事**（要不要主动靠近、要不要弹气泡、要不要播睡觉）；
 *   2. 提供「再按一次恢复」的切换语义（快捷键与托盘菜单都调它）。
 *
 * 刻意**不**在前端复刻后端的时间规则（夜间窗口、到期判定）—— 那些一律以后端返回的
 * `level` 为准，前端只消费结果，避免两处各算一份而漂移。
 */

/** 四档，索引越大越安静。顺序必须与后端 `mode.LEVELS` 一致。 */
export type Level = 'active' | 'quiet' | 'dnd' | 'asleep'

export const LEVELS: readonly Level[] = ['active', 'quiet', 'dnd', 'asleep']

/** `GET /api/pet/mode` 的响应里前端用得到的字段。 */
export interface PetModeState {
  level: string
  source?: string
  reason?: string
  until?: number | null
  default_level?: string
  manual_level?: string | null
  night_sleep?: boolean
  /** 空闲时「主动走到鼠标旁」的用户偏好(设置里可改,见 pet/runtime.py) */
  approach_enabled?: boolean
  approach_interval_min?: number
  choices?: string[]
  levels?: string[]
  preset_minutes?: number[]
  night_window?: number[]
}

function rankOf(level: string | undefined | null): number {
  const i = LEVELS.indexOf((level ?? '') as Level)
  // 未知值按**最响**处理：宁可多一次主动，也不因一份脏数据把桌宠永久变沉默
  return i < 0 ? 0 : i
}

/** `level` 是否「至少和 `floor` 一样安静」。 */
export function atLeast(level: string | undefined | null, floor: Level): boolean {
  return rankOf(level) >= rankOf(floor)
}

/** 要不要做「主动的」事：主动靠近、主动消息气泡、待机小动作。 */
export function mayBeProactive(level: string | undefined | null): boolean {
  return !atLeast(level, 'quiet')
}

/** 要不要出系统通知（dnd 及以上不出）。 */
export function mayNotify(level: string | undefined | null): boolean {
  return !atLeast(level, 'dnd')
}

/** 桌宠是否在睡觉（播放 Sleep、只保留「被唤醒」）。 */
export function isAsleep(level: string | undefined | null): boolean {
  return (level ?? '') === 'asleep'
}

/**
 * 「切换勿扰」的目标档。
 *
 * 规则：当前比 active 安静 → 回到 active；否则 → 进 dnd。
 * 刻意**不**在四档间循环 —— 一键循环到「休眠」很容易让用户搞不清"我按了几次"。
 */
export function nextDndLevel(level: string | undefined | null): Level {
  return rankOf(level) > 0 ? 'active' : 'dnd'
}

/** 等级的中文短名（只用于菜单/提示，不进入任何面向情绪判断的文案）。 */
export function levelLabel(level: string | undefined | null): string {
  switch (level) {
    case 'quiet':
      return '安静'
    case 'dnd':
      return '勿扰'
    case 'asleep':
      return '休眠'
    default:
      return '全开'
  }
}
