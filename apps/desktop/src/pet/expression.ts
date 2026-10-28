/**
 * 情绪驱动表情（见 `PLAN_NEXT.md` §4，裁定 ⑥「接受单侧偏置」）。
 *
 * ## 素材是现成的
 *
 * `pet/assets/vup/Default/` 下就是 VPet 原生的四态表情组 ——
 * `Happy/{1,2,3}`、`Nomal/{1,2,3}`、`PoorCondition/{1,2}`、`Ill/{1,2}`，共 10 个变体。
 * 此前只用了 **2 个**（`Happy/1` 待机、`Nomal/1` 对话后呼吸），这里把另外几组接上，
 * **不需要任何新素材**。
 *
 * ## 三条不可回退的约束
 *
 * 1. **只看基调，不看情绪判定。**
 *    输入是后端 `care.mode.dialogue_mode` 压出来的四个**表达方式**
 *    （`playful` / `normal` / `gentle` / `serious`），不是 `emotion` 原值。
 *    基调本身就体现在文案语气里、用户已经感知得到，所以拿它选表情不泄露新信息；
 *    而若按 `emotion` 一一映射，等于把内部判定翻译成动画给用户看 ——
 *    用户会学会解读成「系统判定我状态差」，对心理陪伴是净伤害
 *    （`PET_POLICY.md` 的「绝不向用户展示内部标签」）。
 *
 * 2. **单侧偏置：负向绝不换更惨的表情。**
 *    负向时只**降低活泼度**（退到安静的站立 / 呼吸），**不切到**
 *    `PoorCondition` 或 `Ill`。
 *
 * 3. **`PoorCondition` / `Ill` 整组不用。**
 *    它们语义偏「生病」，与「互动气氛」无关。留给将来的「桌宠**自身**状态」
 *    （它困了 / 该睡了）—— 那是在描述桌宠自己，不是在对用户做判定。
 */

import type { PetAction } from './behavior'

/** 后端 `care.mode.dialogue_mode` 的四个取值。 */
export type Tone = 'playful' | 'normal' | 'gentle' | 'serious'

/**
 * 基调 → 待机动作。
 *
 * 返回 `null` 表示「没有偏好，沿用原有的待机规则」（`normal` 与未知值）——
 * 这样在没有对话历史时，桌宠行为与接入本模块之前**完全一致**。
 */
export function idleActionForTone(tone: string | undefined | null): PetAction | null {
  switch (tone) {
    case 'playful':
      return 'idlePlayful' // Happy/2：更活泼的摇晃
    case 'gentle':
      return 'idleCalm' // Nomal/1：安静呼吸（负向时「降低活泼度」而非变惨）
    case 'serious':
      return 'idleSerious' // Nomal/2：更静止的站立
    default:
      return null
  }
}

/** 基调的中文短名。仅用于调试输出，不进入任何面向用户的文案。 */
export function toneLabel(tone: string | undefined | null): string {
  switch (tone) {
    case 'playful':
      return '轻快'
    case 'gentle':
      return '轻柔'
    case 'serious':
      return '沉静'
    default:
      return '平常'
  }
}
