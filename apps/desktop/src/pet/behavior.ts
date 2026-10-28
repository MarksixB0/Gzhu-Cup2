/**
 * 桌宠行为表：把「发生什么事」映射到「播哪段素材」。
 *
 * 关键：VPet 的动画是 **A(进入) → B(循环) → C(退出)** 三段式
 * （`VPet-Simulator.Core/Graph/GraphInfo.cs` 的 `AnimatType`）。
 * 之前把三段当一段播，导致「摸头只闪一下」「待机静止」等 bug。本表改为：
 *   - 单段循环动作（待机/启动）只给 `a` + `loop`；
 *   - 三段式动作给 `a/b/c`，运行期按 A → B×N → C 拼接成一条序列播放。
 *
 * 变体名是**精确键**（对照 `pet/assets/vup/manifest.json` 的 `states[state]`），
 * 不再用子串模糊匹配 —— 模糊匹配会把 `'A'` 命中 `Happy/A`、`'Nomal'` 命中 `meowlook/Nomal`。
 *
 * 这里只有**编排**，没有人格文本——桌宠说什么由后端角色卡决定（见 PET_POLICY.md）。
 * 本文件不出现任何角色专名。
 */

import { buildFrames, type PetFrame } from './player'
import type { PetManifest } from './manifest'

/** 桌宠能触发的动作。 */
export type PetAction =
  | 'startup'
  | 'idle'
  | 'idleCalm'
  /** 情绪基调 playful 时的待机（更活泼的 Happy 变体，见 pet/expression.ts） */
  | 'idlePlayful'
  /** 情绪基调 serious 时的待机（更静止的站立，见 pet/expression.ts） */
  | 'idleSerious'
  | 'say'
  | 'think'
  | 'touchHead'
  | 'touchBody'
  | 'pinch'
  | 'raiseHold'
  | 'raiseDrop'
  | 'walkLeft'
  | 'walkRight'
  | 'sleep'

export interface ActionSpec {
  /** 素材状态目录名（与 manifest.states 的键一致） */
  state: string
  /** A 段（进入）精确变体名；缺省表示无进入段 */
  a?: string
  /** B 段（循环）精确变体名；缺省表示无循环段 */
  b?: string
  /** C 段（退出）精确变体名；缺省表示无退出段 */
  c?: string
  /** B 段重复次数（默认 1）。三段式动作靠它把循环播够，再收尾 */
  repeat?: number
  /** 单段动作是否循环（仅当无 b 段时有意义，如待机/启动） */
  loop?: boolean
  /**
   * 「进入后保持」：A 段播一次，然后**只循环 B 段**、并且**不播 C 段**。
   *
   * 为什么需要它：睡觉这类"姿势型"动作没有自然终点 —— 三段式一次性播完(C=起床)，
   * 再被 `goIdle()` 重新触发，就会变成「盖被→睡觉→起床→盖被→…」的无限循环。
   * 标了 `hold` 的动作由**外部状态变化**（等级离开 asleep）来结束，而不是自己播完。
   */
  hold?: boolean
}

export const ACTIONS: Record<PetAction, ActionSpec> = {
  // 启动：单段播一次
  startup: { state: 'StartUP', a: 'Nomal', loop: false },
  // 默认待机：开心的左右摇晃（Happy 组，站立带笑、有轻微摆幅）
  idle: { state: 'Default', a: 'Happy/1', loop: true },
  // 对话结束后待机：安静的站立呼吸（摆幅极小）
  idleCalm: { state: 'Default', a: 'Nomal/1', loop: true },
  // 情绪基调 playful：比默认待机更活泼的摇晃（Happy 组第 2 变体）。
  // 变体名与基调的对应关系集中在 pet/expression.ts，这里只做素材编排。
  idlePlayful: { state: 'Default', a: 'Happy/2', loop: true },
  // 情绪基调 serious：更静止的站立（Nomal 组第 2 变体）。
  // 注意它**仍在 Nomal 组内** —— 负向只「降低活泼度」，绝不换到
  // PoorCondition / Ill（裁定 ⑥的单侧偏置）。
  idleSerious: { state: 'Default', a: 'Nomal/2', loop: true },
  // 说话：A 进入 → B 循环 3 次 → C 退出
  say: { state: 'Say', a: 'Self/A', b: 'Self/B_1', c: 'Self/C', repeat: 3 },
  // 思考：A → B 循环 3 次 → C
  think: { state: 'Think', a: 'Nomal/A', b: 'Nomal/B', c: 'Nomal/C', repeat: 3 },
  // 摸头：完整三段（此前只播 A 的 2 帧 → 闪一下就没）
  touchHead: { state: 'Touch_Head', a: 'A_Nomal', b: 'B_Nomal', c: 'C_Nomal', repeat: 2 },
  // 摸身体：完整三段（Happy 组，tb1）
  touchBody: { state: 'Touch_Body', a: 'A_Happy/tb1', b: 'B_Happy/tb1', c: 'C_Happy/tb1', repeat: 2 },
  // 捏/拖拽（头部区域）：A(1帧) → B 循环 → C(放下，21帧)
  pinch: { state: 'Pinch', a: 'Nomal/A', b: 'Nomal/B', c: 'Nomal/C', repeat: 2 },
  // 抓衣服吊起来·悬空保持（非头部区域拖动）：被提起后循环「提着眨眼」，直到松手
  raiseHold: { state: 'Raise', a: 'Raised_Static/B_Nomal', loop: true },
  // 抓衣服吊起来·放下（松手时）：放下动画，播完回待机
  raiseDrop: { state: 'Raise', a: 'Raised_Static/C_Nomal' },
  // 走路（主动靠近）：B 段循环 = 走路循环；窗口移动由 App.vue 驱动（走到位才停）
  walkLeft: { state: 'MOVE/walk.left', a: 'B_Nomal', loop: true },
  walkRight: { state: 'MOVE/walk.right', a: 'B_Nomal', loop: true },
  // 睡觉：躺下盖被(A)一次 → **保持睡眠循环(B)**，不播起床(C)。
  // 由等级离开「休眠」来唤醒(见 App.vue::goIdle)，而不是自己播完再被重放
  // ——否则会「盖被→睡觉→盖被」无限循环。
  sleep: { state: 'Sleep', a: 'A_Nomal', b: 'B_Nomal', c: 'C_Nomal', hold: true },
}

/**
 * 待机随机小动作：空闲时随机播一个，让桌宠「活起来」（播完回呼吸待机）。
 * 全部取自 `IDEL` 状态（VPet 原生的待机小动作集），当前这些素材此前完全未用。
 * 变体名对照 `manifest.states['IDEL']`；三段式的给 a/b/c，单段的只给 a。
 *
 * 取舍记录：原「喵叫」三连（`Meow/Nomal/1..3`，0.36MB）已**移除** —— 它是纯 flavor，
 * 且 PNG 素材不压缩、1:1 变成安装包体积（见 `pet/sync_vup_assets.py` 的按引用剪枝）。
 * 剩余动画仍覆盖单段与三段两类形态。
 */
export const IDLE_MICRO_ACTIONS: ActionSpec[] = [
  { state: 'IDEL', a: 'yawning/Nomal' },        // 打哈欠
  { state: 'IDEL', a: 'happy_like520' },        // 比心
  { state: 'IDEL', a: 'meowlook/Nomal' },       // 喵看
  { state: 'IDEL', a: 'amusement_B' },          // 自娱
  { state: 'IDEL', a: 'Bubbles/A', b: 'Bubbles/B', c: 'Bubbles/C' },            // 吹泡泡
  { state: 'IDEL', a: 'Squat/A_Nomal', b: 'Squat/B_Nomal/1', c: 'Squat/C_Nomal' }, // 蹲下
  { state: 'IDEL', a: 'Tennis/Nomal/A', b: 'Tennis/Nomal/B', c: 'Tennis/Nomal/C' }, // 玩球
  { state: 'IDEL', a: 'aside/Nomal/A', b: 'aside/Nomal/B', c: 'aside/Nomal/C' },   // 张望
  { state: 'IDEL', a: 'Boring/A_Nomal', b: 'Boring/B_Nomal', c: 'Boring/C_Nomal' }, // 无聊
]

export interface ResolvedAction {
  /** 触发来源：具名动作或 'idleMicro' */
  action: string
  state: string
  /** 实际用到的变体（拼接用 '+' 连接），便于调试 */
  variant: string
  frames: PetFrame[]
  loop: boolean
  /** 循环的起点帧下标（`hold` 动作用：0..loopFrom-1 只播一次，其后的无限循环） */
  loopFrom?: number
}

/** 核心：把一条 ActionSpec 解析成完整帧序列（A → B×repeat → C）。 */
export function resolveSpec(
  manifest: PetManifest,
  spec: ActionSpec,
  repeatOverride?: number,
): ResolvedAction | null {
  const frames: PetFrame[] = []
  const used: string[] = []

  const push = (variant?: string, times = 1): void => {
    if (!variant) return
    const seg = buildFrames(manifest, spec.state, variant)
    if (seg.length === 0) return
    for (let i = 0; i < times; i += 1) frames.push(...seg)
    used.push(variant)
  }

  const repeat = repeatOverride != null && repeatOverride > 0
    ? Math.floor(repeatOverride)
    : (spec.repeat ?? 1)

  // hold：A 播一次 → 只循环 B → 永不播 C（姿势型动作不自行结束，靠外部状态变化离开）
  push(spec.a)
  const loopFrom = frames.length      // B 段起点：hold 时无限循环从这里重来，A 不会重播
  push(spec.b, repeat)
  // B 段**真的有帧**才算 hold —— 否则 loopFrom 会等于帧总数（非法），
  // 退化成"只循环 A(盖被)"，比一次性播完更难看。
  const hold = !!(spec.hold && spec.b) && frames.length > loopFrom
  if (!hold) push(spec.c)

  if (frames.length === 0) return null
  return {
    action: 'spec',
    state: spec.state,
    variant: used.join('+'),
    frames,
    // 有 B 段时是「一次性播完」（A→B×N→C），只有单段动作或 hold 动作才可能循环
    loop: hold || (!spec.b && !!spec.loop),
    loopFrom: hold ? loopFrom : 0,
  }
}

/**
 * 把动作解析成一条完整帧序列（A → B×repeat → C）。返回 null 表示素材缺失。
 *
 * 缺失的段自动跳过（如某动作只有 A/B 没有 C）；全部缺失才返回 null。
 *
 * `repeatOverride` 用于覆盖 B 段的循环次数：说话动画按回复长度动态决定循环几次
 * （短句少循环 → 动画短；长句多循环 → 动画长），避免"回复一句短话却张嘴很久"。
 */
export function resolveAction(
  manifest: PetManifest,
  action: PetAction,
  repeatOverride?: number,
): ResolvedAction | null {
  const resolved = resolveSpec(manifest, ACTIONS[action], repeatOverride)
  if (resolved) resolved.action = action
  return resolved
}

/**
 * 随机挑一个待机小动作并解析。素材缺失的自动跳过；全部不可用返回 null。
 * `rand` 可注入（测试用），默认 Math.random。
 */
export function resolveIdleMicro(
  manifest: PetManifest,
  rand: () => number = Math.random,
): ResolvedAction | null {
  if (IDLE_MICRO_ACTIONS.length === 0) return null
  const start = Math.floor(rand() * IDLE_MICRO_ACTIONS.length)
  for (let i = 0; i < IDLE_MICRO_ACTIONS.length; i += 1) {
    const spec = IDLE_MICRO_ACTIONS[(start + i) % IDLE_MICRO_ACTIONS.length]
    const resolved = resolveSpec(manifest, spec)
    if (resolved) {
      resolved.action = 'idleMicro'
      return resolved
    }
  }
  return null
}

/**
 * 交互判定：命中头/身体/其它区域。
 * 坐标是相对桌宠画布的归一化比例（0–1），阈值来自 VPet `vup.lps` 的锚点定义
 * （touchhead: px159 py16 sw189 sh178；touchbody: px166 py206 sw163 sh136，画布 500×500）。
 */
export type HitZone = 'head' | 'body' | 'other'

const HEAD_BOX = { x0: 0.32, x1: 0.70, y0: 0.03, y1: 0.39 }
const BODY_BOX = { x0: 0.33, x1: 0.66, y0: 0.41, y1: 0.68 }

export function hitTest(nx: number, ny: number): HitZone {
  const inBox = (b: typeof HEAD_BOX) => nx >= b.x0 && nx <= b.x1 && ny >= b.y0 && ny <= b.y1
  if (inBox(HEAD_BOX)) return 'head'
  if (inBox(BODY_BOX)) return 'body'
  return 'other'
}
