<script setup lang="ts">
/**
 * 长期记忆洞察视图（只读）。
 *
 * 数据来源：GET /api/memory/insight。展示记忆沉淀结果、读侧配额命中情况与
 * 守护层判定依据，用于内部核对与演示。全部数值不得进入对用户可见的对话文案。
 *
 * 展示约束：
 * - 界面文案面向评审场景，使用客观、精简的陈述句；机制只写设计动机与边界，不出现口语与拟人表述。
 * - 数值必须附带语义说明（时间跨度、相对水平、是否达到阈值）。
 * - 只读：不提供任何修改或删除记忆的入口。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../../shell/api'
import type { InsightUser, MemoryInsight } from '../../../types'
import Icon from '../../../shell/components/Icon.vue'

/**
 * 可见性由外层经 v-show 控制，组件常驻不重建。
 *
 * 前置条件：外层在每次 active 变为 true 时触发重拉，保证展示与最新数据一致。
 * 后置保证：本组件不自带整页滚动，滚动容器与留白由外层提供。
 */
const props = defineProps<{ active?: boolean }>()

const data = ref<MemoryInsight | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const activeUser = ref<string>('')
const openFacts = ref(false)
const openEpisodes = ref(false)
const openPending = ref(false)
const openContext = ref(false)

/* 情绪英文标识 → 中文 */
const EMO: Record<string, string> = {
  happy: '开心', sad: '难过', anxious: '焦虑', angry: '生气',
  frustrated: '沮丧', hopeful: '有希望', fearful: '害怕', neutral: '平静',
}
/* 情绪走向 */
const ARC: Record<string, string> = {
  rising: '逐渐好转', stable: '基本平稳', falling: '逐渐走低',
}
/* 话题 */
const TOPIC: Record<string, string> = {
  school: '学业', work: '工作', family: '家人', social: '社交',
  relationship: '感情/人际', health: '身体与睡眠', emotion: '情绪',
  life: '日常生活', plan: '计划安排', preference: '喜好', other: '其他',
}
/* 守护档位 → 展示名 */
const TIER_TEXT: Record<string, string> = {
  stable: '未见异常',
  watch: '值得关注',
  elevated: '重点关注',
  '数据不足': '数据不足',
}
const TIER_COLOR: Record<string, string> = {
  stable: '#16a34a', watch: '#f59e0b', elevated: '#dc2626', '数据不足': '#64748b',
}
/* 用户原话的风险类型 */
const FACET: Record<string, string> = {
  ideation: '含轻生意向的表述',
  intent: '表达实施意向',
  plan: '提及具体方式',
  behavior: '存在相关行为',
  /* signal = 兜底留痕:该段被判定为风险、原话由系统直接记下(未经模型抽取,见 store 2.6) */
  signal: '系统直接留存的风险原话',
}

const current = computed<InsightUser | null>(() => {
  if (!data.value?.users?.length) return null
  return data.value.users.find((u) => u.user === activeUser.value) ?? data.value.users[0]
})

/**
 * 拉取洞察数据并维护选中用户的一致性。
 *
 * 后置保证：返回后 activeUser 必指向列表内存在的用户；原选中用户已不存在
 * （数据被清或账号切换）时回落首位，避免选中态与展示内容错位。
 *
 * @throws 请求失败时置 error，不清空已有数据。
 */
async function load() {
  loading.value = true
  error.value = null
  try {
    data.value = await api.getMemoryInsight()
    const users = data.value.users
    if (!users.some((u) => u.user === activeUser.value)) {
      activeUser.value = users[0]?.user ?? ''
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
onMounted(load)
// 数据更新发生在聊天与后台调度期间，组件常驻不重建，切回本视图时必须重拉。
watch(() => props.active, (on) => { if (on) load() })

/* ============================ 图表 ============================ */
const W = 900
const H = 240
const PAD_L = 46
const PAD_R = 16
const PAD_T = 16
const PAD_B = 46

function yAt(v: number, ymin: number, ymax: number): number {
  if (ymax <= ymin) return H - PAD_B
  return H - PAD_B - ((H - PAD_T - PAD_B) * (v - ymin)) / (ymax - ymin)
}

/** 'YYYY-MM-DD' → 天序号。取 UTC 正午以规避夏令时造成的 ±1 天偏移。 */
function dayNum(s: string): number {
  const [y, m, d] = s.split('-').map(Number)
  return Date.UTC(y || 1970, (m || 1) - 1, d || 1, 12) / 86400000
}

/**
 * 横轴定位：按自然日排布，不按记录序号。
 *
 * 日桶仅存在于有聊天记录的日期；按序号排布会把中断期压缩成等距点，
 * 而沉默期本身是高价值信号，在图上必须表现为可见缺口。
 */
const axis = computed(() => {
  const bs = current.value?.buckets ?? []
  if (!bs.length) return null
  const first = dayNum(bs[0].day)
  const last = dayNum(bs[bs.length - 1].day)
  const days = last - first
  if (days <= 0) {
    // 单日无间距可言：置于水平中点，而非按 days=1 摊开
    const mid = (PAD_L + W - PAD_R) / 2
    return { bs, first, last, days: 0, step: W - PAD_L - PAD_R, xOf: () => mid }
  }
  // 每日一格、两侧各留半格：柱/色块以日期为中心，末条不会越出画布
  const step = (W - PAD_L - PAD_R) / (days + 1)
  return {
    bs, first, last, days, step,
    xOf: (d: string) => PAD_L + step * 0.5 + (dayNum(d) - first) * step,
  }
})

/** 折线路径：日期不连续处断开（M 起新段），禁止跨越空白期硬连。 */
function lineD(xOf: (d: string) => number, yOf: (b: any) => number | null): string {
  const bs = axis.value?.bs ?? []
  let d = ''
  let prev: number | null = null
  for (const b of bs) {
    const y = yOf(b)
    const n = dayNum(b.day)
    if (y == null) { prev = null; continue }
    d += `${prev != null && n - prev <= 1 ? 'L' : 'M'}${xOf(b.day).toFixed(1)},${y.toFixed(1)} `
    prev = n
  }
  return d.trim()
}

/** 孤立单日补画数据点：单点路径在 SVG 中不可见，稀疏记录须以离散点呈现。 */
function dots(xOf: (d: string) => number, yOf: (b: any) => number | null,
              color: string, r = 2.6): string {
  const bs = axis.value?.bs ?? []
  const out: string[] = []
  for (let i = 0; i < bs.length; i++) {
    const y = yOf(bs[i])
    if (y == null) continue
    const n = dayNum(bs[i].day)
    const p = i > 0 ? dayNum(bs[i - 1].day) : null
    const q = i < bs.length - 1 ? dayNum(bs[i + 1].day) : null
    const isolated = (p == null || n - p > 1) && (q == null || q - n > 1)
    if (isolated) {
      out.push(`<circle cx="${xOf(bs[i].day).toFixed(1)}" cy="${y.toFixed(1)}" r="${r}" fill="${color}"/>`)
    }
  }
  return out.join('')
}

/** 每日负面情绪程度与负面用词程度折线图。 */
const moodChart = computed(() => {
  const ax = axis.value
  if (!ax) return ''
  const out: string[] = []
  for (let i = 0; i <= 4; i++) {
    const v = i / 4
    const y = yAt(v, 0, 1)
    out.push(
      `<line x1="${PAD_L}" y1="${y.toFixed(1)}" x2="${W - PAD_R}" y2="${y.toFixed(1)}" stroke="var(--c-border)" stroke-width="1"/>`,
      `<text x="${PAD_L - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="var(--c-text-3)">${(v * 100).toFixed(0)}%</text>`,
    )
  }
  const negY = (b: any) => (b.neg_avg == null ? null : yAt(b.neg_avg, 0, 1))
  const lexY = (b: any) => (b.lex_avg == null ? null : yAt(b.lex_avg, 0, 1))
  const neg = lineD(ax.xOf, negY)
  const lex = lineD(ax.xOf, lexY)
  // 日期轴按自然日定位，标签抽稀至多 8 个即可避免两端拥挤
  const every = Math.max(1, Math.ceil(ax.bs.length / 8))
  const labels = ax.bs
    .map((d, i) => (i % every === 0
      ? `<text x="${ax.xOf(d.day).toFixed(1)}" y="${H - PAD_B + 16}" text-anchor="middle" font-size="10.5" fill="var(--c-text-3)">${d.day.slice(5)}</text>`
      : ''))
    .join('')
  return out.join('')
    + `<path d="${neg}" fill="none" stroke="#3b82f6" stroke-width="2.2" stroke-linejoin="round"/>`
    + `<path d="${lex}" fill="none" stroke="#a855f7" stroke-width="2.2" stroke-dasharray="5,4" stroke-linejoin="round"/>`
    + dots(ax.xOf, negY, '#3b82f6')
    + dots(ax.xOf, lexY, '#a855f7', 2.2)
    + `<line x1="${PAD_L}" y1="${H - PAD_B}" x2="${W - PAD_R}" y2="${H - PAD_B}" stroke="var(--c-text-3)" stroke-width="1"/>`
    + labels
})

/** 每日聊天轮数柱状图与深夜活跃折线。 */
const talkChart = computed(() => {
  const ax = axis.value
  if (!ax) return ''
  const cmax = Math.max(1, ...ax.bs.map((d) => d.count || 0))
  const out: string[] = []
  // 刻度抽稀：逐 1 递增在峰值大、画布矮时不可读
  const tickStep = Math.max(1, Math.ceil(cmax / 4))
  const ticks: number[] = []
  for (let v = 0; v <= cmax; v += tickStep) ticks.push(v)
  if (ticks[ticks.length - 1] !== cmax) ticks.push(cmax)
  for (const v of ticks) {
    const y = yAt(v, 0, cmax)
    out.push(
      `<line x1="${PAD_L}" y1="${y.toFixed(1)}" x2="${W - PAD_R}" y2="${y.toFixed(1)}" stroke="var(--c-border)" stroke-width="1"/>`,
      `<text x="${PAD_L - 8}" y="${(y + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="var(--c-text-3)">${v}</text>`,
    )
  }
  ax.bs.forEach((d) => {
    const h = ((d.count || 0) / cmax) * (H - PAD_T - PAD_B)
    // 宽度封顶 48：单日记录时 step 等于画布宽，不封顶会渲染为巨型柱
    const bw = Math.max(2, Math.min(ax.step * 0.6, 48))
    const x = ax.xOf(d.day) - bw / 2
    out.push(`<rect x="${x.toFixed(1)}" y="${(H - PAD_B - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="#0ea5e9" opacity="0.5"><title>${d.day}：${d.count} 轮对话</title></rect>`)
  })
  const night = lineD(ax.xOf, (b) => yAt(b.night_count || 0, 0, cmax))
  out.push(`<path d="${night}" fill="none" stroke="#f97316" stroke-width="1.8" stroke-dasharray="4,3"/>`)
  return out.join('')
})

/** 每日守护档位色带。 */
const tierBand = computed(() => {
  const ax = axis.value
  const rp = current.value?.guard.replay ?? []
  if (!rp.length || !ax) return ''
  return rp.map((r) => {
    const c = TIER_COLOR[r.tier] ?? '#94a3b8'
    // 色块以日期为中心、宽一格，块间仅留 0.6px 缝；缩窄块宽会使连续两日视觉上断开
    const x = ax.xOf(r.day) - ax.step * 0.5
    const bw = Math.max(1, Math.min(ax.step - 0.6, 48))
    return `<rect x="${x.toFixed(1)}" y="4" width="${bw.toFixed(1)}" height="16" rx="2" fill="${c}"><title>${r.day}：${TIER_TEXT[r.tier] ?? r.tier}</title></rect>`
  }).join('')
})

/* ============================ 展示工具 ============================ */
/** 0-1 分数 → 程度词。 */
function level(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v < 0.15) return '很轻'
  if (v < 0.3) return '较轻'
  if (v < 0.45) return '中等'
  if (v < 0.6) return '偏重'
  return '很重'
}
function pct(v: number | null | undefined): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}
function shortDate(s: string | null): string {
  return s ? s.slice(5, 10).replace('-', '/') : '—'
}

// 分道配色：档案（不变）/ 状态（可变）/ 事件（带日期），三者均为时间性质而非对用户的判定
function laneColor(lane?: string): string {
  if (lane === 'event') return '#4c728e'
  if (lane === 'state') return '#d97706'
  return 'var(--c-text-3)'
}
/** 落选原因 → 展示文案：说明该条为何未进入本次上下文。 */
function dropReasonZh(reason?: string | null): string {
  const m: Record<string, string> = {
    budget: '配额被更高相关度条目占据',
    state_lane_cap: '同类条目达到分道上限',
    duplicate_in_context: '与已选条目语义重复',
    cap: '达到条数上限',
    not_reached: '排序靠后未入选',
  }
  return m[reason ?? ''] ?? '未入选'
}

/** 顶部统计卡。danger 项非 0 时转红。 */
const statCards = computed(() => {
  const t = current.value?.totals
  if (!t) return []
  return [
    { label: '有记录天数', value: t.day_buckets, danger: false },
    { label: '对话轮次', value: t.turns, danger: false },
    { label: '事实条目', value: t.fact_active, danger: false },
    { label: '片段条目', value: t.episode_active, danger: false },
    { label: '风险原话条数', value: t.risk_event_count, danger: true },
  ]
})

/** 守护层综合分。 */
const gscore = computed(() => current.value?.guard.score ?? null)

/**
 * 守护层各独立证据路径的亮起状态与展示文案。
 *
 * 各路径独立打分、取最亮者，不要求多路径同时成立；`on` 表示该路径自身达标，
 * 与其他路径无关。短窗路径（近 3 天）上限仅推到 watch 档，长窗路径方可推到 elevated。
 */
const watchItems = computed(() => {
  const v = gscore.value
  if (!v?.scores) return []
  const s = v.scores
  const d = (v.detail ?? {}) as Record<string, any>
  const fam = (v.families ?? {}) as Record<string, number | null>
  const FAM: Record<string, string> = {
    withdraw: '社交回避',
    conflict: '易怒冲突',
    anhedonia: '兴趣减退',
    somatic: '躯体与作息紊乱',
  }
  const kind: string | null = d.behavior_kind ?? null
  const delta = d.delta as number | null | undefined
  const eff = d.effect_d as number | null | undefined
  const nHist = Number(d.chronic_n_hist ?? 0)
  // 密度分母为 max(5, 近窗有记录天数)，非固定 7 天
  const famSpan = Number(v.family_span ?? 0) || 7
  const famDays = kind ? Math.round((fam[kind] ?? 0) * famSpan) : 0

  return [
    {
      // 变化分：位移 + 效应量
      name: '近一周情绪相对个人基线的偏移',
      on: (s.s_change ?? 0) >= 0.35,
      detail: delta == null
        ? '历史样本不足，个人基线未建立，本项暂不比较。'
        : (s.s_change ?? 0) >= 0.35
          ? `近一周均值低于个人基线${eff != null && eff >= 1
            ? `，幅度达日常波动幅度的 ${Number(eff).toFixed(1)} 倍`
            : ''}。`
          : '近一周均值与个人基线基本一致。',
    },
    {
      // 快尺度：近 3 天窗口，样本过短故分值上限压至 watch 档
      name: '近 72 小时快速恶化',
      on: (s.s_fast ?? 0) >= 0.40,
      detail: (s.s_fast ?? 0) >= 0.40
        ? '近 3 天内 2 天以上显著低于基线，属持续性突变而非单日波动。'
        : '近 3 天未出现连续显著走低。',
    },
    {
      // 慢性分：与快尺度互斥，快尺度生效期间本路径被主动衰减，避免掩盖近期突变
      name: '长期低位',
      on: (s.s_chronic ?? 0) >= 0.35,
      detail: (s.s_chronic ?? 0) >= 0.35
        ? '整体情绪持续处于低位，非短期波动。'
        : (nHist < 14
          ? `样本仅 ${nHist} 天，长期趋势暂不可判。`
          : '整体情绪未长期处于低位。'),
    },
    {
      // 症状族：四类表述与情绪词表互不重叠，构成独立证据；单独达标最高推至 watch 档
      name: '行为症状族变化（回避/冲突/兴趣减退/躯体不适）',
      on: (s.s_behavior ?? 0) >= 0.40,
      detail: (s.s_behavior ?? 0) >= 0.40 && kind
        ? (famDays >= 1
          ? `近 ${famSpan} 天内「${FAM[kind] ?? kind}」类表现出现约 ${famDays} 天。`
          : `近几日出现「${FAM[kind] ?? kind}」类表现。`)
        : '未观察到持续行为变化。',
    },
    {
      // 维度分：近一周用户明确表达且达到"明显"阈值的困扰项计数
      name: '用户自述困扰维度',
      on: (s.s_dimension ?? 0) >= 0.40,
      detail: (s.s_dimension ?? 0) >= 0.40
        ? `近一周明确提及 ${Number(d.dim_n_counted ?? 0)} 个维度。`
        : '近一周未明确提及。',
    },
    {
      // 前驱分：参与度塌陷 / 作息后移经变点检测确认；仅使用行为数据，不涉及内容
      name: '前驱信号（参与度塌陷 / 作息后移）',
      on: (s.s_precursor ?? 0) >= 0.40,
      detail: (s.s_precursor ?? 0) >= 0.40
        ? (() => {
          const cp = d.activity_cp as { p_break?: number; shift?: number } | null | undefined
          if (cp && typeof cp.shift === 'number' && cp.shift < 0) {
            return '聊天频率显著下降并已持续数日，非单日波动。'
          }
          return '参与频率或活跃时段出现持续性变化。'
        })()
        : '未观察到持续性参与度变化。',
    },
  ]
})

/** 守护层是否处于弃权状态（数据不足，不作判定）。 */
const isAbstain = computed(() => current.value?.guard.data_sufficient === false)

/** 弃权状态下的说明文案及其数据依据。 */
const dataHint = computed(() => {
  const g = current.value?.guard
  if (!g || g.data_sufficient) return null
  const b = g.data_basis
  if (b?.basis === 'recent') {
    if (!b.recent_n_days && !b.days_total) {
      return {
        title: '无聊天记录',
        body: '尚无日桶数据，无法判定。',
        notes: [],
      }
    }
    return {
      title: '近期样本不足，暂不判定',
      body: `近 7 天仅 ${b.recent_n_days ?? 0} 天有记录，低于最低要求 ${b.min_recent_days ?? 2} 天，`
        + '无法与个人基线比较。',
      notes: [
        '数据缺失不判为好转或恶化：不足样本按弃权处理，不计 0 分。',
        `历史共留存 ${b.days_total ?? 0} 天记录，近期活跃度下降。`,
      ],
    }
  }
})

/**
 * 档位停留说明。仅在落盘档位 ≠ 瞬时档时给出。
 *
 * 面板并存三种口径：徽章（落盘档位，含停留期与保护期）、色带（每日独立）、
 * 卡片（当前瞬时分数）。三者不一致时必须给出解释，否则展示自相矛盾。
 * 沉默与数据不足各有独立说明块，此处不重复。
 */
const tierHold = computed(() => {
  const g = current.value?.guard
  if (!g || !g.available || g.tier === g.tier_instant) return null
  const mode = String(g.mode ?? '')
  const reason = String(g.tier_reason ?? '')
  if (mode === 'lapse' || mode === 'lapse_decay' || mode === 'insufficient') return null
  if (reason === 'crisis_protected') {
    return {
      title: '危机保护期：72 小时内档位只升不降',
      body: '危机轮次被排除在情绪趋势统计之外（避免趋势被危机掩盖），保护期作为独立补偿层存在。',
    }
  }
  if (reason === 'max_stay_decay') {
    return {
      title: '单档停留届满，按规则回落',
      body: '单档最长停留 28 天，无新增升档证据时降一档，避免永久标记。',
    }
  }
  return {
    title: '瞬时分数已回落，档位处于确认期',
    body: '升档即时生效、降档需确认期：误降档的代价高于多保留一档关注。',
  }
})

/** 有日桶才渲染图表：无数据时三张图会渲染成空框，视觉上如同故障。 */
const hasBuckets = computed(() => (current.value?.buckets ?? []).length > 0)

/** 风险标记展示形态：内部档位 → 文案与配色，附起始时间。 */
const riskMark = computed(() => {
  const st = current.value?.risk_state
  if (!st) return null
  // since 缺失或非法时（旧状态文件 / 部分清除）不渲染时间，
  // 避免 new Date(NaN) 产出 "NaN/NaN/NaN" 文本
  const sec = Number(st.since)
  const ok = Number.isFinite(sec) && sec > 0
  const since = ok ? new Date(sec * 1000) : null
  const days = since
    ? Math.max(0, Math.floor((Date.now() - since.getTime()) / 86400000))
    : null
  return {
    tier: st.tier,
    text: TIER_TEXT[st.tier] ?? st.tier,
    color: TIER_COLOR[st.tier] ?? '#64748b',
    sinceText: since
      ? `${since.getFullYear()}/${since.getMonth() + 1}/${since.getDate()}`
      : null,
    days,
  }
})

/** 标记跃迁历史（仅档位变化时落盘，条目稀疏）。 */
const riskTimeline = computed(() => {
  const h = current.value?.risk_history ?? []
  return h
    .map((e) => {
      const sec = Number(e.ts)
      const ok = Number.isFinite(sec) && sec > 0
      const date = ok ? new Date(sec * 1000) : null
      return {
        date,
        dateText: date ? `${date.getMonth() + 1}/${date.getDate()}` : '—',
        from: e.from ? (TIER_TEXT[e.from] ?? e.from) : '未标记',
        to: TIER_TEXT[e.to] ?? e.to,
        color: TIER_COLOR[e.to] ?? '#64748b',
      }
    })
})

/** 事实重复度：本次配额内语义重复条目的粗估（前 10 字归一化分组）。 */
const dupInfo = computed(() => {
  const facts = current.value?.facts.filter((f) => f.in_reply_quota) ?? []
  const key = (t: string) => t.replace(/[，。、,.\s]/g, '').slice(0, 10)
  const groups = new Map<string, number>()
  for (const f of facts) groups.set(key(f.text), (groups.get(key(f.text)) ?? 0) + 1)
  let dup = 0
  for (const c of groups.values()) if (c > 1) dup += c - 1
  return { total: facts.length, dup }
})
</script>

<template>
  <section>
    <!-- 头部 -->
    <div class="mb-3 flex flex-wrap items-center gap-3">
      <h2 class="text-[16px] font-semibold" style="color: var(--c-text)">记忆洞察（只读）</h2>
      <span v-if="data" class="text-[11px]" style="color: var(--c-text-3)">
        数据更新于 {{ data.generated_at }}
      </span>
      <button
        class="ml-auto flex items-center gap-1 text-[12px] ui-hover"
        style="color: var(--c-text-3)"
        :disabled="loading"
        @click="load"
      >
        <Icon name="refresh" :size="13" :class="{ 'animate-spin': loading }" />
        刷新
      </button>
    </div>

    <p class="mb-4 max-w-[780px] text-[12.5px] leading-relaxed" style="color: var(--c-text-2)">
      本页展示记忆沉淀结果、读侧配额命中情况与守护层判定依据，用于内部核对与评审。
      全部数据仅作观察，<b>不会进入对话内容</b>。
    </p>

    <!-- 多用户切换 -->
    <div v-if="data && data.users.length > 1" class="mb-3 flex flex-wrap gap-1.5">
      <button
        v-for="u in data.users" :key="u.user"
        class="rounded-full border px-3 py-1 text-[12px]"
        :style="{
          borderColor: activeUser === u.user ? 'var(--c-accent)' : 'var(--c-border)',
          color: activeUser === u.user ? 'var(--c-accent)' : 'var(--c-text-2)',
          background: activeUser === u.user ? 'var(--c-accent-soft)' : 'transparent',
        }"
        @click="activeUser = u.user"
      >{{ u.user }}</button>
    </div>

    <div
      v-if="error"
      class="mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-[12px]"
      style="border-color: #e5484d; color: #e5484d; background: var(--c-input)"
    >
      <Icon name="alert" :size="14" class="mt-0.5 shrink-0" />
      <span>{{ error }}</span>
    </div>

    <div v-if="current" class="space-y-4 pb-16">
      <!-- ===== 1. 一眼看明白 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div
          class="mb-3 flex flex-wrap items-center gap-3 rounded-lg px-3.5 py-3"
          :style="{ background: (TIER_COLOR[current.guard.tier] ?? '#64748b') + '14' }"
        >
          <span
            class="rounded-full px-2.5 py-1 text-[12px] font-semibold text-white"
            :style="{ background: TIER_COLOR[current.guard.tier] ?? '#64748b' }"
          >{{ TIER_TEXT[current.guard.tier] ?? current.guard.tier }}</span>
          <span class="text-[12px] leading-relaxed" style="color: var(--c-text-2)">
            <template v-if="current.guard.tier === 'stable'">
              未发现异常信号，交互策略保持默认。
            </template>
            <template v-else-if="current.guard.tier === 'watch'">
              存在个别值得关注信号，回复语气放缓、主动关怀频率提高。
            </template>
            <template v-else-if="current.guard.tier === 'elevated'">
              多项信号并发，回复节奏明显放缓、陪伴强度提升。
            </template>
            <template v-else>
              样本不足，暂不判定。
            </template>
          </span>
        </div>

        <div class="grid grid-cols-2 gap-2 md:grid-cols-5">
          <div
            v-for="c in statCards" :key="c.label"
            class="rounded-lg px-3 py-2.5" style="background: var(--c-input)"
          >
            <div class="font-mono text-[17px]"
                 :style="{ color: c.danger ? (c.value ? '#dc2626' : 'var(--c-text-3)') : 'var(--c-accent)' }"
            >{{ c.value }}</div>
            <div class="text-[11px]" style="color: var(--c-text-3)">{{ c.label }}</div>
          </div>
        </div>

        <div v-if="current.trend.recent_summary" class="mt-3 rounded-lg px-3 py-2.5" style="background: var(--c-input)">
          <div class="mb-1 text-[11px]" style="color: var(--c-text-3)">
            读侧近期摘要（当前注入上下文）
          </div>
          <div class="text-[12px] leading-relaxed" style="color: var(--c-text-2)">
            {{ current.trend.recent_summary }}
          </div>
        </div>

        <div class="mt-2 text-[11px]" style="color: var(--c-text-3)">
          记录时间：{{ current.totals.first_day }} 到 {{ current.totals.last_day }}
        </div>
      </div>

      <!-- ===== 1.5 心理风险标记 ===== -->
      <div
        v-if="riskMark"
        class="rounded-xl border p-4"
        :style="{ borderColor: riskMark.color + '66', background: riskMark.color + '0d' }"
      >
        <div class="mb-2 flex flex-wrap items-center gap-3">
          <span class="text-[13.5px] font-medium" style="color: var(--c-text-2)">心理风险标记</span>
          <span
            class="rounded-full px-2.5 py-1 text-[12px] font-semibold text-white"
            :style="{ background: riskMark.color }"
          >{{ riskMark.text }}</span>
          <span class="text-[11.5px]" style="color: var(--c-text-3)">
            <template v-if="riskMark.sinceText">
              自 {{ riskMark.sinceText }} 起<template v-if="(riskMark.days ?? 0) > 0">（已 {{ riskMark.days }} 天）</template>
            </template>
            <template v-else>开始时间未知（状态文件较早，缺少时间戳）</template>
          </span>
        </div>

        <p class="mb-2.5 text-[12px] leading-relaxed" style="color: var(--c-text-2)">
          该标记仅存于本机，用于调整桌宠陪伴策略（语气放缓、关怀增强、主动度提高），
          <b>未接入任何外部功能</b>。
        </p>

        <div class="mb-2 rounded-lg px-3 py-2.5" style="background: var(--c-input)">
          <div class="mb-1 text-[11.5px] font-medium" style="color: var(--c-text-2)">
            设计边界
          </div>
          <ul class="space-y-0.5 text-[11.5px] leading-relaxed" style="color: var(--c-text-3)">
            <li>· <b>不通知第三方</b>：不联系辅导员、家长或任何外部对象，不推送、不生成对外报告。</li>
            <li>· <b>对用户不可见</b>：用户无法看到该标记与任何分数。</li>
            <li>· <b>不改危机协议</b>：对话中出现明确危险表达时，仍走前台固定热线协议，与本标记无关。</li>
            <li>· <b>不主动提问</b>：标记仅影响语气，不产生量表或问句。</li>
          </ul>
        </div>

        <div v-if="riskTimeline.length > 1" class="rounded-lg px-3 py-2.5" style="background: var(--c-input)">
          <div class="mb-1.5 text-[11.5px] font-medium" style="color: var(--c-text-2)">
            标记变化历史（仅档位变化时落盘）
          </div>
          <div class="space-y-1">
            <div
              v-for="(t, i) in riskTimeline" :key="i"
              class="flex items-center gap-2 text-[11.5px]"
            >
              <span class="font-mono text-[10.5px]" style="color: var(--c-text-3)">
                {{ t.dateText }}
              </span>
              <span style="color: var(--c-text-3)">{{ t.from }}</span>
              <span style="color: var(--c-text-3)">→</span>
              <span class="rounded px-1.5 py-0.5 font-medium text-white"
                    :style="{ background: t.color }">{{ t.to }}</span>
            </div>
          </div>
        </div>
        <div v-else class="text-[11.5px]" style="color: var(--c-text-3)">
          该标记建立以来未发生变化。
        </div>
      </div>

      <!-- ===== 2. 心情变化 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-1 text-[13.5px] font-medium" style="color: var(--c-text-2)">逐日情绪曲线</div>
        <p class="mb-2 text-[12px] leading-relaxed" style="color: var(--c-text-3)">
          两线取值越高越消极：蓝色为逐日整体情绪强度，紫色虚线为逐日负面词汇占比。
          两线同步上行通常指示持续性低落。
        </p>
        <div class="mb-1 flex flex-wrap gap-4 text-[11.5px]" style="color: var(--c-text-3)">
          <span><i class="mr-1 inline-block h-[3px] w-4 rounded align-middle" style="background:#3b82f6"></i>整体情绪</span>
          <span><i class="mr-1 inline-block h-[3px] w-4 rounded align-middle" style="background:#a855f7"></i>负面用词（虚线）</span>
        </div>
        <svg v-if="hasBuckets" :viewBox="`0 0 ${W} ${H}`" class="w-full" role="img" v-html="moodChart" />
        <div v-else class="rounded-lg px-3 py-2 text-[11.5px]" style="background: var(--c-input); color: var(--c-text-3)">
          暂无日桶数据，无法绘制。
        </div>
      </div>

      <!-- ===== 3. 聊天频率 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-1 text-[13.5px] font-medium" style="color: var(--c-text-2)">参与度曲线</div>
        <p class="mb-2 text-[12px] leading-relaxed" style="color: var(--c-text-3)">
          柱状为逐日对话轮数，橙色虚线为 0–6 时轮数。
          参与度骤降或深夜活跃上升均为关注信号。
        </p>
        <svg v-if="hasBuckets" :viewBox="`0 0 ${W} ${H}`" class="w-full" role="img" v-html="talkChart" />
        <div v-else class="rounded-lg px-3 py-2 text-[11.5px]" style="background: var(--c-input); color: var(--c-text-3)">
          暂无日桶数据，无法绘制。
        </div>
      </div>

      <!-- ===== 4. 是否要更关心一点 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <span class="text-[13.5px] font-medium" style="color: var(--c-text-2)">守护层判定依据</span>
          <span
            class="rounded-full px-2 py-0.5 text-[11px] font-semibold text-white"
            :style="{ background: TIER_COLOR[current.guard.tier] ?? '#64748b' }"
          >{{ TIER_TEXT[current.guard.tier] ?? current.guard.tier }}</span>
        </div>

        <p class="mb-3 text-[12px] leading-relaxed" style="color: var(--c-text-3)">
          六条证据路径独立打分、取最亮者定档，不要求多路径同时成立——
          「自述平稳但行为与身体先行变化」是此类风险的常见形态。
          灵敏度不对称：短窗路径（近 3 天）上限仅推至「值得关注」，
          推至「重点关注」需更长窗口证据（周级变化、长期低位或持续行为变化）。
          判定仅决定陪伴强度，不构成任何诊断。
        </p>

        <div v-if="current.guard.mode === 'lapse' || current.guard.mode === 'lapse_decay'" class="mb-3 rounded-lg px-3 py-2.5"
             style="background: var(--c-input)">
          <div class="text-[12px] font-medium" style="color: var(--c-text-2)">
            沉默期处理
          </div>
          <div class="mt-0.5 text-[11.5px] leading-relaxed" style="color: var(--c-text-3)">
            沉默对应状态平稳或无法交流两种可能，无法区分，因此档位不因沉默下调，
            仅随时间线性衰减，用户恢复活跃后以近期数据重新评估。
            <template v-if="current.guard.mode === 'lapse_decay'">
              已超出最长沉默容忍期，当前按规则逐档回落。
            </template>
          </div>
        </div>

        <!-- 落盘档位与瞬时分数不一致时必须解释，否则徽章与下方卡片互相矛盾 -->
        <div v-if="tierHold" class="mb-3 rounded-lg px-3 py-2.5" style="background: var(--c-input)">
          <div class="text-[12px] font-medium" style="color: var(--c-text-2)">{{ tierHold.title }}</div>
          <div class="mt-0.5 text-[11.5px] leading-relaxed" style="color: var(--c-text-3)">
            {{ tierHold.body }}
            <template v-if="current.guard.mode === 'normal' && current.guard.tier_instant">
              （按当前分数单独看是「{{ TIER_TEXT[current.guard.tier_instant] ?? current.guard.tier_instant }}」）
            </template>
          </div>
        </div>

        <div v-if="dataHint" class="mb-3 rounded-lg px-3 py-2.5"
             style="background: #fffbeb; color: #92400e">
          <div class="text-[12px] font-medium">{{ dataHint.title }}</div>
          <div class="mt-0.5 text-[11.5px] leading-relaxed">{{ dataHint.body }}</div>
          <ul v-if="dataHint.notes.length" class="mt-1 space-y-0.5 text-[11px] leading-relaxed" style="opacity: .85">
            <li v-for="(n, i) in dataHint.notes" :key="i">· {{ n }}</li>
          </ul>
        </div>

        <div v-if="watchItems.length && !isAbstain" class="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div
            v-for="w in watchItems" :key="w.name"
            class="rounded-lg border px-3 py-2.5"
            :style="{
              borderColor: w.on ? '#dc2626' : 'var(--c-border)',
              background: w.on ? 'rgba(220,38,38,.06)' : 'var(--c-input)',
            }"
          >
            <div class="mb-1 flex items-center gap-2">
              <span
                class="inline-block h-2 w-2 shrink-0 rounded-full"
                :style="{ background: w.on ? '#dc2626' : '#cbd5e1' }"
              />
              <span class="text-[12.5px] font-medium" style="color: var(--c-text)">{{ w.name }}</span>
            </div>
            <div class="text-[11.5px] leading-relaxed" style="color: var(--c-text-2)">{{ w.detail }}</div>
          </div>
        </div>
        <!-- 弃权时不渲染卡片：卡片文案均为"无异常"措辞，将未判定渲染为正常 -->
        <div v-else-if="isAbstain" class="rounded-lg px-3 py-2.5 text-[11.5px] leading-relaxed"
             style="background: var(--c-input); color: var(--c-text-3)">
          数据不足，各证据路径暂不可判；样本恢复后自动重新评估。
        </div>

        <div class="mt-3">
          <div class="mb-1.5 text-[11.5px]" style="color: var(--c-text-3)">
            逐日独立档位（不含停留期与保护期；横轴为自然日，空白为无记录日）：
          </div>
          <svg v-if="tierBand" :viewBox="`0 0 ${W} 26`" class="w-full" role="img" v-html="tierBand" />
          <div v-else class="text-[11.5px]" style="color: var(--c-text-3)">暂无逐日记录。</div>
          <div class="mt-2 flex flex-wrap gap-4 text-[11px]" style="color: var(--c-text-3)">
            <span><i class="mr-1 inline-block h-2.5 w-4 rounded align-middle" style="background:#16a34a"></i>未见异常</span>
            <span><i class="mr-1 inline-block h-2.5 w-4 rounded align-middle" style="background:#f59e0b"></i>值得关注</span>
            <span><i class="mr-1 inline-block h-2.5 w-4 rounded align-middle" style="background:#dc2626"></i>重点关注</span>
            <span><i class="mr-1 inline-block h-2.5 w-4 rounded align-middle" style="background:#64748b"></i>数据不足</span>
          </div>
        </div>

              </div>

      <!-- ===== 5. 聊天时实际会想起什么 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-2 flex flex-wrap items-center gap-3">
          <span class="text-[13.5px] font-medium" style="color: var(--c-text-2)">读侧上下文预览</span>
          <span class="font-mono text-[11px]" style="color: var(--c-text-3)">{{ current.context_preview.reply_chars }} 字</span>
          <button class="text-[12px] ui-hover" style="color: var(--c-accent)" @click="openContext = !openContext">
            {{ openContext ? '收起' : '展开' }}
          </button>
        </div>
        <p class="mb-2 text-[12px] leading-relaxed" style="color: var(--c-text-3)">
          每次回复前实际注入的记忆内容。单次配额为事实
          {{ current.retrieve_budget.facts }} 条、片段
          {{ current.retrieve_budget.episodes }} 段，按相关度优先选取。
        </p>
        <pre
          v-if="openContext"
          class="max-h-[320px] overflow-auto rounded-lg p-3 text-[11.5px] leading-relaxed"
          style="background: var(--c-input); color: var(--c-text-2)"
        >{{ current.context_preview.reply || '（暂无记忆）' }}</pre>
        <div v-else class="rounded-lg px-3 py-2 text-[11.5px] leading-relaxed"
             style="background: var(--c-input); color: var(--c-text-3)">
          {{ (current.context_preview.reply || '（暂无记忆）').slice(0, 150) }}{{ current.context_preview.reply.length > 150 ? '……' : '' }}
        </div>
      </div>

      <!-- ===== 6. 记住的事 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-2 flex flex-wrap items-center gap-3">
          <span class="text-[13.5px] font-medium" style="color: var(--c-text-2)">
            事实条目（{{ current.facts.length }} 条）
          </span>
          <button class="text-[12px] ui-hover" style="color: var(--c-accent)" @click="openFacts = !openFacts">
            {{ openFacts ? '收起' : '展开全部' }}
          </button>
        </div>

        <div
          v-if="dupInfo.dup > 0"
          class="mb-2 rounded-lg px-3 py-2"
          style="background: #fffbeb; color: #92400e"
        >
          <span class="text-[11.5px] leading-relaxed">
            本次配额 {{ dupInfo.total }} 条中，{{ dupInfo.dup }} 条为<b>语义重复</b>
            （同义改写未归并），挤占其余条目配额。语义去重为当前已知短板。
          </span>
        </div>

        <div class="space-y-1.5">
          <div
            v-for="f in (openFacts ? current.facts : current.facts.slice(0, 6))"
            :key="f.key"
            class="flex items-start gap-2.5 rounded-lg px-3 py-2"
            style="background: var(--c-input)"
          >
            <span
              class="mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10.5px]"
              :style="{
                background: f.in_reply_quota ? 'rgba(22,163,74,.14)' : 'transparent',
                color: f.in_reply_quota ? '#16a34a' : 'var(--c-text-3)',
              }"
              :title="f.in_reply_quota ? '本次回复注入' : '本次回复未注入'"
            >{{ f.in_reply_quota ? '已注入' : '未注入' }}</span>
            <div class="min-w-0 flex-1">
              <div class="text-[12.5px] leading-relaxed" style="color: var(--c-text)">{{ f.text }}</div>
              <div class="mt-0.5 text-[10.5px]" style="color: var(--c-text-3)">
                <!-- 分道标签与落选原因：保证"该条为何未命中"可核查 -->
                <template v-if="f.lane_zh">
                  <span :style="{ color: laneColor(f.lane) }">{{ f.lane_zh }}</span> · 
                </template>
                <template v-if="f.last_seen">最近提及：{{ shortDate(f.last_seen) }} · </template>
                提及次数：{{ f.mentions ?? 1 }}
                <template v-if="f.initiable"> · <span style="color:#16a34a">可主动发起</span></template>
                <template v-if="f.archived"> · 已归档</template>
                <template v-if="f.drop_reason && !f.in_reply_quota">
                  · <span style="color:#d97706">{{ dropReasonZh(f.drop_reason) }}</span>
                </template>
              </div>
            </div>
          </div>
        </div>
        <div v-if="!current.facts.length" class="text-[12px]" style="color: var(--c-text-3)">
          暂无事实条目（需数轮对话后触发整理）。
        </div>
      </div>

      <!-- ===== 7. 聊过的片段 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-2 flex flex-wrap items-center gap-3">
          <span class="text-[13.5px] font-medium" style="color: var(--c-text-2)">
            片段条目（{{ current.episodes.length }} 条）
          </span>
          <button class="text-[12px] ui-hover" style="color: var(--c-accent)" @click="openEpisodes = !openEpisodes">
            {{ openEpisodes ? '收起' : '展开全部' }}
          </button>
        </div>
        <div class="space-y-2">
          <div
            v-for="e in (openEpisodes ? current.episodes : current.episodes.slice(0, 3))"
            :key="e.key"
            class="rounded-lg px-3 py-2.5" style="background: var(--c-input)"
          >
            <div class="mb-1 flex flex-wrap items-center gap-2 text-[10.5px]" style="color: var(--c-text-3)">
              <span v-if="e.ts_end">{{ shortDate(e.ts_end) }}</span>
              <span
                v-for="t in e.topics" :key="t"
                class="rounded px-1.5 py-0.5"
                style="background: var(--c-border); color: var(--c-text-2)"
              >{{ TOPIC[t] ?? t }}</span>
              <span v-if="e.dominant_emotion">情绪：{{ EMO[e.dominant_emotion] ?? e.dominant_emotion }}</span>
              <span v-if="e.valence_arc">走向：{{ ARC[e.valence_arc] ?? e.valence_arc }}</span>
              <span v-if="e.protected" class="rounded px-1.5 py-0.5"
                    style="background: rgba(220,38,38,.14); color:#dc2626">已长期保留</span>
            </div>
            <div class="text-[12.5px] leading-relaxed" style="color: var(--c-text)">{{ e.digest }}</div>
          </div>
          <div v-if="!current.episodes.length" class="text-[12px]" style="color: var(--c-text-3)">
            暂无片段条目。
          </div>
        </div>
      </div>

      <!-- ===== 8. 值得留意的原话 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-2 text-[13.5px] font-medium" style="color: var(--c-text-2)">
          风险原话留痕（永久保留）
        </div>
        <div v-if="current.risk_events.length" class="space-y-1.5">
          <div
            v-for="(r, i) in current.risk_events" :key="i"
            class="flex flex-wrap items-center gap-2 rounded-lg px-3 py-2"
            style="background: var(--c-input)"
          >
            <span class="rounded px-1.5 py-0.5 text-[10.5px]"
                  style="background: rgba(220,38,38,.14); color:#dc2626">
              {{ FACET[r.facet ?? ''] ?? r.facet }}
            </span>
            <span class="text-[12.5px]" style="color: var(--c-text)">「{{ r.quote }}」</span>
            <span v-if="r.ts" class="text-[10.5px]" style="color: var(--c-text-3)">{{ shortDate(r.ts) }}</span>
          </div>
        </div>
        <div v-else class="text-[12px] leading-relaxed" style="color: var(--c-text-3)">
          暂无记录。风险原话仅追加、不可删除；不参与情绪曲线计算，
          也不作为其他结论的推断依据。
        </div>
      </div>

      <!-- ===== 9. 聊天涉及的话题 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-2 text-[13.5px] font-medium" style="color: var(--c-text-2)">话题覆盖度</div>
        <p class="mb-2.5 text-[12px] leading-relaxed" style="color: var(--c-text-3)">
          统计各话题维度的覆盖情况。未覆盖维度仅作为低强度闲聊候选，
          <b>不构成追问，也不产生量表提问</b>。
        </p>
        <div class="grid grid-cols-2 gap-1.5 md:grid-cols-3">
          <div
            v-for="(dn, k) in current.dimension_ledger.dim_names" :key="k"
            class="flex items-center gap-2 rounded-lg px-2.5 py-1.5"
            :style="{
              background: current.dimension_ledger.covered[k] ? 'rgba(22,163,74,.08)' : 'var(--c-input)',
            }"
          >
            <span
              class="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
              :style="{ background: current.dimension_ledger.covered[k] ? '#16a34a' : '#cbd5e1' }"
            />
            <span class="min-w-0 flex-1 truncate text-[12px]" style="color: var(--c-text-2)">{{ dn }}</span>
            <span v-if="current.dimension_ledger.covered[k]" class="text-[10.5px]"
                  style="color: var(--c-text-3)">
              聊过 {{ current.dimension_ledger.covered[k].count }} 次
            </span>
            <span v-else class="text-[10.5px]" style="color: var(--c-text-3)">未覆盖</span>
          </div>
        </div>
      </div>

      <!-- ===== 10. 用户画像 ===== -->
      <div class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <div class="mb-2 text-[13.5px] font-medium" style="color: var(--c-text-2)">用户画像</div>
        <div v-if="current.profile.trait_card" class="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div class="rounded-lg px-3 py-2.5" style="background: var(--c-input)">
            <div class="mb-1.5 text-[11px]" style="color: var(--c-text-3)">
              依据：{{ current.profile.trait_card.evidence.days }} 天、
              {{ current.profile.trait_card.evidence.turn_count }} 轮聊天
            </div>
            <div class="space-y-1 text-[12.5px]" style="color: var(--c-text-2)">
              <div>整体心情：{{ current.profile.trait_card.traits.baseline_mood }}</div>
              <div>情绪起伏：{{ current.profile.trait_card.traits.stability }}</div>
              <div>负面情绪占比：{{ current.profile.trait_card.traits.negative_tendency }}</div>
              <div>社交话题：{{ current.profile.trait_card.traits.social_orientation }}</div>
            </div>
          </div>
          <div class="rounded-lg px-3 py-2.5" style="background: var(--c-input)">
            <div class="mb-1.5 text-[11px]" style="color: var(--c-text-3)">
              话题 → 情绪下行关联（仅观察，不据此主动发起话题）
            </div>
            <div v-if="current.profile.trigger_mining.length" class="space-y-1.5">
              <div v-for="t in current.profile.trigger_mining" :key="t.topic" class="flex items-center gap-2">
                <span class="w-[64px] shrink-0 text-[12px]" style="color: var(--c-text-2)">
                  {{ TOPIC[t.topic] ?? t.topic_zh }}
                </span>
                <span class="h-1.5 flex-1 overflow-hidden rounded-full" style="background: var(--c-border)">
                  <span class="block h-full rounded-full" style="background:#f97316"
                        :style="{ width: `${Math.round(t.rate * 100)}%` }" />
                </span>
                <span class="w-[86px] shrink-0 text-right text-[10.5px]" style="color: var(--c-text-3)">
                  {{ t.n_neg }}/{{ t.n_total }} 次偏低落
                </span>
              </div>
            </div>
            <div v-else class="text-[12px]" style="color: var(--c-text-3)">样本不足。</div>
          </div>
        </div>
        <div v-else class="text-[12px]" style="color: var(--c-text-3)">样本不足，画像未生成。</div>
      </div>

      <!-- ===== 11. 正在整理中的对话 ===== -->
      <details class="rounded-xl border p-4" style="border-color: var(--c-border)">
        <summary class="cursor-pointer text-[13.5px] font-medium" style="color: var(--c-text-2)">
          正在整理中的对话（{{ current.pending.turn_count }} 轮）
        </summary>
        <p class="mb-2 mt-2 text-[12px] leading-relaxed" style="color: var(--c-text-3)">
          新对话先进入待固化队列，达到
          {{ current.pending.min_turns_threshold }} 轮、或静默
          {{ Math.round(current.pending.idle_seconds_threshold / 60) }} 分钟后，
          整理为事实与片段。整理完成后原话不再保留，本区为原话唯一可见位置。
        </p>
        <div v-if="current.pending.turns.length" class="max-h-[240px] space-y-1.5 overflow-auto">
          <div
            v-for="(t, i) in current.pending.turns" :key="i"
            class="rounded-lg px-3 py-2" style="background: var(--c-input)"
          >
            <div class="text-[12.5px]" style="color: var(--c-text)">{{ t.user }}</div>
            <div class="mt-0.5 text-[10.5px]" style="color: var(--c-text-3)">
              {{ t.ts }}
              <template v-if="t.emotion"> · 情绪 {{ EMO[t.emotion] ?? t.emotion }}</template>
              <template v-if="t.negative_score != null"> · 程度{{ level(t.negative_score) }}</template>
              <template v-if="t.lex_neg != null"> · 负面用词{{ level(t.lex_neg) }}</template>
            </div>
          </div>
        </div>
        <div v-else class="text-[12px]" style="color: var(--c-text-3)">队列为空。</div>
      </details>

      <p class="text-[11px] leading-relaxed" style="color: var(--c-text-3)">
        本页只读，无修改与删除入口；全部数据仅用于内部核对与评审，不进入对话内容。
      </p>
    </div>

    <div v-else-if="!loading && !error" class="text-[12px]" style="color: var(--c-text-3)">
      暂无记忆数据。
    </div>
  </section>
</template>
