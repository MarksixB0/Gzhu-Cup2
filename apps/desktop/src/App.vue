<script setup lang="ts">
/**
 * 桌宠窗口的渲染逻辑。
 *
 * 分工：
 *   - 演什么（帧序列）→ `pet/player.ts` + `pet/behavior.ts`，素材来自 pet/assets；
 *   - 说什么（人格与文案）→ 后端角色卡，经既有 API 取回；本文件不出现任何人格文本。
 *
 * 气泡的三条生命周期规则：
 *   1. 开场白只在启动时出现（约 30s 后自动收起），用户一开口就消失且不再回来；
 *   2. 说完话后气泡会自动收起，桌宠回到待机动画；
 *   3. **任何气泡显示期间，主动消息都不插入** —— 既不打断裂对话，也不覆盖开场白。
 *
 * 进入功能页：鼠标悬停在桌宠上时右上角浮现三枚图标（设置 / 对话 / 日历），
 * 点一下直接打开主窗口并停在对应页。
 *
 * 对话输入框：鼠标**悬停**到桌宠上即唤起（10 秒无输入自动收起，有内容则保持）；
 * 左键仍是拖拽/摸头，右键不触发任何桌宠状态 —— 显隐、勿扰、退出等系统级动作
 * 统一收口在**托盘右键菜单**（见 main.cjs 的 createTray）。
 *
 * 打扰等级：等级由**后端**算（见 src/gzhu_cup2/agent/pet/mode.py 与 docs/PLAN_NEXT.md §2），
 * 本文件只消费结果 —— 决定要不要主动靠近、要不要弹主动消息、要不要播睡觉。
 * 前端**不复刻**夜间窗口与到期判定，那些一律以后端返回为准。
 *
 * 情绪表情：只看**对话基调**（后端 care.mode.dialogue_mode 的四档），不看情绪判定 ——
 * 详见 pet/expression.ts 的三条约束（尤其"负向绝不换更惨的表情"）。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import { BackendClient, type UserProfile } from './api'
import { hitTest, resolveAction, resolveIdleMicro, type HitZone, type PetAction, type ResolvedAction } from './pet/behavior'
import { idleActionForTone, toneLabel } from './pet/expression'
import { isAsleep, levelLabel, mayBeProactive, mayNotify, nextDndLevel, type PetModeState } from './pet/mode'
import type { PetManifest } from './pet/manifest'
import { PngSequencePlayer, preloadFrames } from './pet/player'

const MANIFEST_URL = 'pet-asset://vup/manifest.json'
/** 默认待机：开心的左右摇晃 */
const IDLE: PetAction = 'idle'
/** 对话结束后待机：安静的站立呼吸 */
const IDLE_CALM: PetAction = 'idleCalm'
/**
 * 当前「回到待机」用哪个动作：
 *   默认 = 开心摇晃；桌宠刚说完话 = 安静呼吸（对话完后没进一步对话的待机）。
 * 用户再次交互 / 待机小动作时切回开心摇晃。
 */
let idleTarget: PetAction = IDLE
/** 主动消息轮询间隔（主动关怀由后端决定何时该说，前端只负责拉取） */
const POLL_INTERVAL = 15_000
/**
 * 判定「拖拽」还是「点击」的总位移阈值（px）。
 * 取 8 而非更小值：鼠标按下时的手抖常有 3–5px，阈值太小会把正常点击误判成拖拽，
 * 于是 `pointerup` 走进拖拽分支、永远不触发摸头 —— 表现为「点了没反应」。
 */
const DRAG_THRESHOLD = 8
/** 对话/主动消息说完后，气泡自动收起的延时 */
const BUBBLE_TTL_MS = 12_000
/**
 * 空闲「主动靠近」的默认间隔（分钟）。实际值以后端返回的用户偏好为准
 * （设置 → 桌宠 → 主动靠近，见 pet/runtime.py 的 approach_*）。
 */
const APPROACH_INTERVAL_MIN_DEFAULT = 10
/** 出错气泡的收起延时（留久一点，方便看清原因） */
const ERROR_TTL_MS = 8_000
/**
 * 开场白的展示时长。
 *
 * 开场白**不设成永久**：它要占住气泡（占住期间主动消息一律不插入），
 * 永久占位会让主动关怀永远发不出来（用户不开口就永远收不到）。
 * 30s 足够看清，之后收起回待机，主动消息即可正常展示。
 */
const GREETING_TTL_MS = 30_000
/**
 * 开场白重试参数。Electron 是「先开窗口、再拉起后端」，首次请求多半扑空
 * （后端还在启动），所以必须重试；最多等 10 × 1.5s ≈ 15s。
 */
const GREETING_MAX_ATTEMPTS = 10
const GREETING_RETRY_MS = 1_500
/**
 * 首启引导收尾提示的展示时长。
 *
 * 明显短于 `BUBBLE_TTL_MS`（12s）：收尾语只是一句"记下了"，后面还要接着放
 * 角色卡开场白 —— 让用户等 12 秒才看到角色说的第一句话，没有必要。
 */
const ONBOARD_DONE_TTL_MS = 3_000

const frameUrl = ref('')
const bubble = ref<string | null>(null)
const bubbleEl = ref<HTMLElement | null>(null)
const petEl = ref<HTMLImageElement | null>(null)
/** 是否正在拖拽（响应式，供模板隐藏气泡/输入框，只留动画） */
const isDragging = ref(false)
const input = ref('')
const busy = ref(false)
/** 悬停桌宠时右上角的三枚快捷入口是否浮现 */
const hoverActions = ref(false)
/** 输入框是否显示：常态隐藏（不挡屏幕），悬停桌宠时唤起 */
const showComposer = ref(false)
const inputEl = ref<HTMLInputElement | null>(null)

/* ---------------------------------------------------------------- 首启引导
 * 第一次见面问三件事:称呼 / 喜欢的事物 / 讨厌的事物(用户随后可在
 * 「设置 → 用户」里改)。三条不可回退的约束:
 *
 *   1. **不走 LLM** —— 首次启动时 API Key 很可能还没配,走 /api/chat 会 503;
 *      这三问是产品引导(静态文案),不是 care 的"关怀消息",不受"必过 LLM"约束。
 *   2. **可跳过,且跳过就真的不再问** —— 状态落在后端(`core/user_profile.py`),
 *      前端只内存的话,重启就又问一遍。
 *   3. **回答只用于称呼与话题** —— 不进 ticks / trend / guard,用户自述不是风险信号。
 */
const ONBOARD_QUESTIONS = [
  '你好，初次见面。我该怎么称呼你？',
  '能跟我说说你喜欢的东西吗？',
  '不好意思，你有什么讨厌的东西吗？',
] as const
const ONBOARD_FIELDS = ['display_name', 'likes', 'dislikes'] as const
/** 与后端的限额一致(`core/user_profile.py`);超长时本地先拦,免得多跑一次请求 */
const ONBOARD_MAX_LEN = [20, 300, 300] as const

/** 当前问到第几题;null = 不在引导中 */
const onboardStep = ref<number | null>(null)

/** 是否正在首启引导中(模板/气泡行为都看它)。 */
function onboardingActive(): boolean {
  return onboardStep.value !== null
}

/**
 * 启动时判断要不要引导。返回 true 表示**已接管开场**(不再放角色卡开场白),
 * 避免两句话同时抢气泡。
 *
 * **为什么要自己重试**:Electron 先开窗口、再拉起后端,首次请求几乎必然扑空。
 * 与 `showGreeting` 同一节奏重试(最多 10 × 1.5s),否则"第一次启动"这一瞬
 * 错过就**永远不会再问**(`needed` 只在启动时看一次)。
 */
async function beginOnboardingIfNeeded(attempt = 0): Promise<boolean> {
  let profile: UserProfile | null = null
  try {
    profile = await backend.userProfile()
  } catch {
    profile = null // 后端还没起:走下面的重试
  }
  if (profile === null) {
    if (attempt < GREETING_MAX_ATTEMPTS) {
      await new Promise((resolve) => setTimeout(resolve, GREETING_RETRY_MS))
      return beginOnboardingIfNeeded(attempt + 1)
    }
    return false // 后端始终没起:交给开场白那条路径,桌宠其余部分照常
  }
  if (!profile.onboarding?.needed) return false

  // 已经填过的项不再问:上次可能只答了一半就关了应用。
  const answered = [
    Boolean(profile.display_name?.trim()),
    Boolean(profile.likes?.trim()),
    Boolean(profile.dislikes?.trim()),
  ]
  const startAt = answered.findIndex((filled) => !filled)
  if (startAt < 0) {
    // 三项都已有值,只是没走完流程 —— 直接收尾,别再用"你叫什么"烦人
    void backend.onboarding('complete')
    return false
  }

  void backend.onboarding('seen') // 计入次数上限:最多问 3 次
  onboardStep.value = startAt
  focusComposer() // 直接露出输入框,否则用户不知道该在哪回答
  clearComposerTimer() // 引导期间输入框常驻:10s 收起会让问题变成"没法答"
  say(ONBOARD_QUESTIONS[startAt])
  cancelBubbleHide() // 引导期间气泡不自动收起(问题被收走就答不成了)
  return true
}

/** 提交一题;`text` 为空 = 这题不答,直接进入下一题(不算跳过整个引导)。 */
async function answerOnboarding(text: string): Promise<void> {
  const step = onboardStep.value
  if (step === null) return
  if (text) {
    const limit = ONBOARD_MAX_LEN[step]
    if (text.length > limit) {
      say(`这条有点长（最多 ${limit} 字），可以短一点再说一次。`)
      cancelBubbleHide()
      return
    }
    const ok = await backend.setProfileField(ONBOARD_FIELDS[step], text)
    if (!ok) {
      say('这句没存下来，稍后可以在「设置 → 用户」里补上。')
      cancelBubbleHide()
      return
    }
  }
  const next = step + 1
  if (next < ONBOARD_QUESTIONS.length) {
    onboardStep.value = next
    say(ONBOARD_QUESTIONS[next])
    cancelBubbleHide()
    clearComposerTimer() // 下一题同样要能答:别让输入框自己收起来
    void nextTick(() => inputEl.value?.focus())
    return
  }
  await finishOnboarding()
}

/** 三题走完:告知"存在哪、随时能改",提示收起后再放角色卡开场白。 */
async function finishOnboarding(): Promise<void> {
  onboardStep.value = null
  hideComposer()
  void backend.onboarding('complete')
  closeOnboarding('我记下了。（注：随时可以在「设置 → 用户」里改。）')
}

/**
 * 跳过引导:写进后端状态,**之后永不主动询问**。
 *
 * 刻意不追问"确定吗":跳过是用户的权利,不该用二次确认当阻力;
 * 想填的时候「设置 → 用户」一直在那儿。
 *
 * 也**不回应任何内容**:把拒绝再翻译成一句客套话甩回去,等于没让用户安静下来;气泡直接收起、回待机即可。
 */
async function skipOnboarding(): Promise<void> {
  if (!onboardingActive()) return
  onboardStep.value = null
  input.value = ''
  hideComposer()
  void backend.onboarding('skip')
  cancelBubbleHide()
  bubble.value = null
  goIdle()
}

/**
 * 引导收尾：只说这一句收尾提示。
 *
 * 开场白**不在这里补放**：它只在每次启动时的第一句出现（见 `bootOpening`）。
 * 引导接管了启动气泡时，本次启动就没有开场白；下次启动、无需引导时照常出现。
 */
function closeOnboarding(hint: string): void {
  say(hint, { ttl: ONBOARD_DONE_TTL_MS })
}

/**
 * 当前打扰等级（后端算；前端只消费）。null = 还没拉到 —— 此时**按「全开」处理**：
 * 宁可多一次主动，也不因一次拉取失败把桌宠变成沉默的摆设。
 */
const petMode = ref<PetModeState | null>(null)
const modeLevel = computed(() => petMode.value?.level ?? 'active')

/**
 * 最近一轮对话的基调（playful / normal / gentle / serious，由**后端**算）。
 *
 * null = 还没聊过 —— 此时待机沿用原有规则，行为与接入表情前**完全一致**
 * （见 pet/expression.ts::idleActionForTone 对未知基调返回 null）。
 */
const tone = ref<string | null>(null)

/**
 * 气泡展示文本：把回复里的**所有换行**（含 `\n` 和 `\n\n` 空行）都折成空格，
 * 连成一段自然文字。聊天气泡不需要多段落 —— 模型常用空行分隔句子，
 * 若保留就会变成「一句一行 + 空行」。同时折叠多余空白。
 */
const bubbleText = computed(() => {
  const raw = bubble.value
  if (raw == null) return ''
  // \s 已覆盖普通空格/制表/换行/全角空格(\u3000)/不换行空格(\u00a0)
  // 全部折成单个空格，再去首尾空白 —— 彻底消除「一句一行/空行/末尾空行」。
  return raw.replace(/\s+/g, ' ').trim()
})

const backend = new BackendClient()
const player = new PngSequencePlayer((frame) => {
  frameUrl.value = frame.url
})

let manifest: PetManifest | null = null
let backToIdle: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let bubbleTimer: ReturnType<typeof setTimeout> | null = null
let greetingRetry: ReturnType<typeof setTimeout> | null = null
let lastShownId = ''
/** 上一条展示过的主动消息文本：用于去重（后端可能重复生成同一条） */
let lastShownText = ''

// ---------------------------------------------------------------- 打字机节流
/**
 * 流式输出节流：模型可能秒回一大段，直连渲染会「唰」地全出现。
 * 这里把 token 先堆进队列，再按固定节奏逐个吐出，输出速度与模型无关。
 * 想让整体更快/更慢，只改 `TYPE_INTERVAL_MS` 与 `TYPE_CHARS_PER_TICK`。
 */
const TYPE_INTERVAL_MS = 45   // 每次吐字的间隔（毫秒）
const TYPE_CHARS_PER_TICK = 1 // 每次吐几个字（调大 = 更快）
let typeQueue = ''
let typeTimer: ReturnType<typeof setInterval> | null = null

/** 启动打字机（若未在跑）。 */
function startTypewriter(): void {
  if (typeTimer !== null) return
  typeTimer = setInterval(() => {
    if (typeQueue.length === 0) return
    const chunk = typeQueue.slice(0, TYPE_CHARS_PER_TICK)
    typeQueue = typeQueue.slice(TYPE_CHARS_PER_TICK)
    bubble.value = (bubble.value ?? '') + chunk
  }, TYPE_INTERVAL_MS)
}

/** 停止打字机并清空队列。 */
function stopTypewriter(): void {
  if (typeTimer !== null) {
    clearInterval(typeTimer)
    typeTimer = null
  }
  typeQueue = ''
}

/** 等打字机把队列吐完后执行回调（用于定稿）；超时兜底防止卡死。 */
function flushTypewriter(done: () => void): void {
  const deadline = Date.now() + 8000
  const poll = (): void => {
    if (typeQueue.length === 0 || Date.now() > deadline) {
      stopTypewriter()
      done()
      return
    }
    setTimeout(poll, TYPE_INTERVAL_MS)
  }
  poll()
}

/**
 * 本轮会话 id。**必须沿用后端返回的值**：后端按 thread 累积历史，
 * 每轮都传 null 会让它每次都新建会话，于是桌宠「聊着聊着就失忆」，
 * 回复退化成通用话术。
 */
let threadId: string | null = null
/** 用户是否已经开口过。开场白在开口后不再出现，直到重启 */
let interacted = false
const preloaded = new Set<string>()

// ---------------------------------------------------------------- 输入框自动隐藏
/**
 * 输入框出现后，若 10 秒内没有输入内容则自动收起（保持桌面干净）；
 * 只要框里有未发送的内容，就**始终显示**，不自动隐藏。
 */
const COMPOSER_IDLE_HIDE_MS = 10_000
let composerHideTimer: ReturnType<typeof setTimeout> | null = null

function clearComposerTimer(): void {
  if (composerHideTimer !== null) {
    clearTimeout(composerHideTimer)
    composerHideTimer = null
  }
}

/** 安排一次「无内容则收起」：到点时输入框为空才隐藏，有内容则保持。 */
function scheduleComposerHide(): void {
  clearComposerTimer()
  composerHideTimer = setTimeout(() => {
    composerHideTimer = null
    if (!input.value.trim()) showComposer.value = false
  }, COMPOSER_IDLE_HIDE_MS)
}

/** 收起输入框（发完消息 / Esc）。 */
function hideComposer(): void {
  clearComposerTimer()
  showComposer.value = false
}

// 输入内容变化：有内容 → 取消自动隐藏；清空 → 重新计时
watch(input, (value) => {
  if (!showComposer.value) return
  if (value.trim()) clearComposerTimer()
  else scheduleComposerHide()
})

// ---------------------------------------------------------------- 待机行为
/** 空闲多久后随机播一个小动作（毫秒）。太短显得躁动，太长像卡住。 */
const IDLE_MICRO_DELAY_MS = 22_000
let idleMicroTimer: ReturnType<typeof setTimeout> | null = null
let proactiveTimer: ReturnType<typeof setTimeout> | null = null

function clearMicroTimer(): void {
  if (idleMicroTimer !== null) {
    clearTimeout(idleMicroTimer)
    idleMicroTimer = null
  }
}

function clearProactiveTimer(): void {
  if (proactiveTimer !== null) {
    clearTimeout(proactiveTimer)
    proactiveTimer = null
  }
}

function clearIdleTimers(): void {
  clearMicroTimer()
  clearProactiveTimer()
}

/**
 * 重置「小动作」计时（仅小动作用）。
 * 关键：小动作**不能**重置「主动靠近」计时 —— 否则 22s 一次的小动作会不停
 * 把 3 分钟的靠近计时推后，导致主动靠近永远不触发（这是之前的 bug）。
 */
function resetMicroTimer(): void {
  clearMicroTimer()
  idleMicroTimer = setTimeout(() => void onIdleMicro(), IDLE_MICRO_DELAY_MS)
}

/**
 * 重置「主动靠近」计时。间隔用**后端的用户偏好**（分钟 → 毫秒）；
 * 拉不到时按默认 10 分钟。
 */
function resetProactiveTimer(): void {
  clearProactiveTimer()
  const minutes = petMode.value?.approach_interval_min ?? APPROACH_INTERVAL_MIN_DEFAULT
  proactiveTimer = setTimeout(
    () => void onIdleProactive(),
    Math.max(1, minutes) * 60_000,
  )
}

/** 重置全部空闲计时（任何用户交互后调用）。 */
function resetIdleTimers(): void {
  resetMicroTimer()
  resetProactiveTimer()
}

/** 空闲到达：随机播一个待机小动作（对话/气泡/拖拽/靠近期间跳过）。 */
async function onIdleMicro(): Promise<void> {
  if (!manifest || busy.value || bubble.value !== null || isDragging.value || approaching) {
    resetMicroTimer()
    return
  }
  // 安静及以上：小动作也停 —— 它是「系统发起」的行为，不是对用户的回应
  if (!mayBeProactive(modeLevel.value)) {
    resetMicroTimer()
    return
  }
  idleTarget = IDLE // 待机小动作后回到开心摇晃
  await playResolved(resolveIdleMicro(manifest))
  resetMicroTimer()
}

/** 空闲到达：桌宠主动「来找你」——走到鼠标旁，再尝试开口（后端沉默则只靠近）。
 * 用户在设置里关掉「主动靠近」后，整条行为（靠近 + 随之的开口）都不发生；
 * 调度线产生的主动消息仍照常经 pollPending 投递。 */
async function onIdleProactive(): Promise<void> {
  if (busy.value || bubble.value !== null || isDragging.value || approaching) {
    resetProactiveTimer()
    return
  }
  // 打扰等级 ≥ 安静：不做任何「系统发起」的事（不靠近、不开口）。
  // 注意用户**主动**说话完全不受等级影响 —— 静默只针对系统发起的行为
  // （PET_POLICY.md §一.2：不能把用户锁在外面）。
  if (!mayBeProactive(modeLevel.value)) {
    resetProactiveTimer()
    return
  }
  // 用户关掉了靠近：直接回计时,不再走近,也不就地开口
  if (petMode.value?.approach_enabled === false) {
    resetProactiveTimer()
    return
  }
  // 主动靠近（桌宠走到鼠标旁）
  await approachMouse()
  try {
    const msg = await backend.evaluateCare()
    if (msg && msg.text && !busy.value && bubble.value === null) {
      lastShownId = msg.id
      lastShownText = msg.text
      say(msg.text)
      await backend.dismiss(msg.id)
    }
  } catch {
    // 后端不可用：静默，桌宠可独立存在
  }
  resetProactiveTimer()
}

/** 回到当前待机动作（默认开心摇晃 / 对话后安静呼吸）。 */
function goIdle(): void {
  // 休眠档：播睡觉 —— 这是「降级必须可被看见」里最直观的一种表示。
  // 用户点它或用快捷键唤起时走 focusComposer，不在这里被拦住。
  if (isAsleep(modeLevel.value)) {
    void play('sleep')
    return
  }
  // 情绪基调优先（见 pet/expression.ts）：playful 更活泼、gentle/serious 更安静；
  // normal 或还没聊过（null）时返回 null，沿用上面那条「说完话→安静呼吸」的原有规则。
  void play(idleActionForTone(tone.value) ?? idleTarget)
}

/**
 * 「进出休眠」这一刻切换动作。
 *
 * 为什么必须有它：睡觉是 `hold` 动作（进入段只播一次，睡眠循环**不会自己结束**），
 * 所以再也没有「播完 → goIdle 重新判断」这条回路 —— 少了这个 watcher，
 * 桌宠睡着后即使等级变了也**永远醒不过来**。
 *
 * 只盯 `isAsleep` 的**翻转**（而非每次 level 变化），避免别的等级调整打断正在进行的
 * 说话 / 互动动画；正在对话时也不抢（结束后 playResolved 的 goIdle 会落到正确动作）。
 */
watch(() => isAsleep(modeLevel.value), () => {
  if (busy.value) return
  goIdle()
})

// ---------------------------------------------------------------- 主动靠近（走到鼠标旁）
/**
 * 桌宠「来找你」：朝鼠标位置走过去，走到附近就停下。
 * 渲染进程负责决策（方向/距离/到达），主进程只做原子移动（move-by）。
 * 走路动画按左右方向切换（MOVE/walk.left / walk.right）。
 *
 * 注意「瞄准点」：桌宠形象在窗口的下方居中（不是窗口中心），所以用窗口内的
 * 视觉锚点（宽 50%、高 72%）对齐鼠标，否则桌宠会停在鼠标偏上方。
 */
const APPROACH_STEP = 9           // 每拍最多移动像素（越大越快）；用户校准为原速的 2/3
const APPROACH_INTERVAL_MS = 40   // 移动节拍
const APPROACH_STOP_DIST = 90     // 桌宠视觉中心距光标多近算到达（留间距，别盖住光标）
const APPROACH_TIMEOUT_MS = 15_000 // 兜底：最长走多久
const PET_ANCHOR_X = 0.5          // 桌宠视觉中心在窗口内的横向比例
const PET_ANCHOR_Y = 0.72         // 纵向比例（形象偏下）
let approaching = false

async function approachMouse(): Promise<void> {
  if (!manifest || isDragging.value || approaching) return
  approaching = true
  let dir: 'left' | 'right' | null = null
  let lastDist = -1
  const start = Date.now()
  try {
    while (Date.now() - start < APPROACH_TIMEOUT_MS) {
      if (busy.value || isDragging.value) break
      const cursor = await window.petWindow?.getCursor()
      const bounds = await window.petWindow?.getBounds()
      if (!cursor || !bounds) {
        console.warn('[pet] 靠近失败：拿不到光标或窗口位置（IPC 不可用？）')
        break
      }
      // 桌宠视觉中心（窗口内锚点）
      const petX = bounds.x + bounds.width * PET_ANCHOR_X
      const petY = bounds.y + bounds.height * PET_ANCHOR_Y
      const dx = cursor.x - petX
      const dy = cursor.y - petY
      const dist = Math.hypot(dx, dy)
      lastDist = dist
      if (dist <= APPROACH_STOP_DIST) break // 到达
      const nd: 'left' | 'right' = dx >= 0 ? 'right' : 'left'
      if (nd !== dir) {
        dir = nd
        console.info(`[pet] 靠近：向${nd === 'right' ? '右' : '左'}走，距光标 ${Math.round(dist)}px`)
        void play(nd === 'right' ? 'walkRight' : 'walkLeft')
      }
      const step = Math.min(APPROACH_STEP, dist)
      await window.petWindow?.moveBy((dx / dist) * step, (dy / dist) * step)
      await new Promise((r) => setTimeout(r, APPROACH_INTERVAL_MS))
    }
    console.info(`[pet] 靠近结束（剩余 ${Math.round(lastDist)}px）`)
  } finally {
    approaching = false
    goIdle() // 到达/超时 → 回待机
  }
}

// ---------------------------------------------------------------- 帧加载自愈
/**
 * 帧加载失败的自愈。
 *
 * 为什么需要：首次安装运行时素材是刚解压的冷文件（冷盘 + 杀软扫描），第一次读取
 * 偶发失败。`preloadFrames` 对单帧失败是**静默放行**的（宁可少一帧，不可整段不播），
 * 于是 <img> 会拿到一个读不到的 URL；而 <img> 自身没有重试入口，结果就是
 * **永久破图 + alt 文本**，只能重开应用。这里补上三层兜底，每层都有界：
 *   1. 同一帧换 URL（?r=1 / ?r=2）最多重试 FRAME_MAX_RETRIES 次；
 *   2. 重试耗尽 → 重取清单并重放当前动作，且 MANIFEST_RELOAD_MIN_MS 内至多一次；
 *   3. 任意一帧成功加载（onFrameLoaded）即清零计数，恢复常态。
 * 最坏情况是「每 5s 一次重取 + 每次失败最多 2 次重试」，不存在无限循环。
 */
const FRAME_MAX_RETRIES = 2
/** 两次「重取清单」之间的最小间隔：持续失败时也不会高频打转 */
const MANIFEST_RELOAD_MIN_MS = 5_000
let frameRetries = 0
let lastManifestReloadAt = 0
/**
 * 「当前动作」的重放入口：清单重取成功后，用它在**新清单**上重新解析并播放同一动作。
 * 由 play() 与待机小动作各自登记（它们才知道动作来源），自愈逻辑只负责调用。
 */
let replayCurrent: (() => void) | null = null

/** 帧加载失败：先换 URL 重试，重试耗尽则走清单自愈（有 5s 节流）。 */
function onFrameError(): void {
  const frame = player.currentFrame
  if (!frame) return
  if (frameRetries < FRAME_MAX_RETRIES) {
    frameRetries += 1
    // 查询串会改变缓存键：协议层重新读取文件，而不是复用上一次的失败结果
    frameUrl.value = `${frame.url}?r=${frameRetries}`
    return
  }
  void reloadManifestAndReplay()
}

/** 帧加载成功：清零重试计数（恢复正常路径）。 */
function onFrameLoaded(): void {
  frameRetries = 0
}

/**
 * 重试耗尽后的兜底：重取清单 → 重放当前动作。
 * 5 秒内至多一次 —— 即使文件真的缺失，也只是每 5 秒尝试一次，绝不打转。
 */
async function reloadManifestAndReplay(): Promise<void> {
  const now = Date.now()
  if (now - lastManifestReloadAt < MANIFEST_RELOAD_MIN_MS) return
  lastManifestReloadAt = now
  frameRetries = 0 // 重放会重新走一遍重试预算
  try {
    const res = await fetch(MANIFEST_URL)
    if (!res.ok) return
    manifest = (await res.json()) as PetManifest
  } catch {
    return // 清单仍读不到：本次放弃，等下一次错误（仍有 5s 节流兜底）
  }
  replayCurrent?.()
}

/** 播放一个已解析的动作；单次动作播完自动回待机。 */
async function playResolved(resolved: ResolvedAction | null): Promise<void> {
  if (!manifest) return
  if (!resolved) {
    goIdle()
    return
  }

  // 待机小动作是随机解析的：登记「再随机一次」的重放入口（供清单自愈重放）
  if (resolved.action === 'idleMicro') {
    replayCurrent = () => {
      if (!manifest) return
      const again = resolveIdleMicro(manifest)
      if (again) void playResolved(again)
    }
  }

  if (backToIdle !== null) {
    clearTimeout(backToIdle)
    backToIdle = null
  }

  const key = `${resolved.state}/${resolved.variant}`
  if (!preloaded.has(key)) {
    await preloadFrames(resolved.frames)
    preloaded.add(key)
  }
  player.play(resolved.frames, resolved.loop, resolved.loopFrom ?? 0)

  if (!resolved.loop) {
    const total = resolved.frames.reduce((sum, frame) => sum + frame.time, 0)
    backToIdle = setTimeout(() => goIdle(), total)
  }
}

/** 播放一个具名动作；`repeat` 覆盖三段式 B 段循环次数。 */
async function play(action: PetAction, repeat?: number): Promise<void> {
  if (!manifest) return
  const resolved = resolveAction(manifest, action, repeat)
  if (!resolved) {
    // 待机类动作缺失时不要递归回 goIdle，否则会打转（见 pet/behavior.ts 的说明）
    if (action !== IDLE && action !== IDLE_CALM) goIdle()
    return
  }
  // 登记重放来源：清单自愈重取后，用新清单重新解析同一动作（含 repeat 覆盖）
  replayCurrent = () => {
    void play(action, repeat)
  }
  await playResolved(resolved)
}

// ---------------------------------------------------------------- 气泡生命周期
function cancelBubbleHide(): void {
  if (bubbleTimer !== null) {
    clearTimeout(bubbleTimer)
    bubbleTimer = null
  }
}

/** 安排气泡自动收起：收起后回到待机动画 */
function scheduleBubbleHide(ttl = BUBBLE_TTL_MS): void {
  cancelBubbleHide()
  bubbleTimer = setTimeout(() => {
    bubbleTimer = null
    bubble.value = null
    goIdle() // 气泡消失 → 桌宠回待机
  }, ttl)
}

/**
 * 按回复长度决定说话动画的 B 段循环次数：短句少循环（动画短），长句多循环。
 * 说话素材每轮 B 段约 15 帧 ×125ms ≈ 1.9s，加 A/C 段约 0.9s。
 *   短句(≤12字) → 1 次（≈2.8s）；中句(≤40字) → 2 次；长句 → 3 次。
 */
function sayRepeat(text: string): number {
  const n = (text ?? '').trim().length
  if (n <= 12) return 1
  if (n <= 40) return 2
  return 3
}

/** 说一句并安排自动收起 */
function say(text: string, opts: { ttl?: number } = {}): void {
  bubble.value = text
  void play('say', sayRepeat(text))
  scheduleBubbleHide(opts.ttl ?? BUBBLE_TTL_MS)
}

/** 点击气泡：立即收起（用户不想等自动收起时）。 */
function dismissBubble(): void {
  // 引导进行中点掉气泡,用户就不知道该答什么了 —— 只能靠「答题」或「跳过」推进。
  if (onboardingActive()) return
  cancelBubbleHide()
  bubble.value = null
  goIdle()
}

// ---------------------------------------------------------------- 输入框唤起
/**
 * 让输入框出现并聚焦。
 *
 * 唤起路径：**悬停到桌宠上**（替代早先的右键）。首启引导期间输入框必须常驻
 * （问题被 10 秒计时收走就没法答了），所以引导中不做自动收起。
 */
function focusComposer(): void {
  showComposer.value = true
  if (!onboardingActive()) scheduleComposerHide() // 10 秒无输入则自动收起
  void nextTick(() => inputEl.value?.focus())
}

/** Esc：收起输入框（输入框常态隐藏的退出方式）。 */
function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    hideComposer()
  }
}

// ---------------------------------------------------------------- 打扰等级（消费）
/**
 * 拉取当前打扰等级。
 *
 * 后端是唯一真相（含夜间窗口与到期判定），前端**不自己算**，只消费 `level`。
 * 拉取失败时保留上一次的值 —— 一次网络抖动不该让桌宠行为突变。
 */
async function refreshMode(): Promise<void> {
  try {
    const m = await backend.petMode()
    if (m) petMode.value = m
  } catch {
    // 后端不可用：保留现值（null 时按「全开」处理）
  }
}

/**
 * 「勿扰 / 专注」切换：在全开 ↔ 勿扰之间来回切（见 pet/mode.ts::nextDndLevel）。
 * 这是**临时**静默 —— 与设置里的「永久关闭主动关怀」是两个语义。
 * 入口在**托盘右键菜单**（主进程经 sendToPet('toggle-dnd') 转达）。
 */
async function toggleDnd(): Promise<void> {
  const next = nextDndLevel(modeLevel.value)
  try {
    const m = await backend.setPetMode({ manualLevel: next })
    if (m) petMode.value = m
    console.info(`[pet] 打扰等级 → ${levelLabel(next)}`)
  } catch {
    // 后端不可用：不改本地状态，避免「看起来切了其实没切」的假象
  }
}

/** 取消主进程动作订阅；未订阅时为 null。 */
let offShortcut: (() => void) | null = null

/** 主进程转达的动作（当前只有托盘菜单的勿扰切换）。 */
function onShortcut(action: string): void {
  if (action === 'toggle-dnd') void toggleDnd()
}

/** 打开主窗口（功能界面）。传 view 则直达该页（悬停图标用）。 */
async function openMain(view?: 'chat' | 'calendar' | 'settings'): Promise<void> {
  hoverActions.value = false
  await window.petWindow?.openMain(view)
}

// 回复较长时气泡会出现滚动条，始终把最新内容留在视野内
watch(bubble, async () => {
  await nextTick()
  const el = bubbleEl.value
  if (el) el.scrollTop = el.scrollHeight
})

// ---------------------------------------------------------------- 点击穿透
// 透明窗口默认整块吃鼠标，会挡住下面的应用。做法：默认让主进程忽略鼠标
// （穿透到桌面）；指针进入「桌宠不透明像素 / 气泡 / 输入框 / 菜单」时再切回接收。
//
// 桌宠是否命中用**逐像素 alpha**判定：把当前帧画到离屏 canvas 采样，
// 因为素材左右各有大片透明（角色仅占画布中间 ~35% 宽），用矩形包围盒仍会误挡。
let ignoringMouse = true

function applyIgnore(ignore: boolean): void {
  if (ignore === ignoringMouse) return
  ignoringMouse = ignore
  void window.petWindow?.setIgnoreMouse(ignore)
}

/** 命中判定用的离屏 canvas（按当前帧缓存）。 */
let hitCanvas: HTMLCanvasElement | null = null
let hitCanvasUrl = ''

/**
 * 兜底包围盒（相对图片的归一化坐标）。
 * 素材角色大致居中：实测不透明区 x≈0.34–0.69、y≈0.03–0.97，这里略放宽。
 * 用途：当逐像素采样不可用（canvas 被跨源污染）时的退路。
 */
const PET_HIT_BOX = { x0: 0.30, x1: 0.72, y0: 0.02, y1: 0.99 }

/**
 * 判断指针是否落在桌宠「可交互」区域（相对 img 元素坐标）。
 * 优先逐像素 alpha（最准）；采样不可用则回落到 PET_HIT_BOX。
 */
function hitPet(img: HTMLImageElement, clientX: number, clientY: number): boolean {
  const rect = img.getBoundingClientRect()
  const nx = (clientX - rect.left) / rect.width
  const ny = (clientY - rect.top) / rect.height
  if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return false

  const inBox = nx >= PET_HIT_BOX.x0 && nx <= PET_HIT_BOX.x1 &&
    ny >= PET_HIT_BOX.y0 && ny <= PET_HIT_BOX.y1

  if (!hitCanvas) hitCanvas = document.createElement('canvas')
  const src = img.currentSrc || img.src
  const w = img.naturalWidth || 512
  const h = img.naturalHeight || 512
  // 帧变了才重画，避免每次移动都 drawImage
  if (hitCanvasUrl !== src || hitCanvas.width !== w) {
    hitCanvas.width = w
    hitCanvas.height = h
    // willReadFrequently：本 canvas 专用于 getImageData 采样，开启可避免 GPU 回读警告并提速
    const ctx = hitCanvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return inBox
    ctx.clearRect(0, 0, w, h)
    ctx.drawImage(img, 0, 0, w, h)
    hitCanvasUrl = src
  }
  const ctx = hitCanvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return inBox
  const px = Math.min(w - 1, Math.max(0, Math.floor(nx * w)))
  const py = Math.min(h - 1, Math.max(0, Math.floor(ny * h)))
  try {
    const alpha = ctx.getImageData(px, py, 1, 1).data[3]
    return alpha > 20 // 阈值：略高于全透明，容忍边缘半透明
  } catch {
    // 跨源污染导致 getImageData 抛错：回落到包围盒
    return inBox
  }
}

/**
 * 全局指针移动：决定窗口该「接收」还是「穿透」，顺带维护悬停图标的可见性。
 * 可接收区 = 气泡 / 输入框 / 悬停图标 / 桌宠不透明像素；其余一律穿透。
 *
 * 用 `document.elementFromPoint` 而不是 `event.target`：主进程以 { forward: true }
 * 忽略鼠标时，转发事件的 target 未必是真实元素；elementFromPoint 是纯 DOM 命中，
 * 不受 OS 鼠标捕获影响。
 *
 * 图标与穿透的判定分开：
 *   - **显示**只看是否进入图片矩形 —— 用户说的"放在贴图上"就是图片范围，不必先摸到角色本体；
 *   - **接收**仍按不透明像素 —— 图片四角的透明区继续穿透，不挡桌面点击。
 *     图标自身由上面 `closest('.pet-actions')` 那条分支负责接收，所以点得到。
 *
 * 悬停唤起输入框：从「不在桌宠上」跨到「在桌宠上」的那一次移动触发
 * （发送/超时收起后，鼠标要离开桌宠再回来才会再次唤起，避免刚发完又弹开）。
 */
let hoveringPet = false

function onGlobalPointerMove(event: PointerEvent): void {
  // 拖拽中必须保持接收，否则 pointermove/up 收不到
  if (pointerActive) {
    hoveringPet = false
    hoverActions.value = false
    applyIgnore(false)
    return
  }
  const el = document.elementFromPoint(event.clientX, event.clientY) as HTMLElement | null
  if (el && el.closest('.bubble, .composer, .pet-actions')) {
    hoveringPet = false
    applyIgnore(false)
    return
  }
  const petImg = petEl.value
  if (!petImg) {
    hoveringPet = false
    hoverActions.value = false
    applyIgnore(true)
    return
  }
  const r = petImg.getBoundingClientRect()
  const inRect =
    event.clientX >= r.left && event.clientX <= r.right &&
    event.clientY >= r.top && event.clientY <= r.bottom
  hoverActions.value = inRect
  // 悬停进入桌宠的第一次移动:唤起输入框(发送中不抢;气泡开着也照常唤起,好接着回复)
  if (inRect && !hoveringPet && !busy.value && !showComposer.value) {
    focusComposer()
  }
  hoveringPet = inRect
  applyIgnore(!inRect || !hitPet(petImg, event.clientX, event.clientY))
}

// ---------------------------------------------------------------- 指针交互
// 桌宠靠拖拽移动，所以不能用 -webkit-app-region: drag（它会吞掉点击）。
// 这里自己区分「点击」与「拖拽」：总位移超过阈值即视为拖拽。
//
// 拖拽时**只上报位移**，窗口起点由主进程在 dragStart 时同步记录。
// 渲染进程不能先 await 拿位置：pointermove 会早于 IPC 往返到达，用未初始化的基准
// 计算绝对坐标会把窗口甩到屏幕角落。

let pointerActive = false
let dragged = false
let dragMouseX = 0
let dragMouseY = 0
/** 按下时命中的区域（head / body / other）：决定拖动播放哪个动画。 */
let dragZone: HitZone = 'other'

function onPointerDown(event: PointerEvent): void {
  // 只有左键参与桌宠交互(拖拽/摸头);右键不再改变桌宠状态 ——
  // 系统级动作统一在托盘右键菜单。
  if (event.button !== 0) return
  pointerActive = true
  dragged = false
  dragMouseX = event.screenX
  dragMouseY = event.screenY
  // 记录按下命中的区域：头部 → 捏；其它 → 抓衣服吊起来
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  dragZone = rect.width > 0 && rect.height > 0
    ? hitTest((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height)
    : 'other'
  idleTarget = IDLE // 用户交互：待机切回开心摇晃
  resetIdleTimers() // 用户交互：重置空闲计时
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  void window.petWindow?.dragStart()
}

function onPointerMove(event: PointerEvent): void {
  if (!pointerActive) return
  // 相对**按下点**的总位移：既用于阈值判断，也直接作为位移上报（主进程加回起点）
  const dx = event.screenX - dragMouseX
  const dy = event.screenY - dragMouseY
  if (!dragged) {
    if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return
    dragged = true
    isDragging.value = true // 拖拽开始：隐藏气泡/输入框，只留动画
    // 头部区域 → 捏；其它区域 → 抓衣服吊起来（悬空保持）
    void play(dragZone === 'head' ? 'pinch' : 'raiseHold')
  }
  void window.petWindow?.dragMove(dx, dy)
}

function onPointerUp(event: PointerEvent): void {
  if (!pointerActive) return
  pointerActive = false
  isDragging.value = false // 拖拽结束：恢复气泡/输入框
  void window.petWindow?.dragEnd()
  if (dragged) {
    // 松手：头部拖动直接回待机；其它区域播「放下」再回待机
    if (dragZone === 'head') goIdle()
    else void play('raiseDrop')
    return
  }
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) return
  const zone = hitTest(
    (event.clientX - rect.left) / rect.width,
    (event.clientY - rect.top) / rect.height,
  )
  if (zone === 'head') void play('touchHead')
  else if (zone === 'body') void play('touchBody')
  // 其它区域（如腿部）：不触发任何动作，回待机即可。
  // 早先这里播 'think'，导致「摸腿变成挠头」且循环停不下来。
  else goIdle()
}

// ---------------------------------------------------------------- 对话
async function send(): Promise<void> {
  const text = input.value.trim()
  // 首启引导期间:输入框里的答案是"资料",不是对话 —— 不走 /api/chat
  // (此时大概率还没配 API Key,走对话会直接 503)。
  if (onboardingActive()) {
    if (busy.value) return
    input.value = ''
    busy.value = true
    try {
      await answerOnboarding(text)
    } finally {
      busy.value = false
    }
    return
  }
  if (!text || busy.value) return
  input.value = ''
  busy.value = true
  interacted = true // 用户开口了：开场白到此为止，重启前不再出现
  cancelBubbleHide() // 对话期间气泡不自动消失
  idleTarget = IDLE // 用户交互：待机切回开心摇晃
  resetIdleTimers() // 用户交互：重置空闲计时
  bubble.value = ''
  void play('think')
  try {
    await backend.chat(
      text,
      {
        onToken: (token) => {
          // 打字机节流：token 先入队，由定时器按固定间隔逐个吐出，
          // 输出速度与模型无关（模型有时秒回一大段，直连会「唰」一下全出来）。
          typeQueue += token
          startTypewriter()
        },
        onResult: (reply, id, analysis) => {
          if (id) threadId = id // 记住会话：下一轮沿用，桌宠才不会「失忆」
          // 情绪基调只影响**待机表情**，不影响任何判定（见 pet/expression.ts）。
          // 后端不可用/字段缺失时保持上一次的值，不让表情乱跳。
          if (analysis?.tone) {
            tone.value = analysis.tone
            console.info(`[pet] 对话基调 → ${toneLabel(analysis.tone)}`)
          }
          // 对话结束：待机切到「安静呼吸」（对话完后没进一步对话的待机）
          idleTarget = IDLE_CALM
          // 等打字机把队列吐完再定稿，避免「瞬间补全」跳变
          flushTypewriter(() => {
            bubble.value = reply || bubble.value
            void play('say', sayRepeat(bubble.value ?? ''))
            scheduleBubbleHide() // 说完后自动收起 → 回待机
          })
        },
        onError: (detail) => {
          stopTypewriter()
          bubble.value = `连不上后端（${backend.baseUrl}）：${detail}`
          goIdle()
          scheduleBubbleHide(ERROR_TTL_MS)
        },
      },
      threadId,
    )
  } catch {
    // fetch 层的网络异常（后端没起 / 端口不通）不会走 onError 回调，必须在这里兜住；
    // 否则异常穿透、气泡保持空，界面毫无反馈 —— 看起来就是「输入没反应」。
    bubble.value = `连不上后端（${backend.baseUrl}），请稍候或检查后端进程`
    goIdle()
    scheduleBubbleHide(ERROR_TTL_MS)
  } finally {
    busy.value = false
    hideComposer() // 发完即收起输入框（常态隐藏，保持桌面干净）
  }
}

/**
 * 显示角色卡的开场白（first_mes）。
 *
 * **为什么带重试**：Electron 先开窗口、再拉起后端，首次请求几乎必然扑空
 * （后端还在启动）。早先失败即放弃，结果是开场白**从来没出现过**。
 * 现在退避重试，直到成功、用户开口、或超过尝试次数。
 */
async function showGreeting(attempt = 0): Promise<void> {
  if (interacted) return
  try {
    const text = await backend.greeting()
    if (!text || interacted) return
    say(text, { ttl: GREETING_TTL_MS })
    return
  } catch {
    // 后端还没就绪：下面安排重试
  }
  if (attempt < GREETING_MAX_ATTEMPTS) {
    greetingRetry = setTimeout(() => void showGreeting(attempt + 1), GREETING_RETRY_MS)
  }
}

/**
 * 拉取待读主动消息。
 *
 * 四条规则（都是被真实问题逼出来的）：
 *   1. **任何气泡显示期间都不插入** —— 早先给开场白开了个「可以被顶掉」的例外，
 *      结果开场白每 15s 就被 petlife 消息覆盖；
 *   2. **文本去重** —— 后端会重复生成同一条消息，不去重就会把同一句话反复弹出来；
 *   3. **安静及以上不弹** —— 后端此时已不产生新消息，这条拦的是「切静默前就已入
 *      outbox 的存量」，否则刚按下勿扰还会立刻弹一条（体验很怪）。待读消息不会丢：
 *      恢复后仍然看得到；
 *   4. **看不见时补一条系统通知** —— 桌宠可能被收进托盘、或被别的窗口盖住，
 *      气泡是看不见的，通知是唯一的送达手段。**是否真的弹由主进程按窗口可见性决定**
 *      （它才知道窗口状态），见 main.cjs 的 `pet:notify`；本层只管「业务上该不该」。
 */
async function pollPending(): Promise<void> {
  // 引导期间不插主动消息:问题被顶掉,用户就答不成了。
  if (onboardingActive()) return
  try {
    const messages = await backend.pending()
    const next = messages[0]
    if (!next || next.id === lastShownId) return
    // 正在对话 / 气泡未收起：本轮跳过，不推进 lastShownId，留到下一轮再展示
    if (busy.value || bubble.value !== null) return
    if (!mayBeProactive(modeLevel.value)) return
    lastShownId = next.id
    const text = String(next.text ?? '')
    if (text && text === lastShownText) {
      // 与上一条内容相同：视作重复，直接确认已读，不再弹一次
      await backend.dismiss(next.id)
      return
    }
    lastShownText = text
    say(text)
    // 勿扰及以上**不出通知**（PLAN_NEXT.md §3.1：安全项之外的静默要被完整尊重）。
    // lastShownId 已推进，所以同一条消息不会再走到这里 —— 不会反复弹同一个通知。
    if (mayNotify(modeLevel.value)) {
      void window.petWindow?.notify({ body: text })
    }
    await backend.dismiss(next.id)
  } catch {
    // 后端没起来属于正常情况（桌宠可独立存在）
  }
}

/**
 * 素材清单加载重试参数。
 *
 * 为什么带重试：首次安装运行时素材是刚解压的冷文件（冷盘 + 杀软扫描），第一次读取
 * 偶发失败。早先失败即 return，气泡提示后就**永远停在破图状态**，只能重开应用；
 * 与帧加载失败是同一类问题。主进程侧的预热（见 main.cjs）是第一道防线，这里是第二道。
 */
const MANIFEST_MAX_ATTEMPTS = 10
const MANIFEST_RETRY_MS = 1_500
let manifestRetryTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 启动开场:有首启引导就先问三件事,否则放角色卡开场白。
 *
 * 两条路径**互斥**:同时说话会把对方的气泡顶掉一半
 * (开场白只在启动出现一次,被顶掉就永远看不到了)。
 */
async function bootOpening(): Promise<void> {
  if (await beginOnboardingIfNeeded()) return
  await showGreeting()
}

/** 清单加载成功后的启动序列（首次成功与重试成功后都走这里）。 */
function startPet(): void {
  void play('startup')
  void bootOpening()
  void refreshMode()
  void pollPending()
  // 等级随轮询一起刷新：夜间窗口的进出由后端判定，前端只是定期问一次
  pollTimer = setInterval(() => {
    void pollPending()
    void refreshMode()
  }, POLL_INTERVAL)
  resetIdleTimers() // 启动待机计时（空闲小动作 / 主动对话）
}

/** 加载素材清单并启动桌宠；失败保留原有提示气泡，并按固定间隔重试（有界）。 */
async function loadManifestAndStart(attempt = 0): Promise<void> {
  try {
    const res = await fetch(MANIFEST_URL)
    manifest = (await res.json()) as PetManifest
    console.info(`[pet] 素材就绪：${Object.keys(manifest.states).length} 个状态`)
  } catch {
    bubble.value = '素材清单加载失败，请先运行 pet/sync_vup_assets.py'
    if (attempt < MANIFEST_MAX_ATTEMPTS) {
      manifestRetryTimer = setTimeout(() => void loadManifestAndStart(attempt + 1), MANIFEST_RETRY_MS)
    }
    return
  }
  if (attempt > 0) bubble.value = null // 重试成功：收起错误提示，继续正常启动
  startPet()
}

/**
 * 指针离开窗口：收起悬停图标并回到穿透。
 *
 * 不做这一步时，鼠标停在桌宠身上直接移出窗口会**保持最后一次的「接收」状态**，
 * 于是整个窗口矩形（含四角透明区）一直拦着桌面点击。
 */
function onPointerLeaveWindow(): void {
  hoveringPet = false
  hoverActions.value = false
  applyIgnore(true)
}

onMounted(() => {
  // 点击穿透：全局监听指针移动，动态决定窗口是否接收鼠标
  window.addEventListener('pointermove', onGlobalPointerMove)
  document.documentElement.addEventListener('pointerleave', onPointerLeaveWindow)
  window.addEventListener('keydown', onKeydown)
  // 全局快捷键由主进程注册，这里只订阅动作；卸载时必须退订，否则重建窗口会累积监听器
  offShortcut = window.petWindow?.onShortcut(onShortcut) ?? null
  applyIgnore(true) // 初始穿透（透明区不挡桌面）
  void loadManifestAndStart()
})

onUnmounted(() => {
  window.removeEventListener('pointermove', onGlobalPointerMove)
  document.documentElement.removeEventListener('pointerleave', onPointerLeaveWindow)
  window.removeEventListener('keydown', onKeydown)
  offShortcut?.()
  offShortcut = null
  player.stop()
  stopTypewriter()
  clearIdleTimers()
  clearComposerTimer()
  if (backToIdle !== null) clearTimeout(backToIdle)
  if (pollTimer !== null) clearInterval(pollTimer)
  if (greetingRetry !== null) clearTimeout(greetingRetry)
  if (manifestRetryTimer !== null) clearTimeout(manifestRetryTimer)
  cancelBubbleHide()
})
</script>

<template>
  <div class="pet-root">
    <div class="pet-wrap">
      <img
        v-show="frameUrl"
        class="pet"
        ref="petEl"
        :src="frameUrl"
        alt="桌宠"
        crossorigin="anonymous"
        draggable="false"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointercancel="onPointerUp"
        @contextmenu.prevent
        @error="onFrameError"
        @load="onFrameLoaded"
      />

      <!-- 悬停浮现的三枚快捷入口：设置 / 对话 / 日历（自上而下；角色卡与记忆已并入设置） -->
      <div v-if="hoverActions && !isDragging" class="pet-actions">
        <button type="button" title="设置" @click="openMain('settings')">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M4 8.5h16M4 15.5h16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
            <circle cx="9.5" cy="8.5" r="2.2" fill="#fff" stroke="currentColor" stroke-width="1.7" />
            <circle cx="14.5" cy="15.5" r="2.2" fill="#fff" stroke="currentColor" stroke-width="1.7" />
          </svg>
        </button>
        <button type="button" title="对话" @click="openMain('chat')">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M4.6 6.4h14.8v9.2h-7.9l-3.6 3.1v-3.1H4.6z"
              stroke="currentColor"
              stroke-width="1.7"
              stroke-linejoin="round"
            />
          </svg>
        </button>
        <button type="button" title="日历" @click="openMain('calendar')">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <rect x="4" y="5.6" width="16" height="14" rx="2.4" stroke="currentColor" stroke-width="1.7" />
            <path d="M4 10.2h16M8.4 3.6v3.4M15.6 3.6v3.4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>
    <!-- 输入框：钉在窗口底部，不参与桌宠的布局（出现/消失都不顶动桌宠） -->
    <form
      v-show="showComposer"
      class="composer"
      :class="{ 'drag-hidden': isDragging }"
      @submit.prevent="send"
    >
      <input
        ref="inputEl"
        v-model="input"
        :disabled="busy"
        placeholder="和我说点什么…"
      />
    </form>
    <!-- 气泡悬在输入框上方：尾巴朝上指着桌宠；长内容向上生长,最多盖到桌宠脚边 -->
    <div
      v-if="bubble !== null"
      ref="bubbleEl"
      class="bubble"
      :class="{ 'drag-hidden': isDragging }"
      @click="dismissBubble"
    >
      <div>{{ bubbleText }}</div>
      <!-- 首启引导的跳过键：跳过即永不主动询问（仍可在「设置 → 用户」里填） -->
      <button
        v-if="onboardingActive()"
        type="button"
        class="onboard-skip"
        @click.stop="skipOnboarding"
      >跳过</button>
    </div>
  </div>
</template>

<style scoped>
.pet-root {
  position: relative;
  height: 100%;
  padding: 8px;
  box-sizing: border-box;
}

/* 拖拽中：气泡与输入框隐藏，只留桌宠动画（保留占位，避免图片跳动） */
.drag-hidden {
  visibility: hidden;
}

.bubble {
  /* 悬在输入框上方、桌宠下方:绝对定位,出现/消失不影响桌宠的位置 */
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 52px;
  z-index: 4;
  width: 84%;
  padding: 10px 13px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.97);
  color: #24292f;
  font-size: 13.5px;
  line-height: 1.62;
  letter-spacing: 0.01em;
  white-space: normal;
  word-break: break-word;
  max-height: 38%;
  overflow-y: auto;
  border: 1px solid rgba(0, 0, 0, 0.06);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.16), 0 1px 3px rgba(0, 0, 0, 0.08);
  backdrop-filter: blur(6px);
  animation: bubble-in 160ms ease-out;
}

/*
 * 首启引导的「跳过」键。
 * 刻意做得轻：它是退路，不该比"继续回答"更显眼（不喧宾夺主地诱导点击）。
 * 命中判定沿用 `.bubble` 的区域（见 onGlobalPointerMove 的 closest 选择器），
 * 所以透明窗口里这个按钮天然可点。
 */
.onboard-skip {
  display: block;
  margin: 8px 0 0 auto;
  width: fit-content;
  padding: 3px 11px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 999px;
  background: transparent;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.6;
  cursor: pointer;
}

.onboard-skip:hover {
  background: rgba(0, 0, 0, 0.05);
  color: #374151;
}

/* 气泡尾巴：指向**上方**的桌宠（气泡现位于输入框下方） */
.bubble::after {
  content: '';
  position: absolute;
  left: 50%;
  top: -6px;
  width: 12px;
  height: 12px;
  margin-left: -6px;
  background: inherit;
  border-left: 1px solid rgba(0, 0, 0, 0.06);
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  transform: rotate(45deg);
  border-radius: 3px 0 0 0;
}

@keyframes bubble-in {
  from { opacity: 0; transform: translateY(6px) scale(0.98); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}

/* 细滚动条，避免默认粗条破坏观感 */
.bubble::-webkit-scrollbar {
  width: 6px;
}
.bubble::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.18);
  border-radius: 3px;
}
.bubble::-webkit-scrollbar-track {
  background: transparent;
}

/*
 * 桌宠 + 悬停图标共用的定位容器。
 * 图标要贴着图片右上角，所以不能直接跟 img 一起用 flex 直排 —— 图片得先有
 * 自己的定位盒子。宽度仍是最初的 72%，视觉与改动前一致。
 *
 * 桌宠钉在窗口底部之上预留的对话区（输入框 + 间距）：
 * 输入框/气泡出现与消失都只在预留区里发生，**不再把桌宠顶起来**。
 */
.pet-wrap {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 58px;
  width: 72%;
}

.pet {
  display: block;
  width: 100%;
  height: auto;
  cursor: grab;
}

.pet:active {
  cursor: grabbing;
}

/*
 * 悬停浮现的三枚快捷入口。
 * 位置贴着图片右上角的透明区：角色本体只占图片横向约 0.34–0.69，
 * 竖排一列落在右侧透明带上，不压角色。
 */
.pet-actions {
  position: absolute;
  top: 4px;
  right: 4px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  animation: actions-in 140ms ease-out;
}

.pet-actions button {
  display: flex;
  width: 22px;
  height: 22px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.9);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.92);
  color: #4c728e;
  box-shadow: 0 2px 6px rgba(15, 26, 36, 0.18);
  cursor: pointer;
  transition: background 0.12s ease, transform 0.12s ease;
}

.pet-actions button:hover {
  background: #fff;
  transform: translateY(-1px);
}

.pet-actions svg {
  width: 13px;
  height: 13px;
}

@keyframes actions-in {
  from { opacity: 0; transform: translateY(-3px); }
  to   { opacity: 1; transform: translateY(0); }
}

.composer {
  /* 输入框钉在窗口最底部:不参与桌宠的布局,唤起时桌宠纹丝不动 */
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 8px;
  z-index: 5;
  width: 68%;
}

.composer input {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: none;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.9);
  color: #1f2328;
  font-size: 13px;
  outline: none;
}

.composer input::placeholder {
  color: #8b949e;
}
</style>
