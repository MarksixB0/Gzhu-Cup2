/** 与后端 web/routes_* 返回结构对应的类型定义 */

/** 聊天消息轮:白板视图,只含时间 / 用户输入 / AI 回复(评估类数据不进入前端) */
export interface Turn {
  ts: string
  /** 主动消息轮为空串(桌宠自己开口,没有用户输入) */
  user: string
  ai: string
  /** 'chat' = 用户与 AI 的一轮;'proactive' = 系统主动发起的消息 */
  kind?: 'chat' | 'proactive'
  /** 仅 proactive:主动消息类型(checkin/recall/petlife/soothe/probe/warm) */
  care_kind?: string | null
  /** 仅 proactive:对应 outbox 条目 id,前端据此把它标记为已读 */
  care_msg_id?: string | null
  /**
   * 仅 proactive:用户当时正在聊天,这是它顺口接的一句 ——
   * 不摆「主动消息 · 问候」卡片,改用轻量标注。
   */
  inline?: boolean
}

export interface ThreadMeta {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  last_preview: string
}

/** 用户设置(存 localStorage) */
export type ThemeMode = 'light' | 'dark'

export interface Settings {
  theme: ThemeMode
  model: string
  temperature: number
}

export const DEFAULT_SETTINGS: Settings = {
  theme: 'light', // 默认白天(浅色):主视觉是浅蓝治愈系,夜间深色由右上角图标手动切换
  // 模型留空 = 不向请求传 model,由后端使用「模型与连接」中保存的默认模型
  model: '',
  temperature: 0.7,
}

/** 「API 连接」后端视图(GET /api/connection / PUT 返回)。Key 不回传明文。 */
export interface ConnectionView {
  enabled: boolean
  base_url: string
  /** 是否已有 Key(明文不会从后端返回) */
  api_key_set: boolean
  /** 展示用的打码 Key */
  api_key_masked: string
  default_model: string
  /** 可选模型 id 列表(探测 /models 得到) */
  models: string[]
  /** true=连接已接管模型调用;false=走环境变量/默认 */
  active: boolean
  /** 当前模型配置来源:connection | env_or_default */
  source: 'connection' | 'env_or_default'
}

/** PUT /api/connection 请求体 */
export interface ConnectionDraft {
  enabled: boolean
  base_url: string
  /** 留空表示保持后端已保存的 Key(后端不明文回显,前端也不能删) */
  api_key: string
  default_model: string
  models: string[]
}

/** POST /api/connection/test 响应 */
export interface ConnectionTestResult {
  ok: boolean
  models: string[]
}

/* ================= 桌宠 / 主动关怀 =================
 * 后端已有 outbox 机制:关怀调度线程评估后写入待读消息,前端拉取并逐条确认。
 * 这部分能力是**通用**的 —— 网页端与未来的桌宠前端复用同一套接口(见 PET_POLICY.md)。
 */

/** 主动消息的类型(对应 care.policy.CareKind) */
export type CareKind = 'checkin' | 'recall' | 'petlife' | 'soothe' | 'probe' | 'warm'

/** outbox 中的一条待读主动消息 */
export interface CareMessage {
  id: string
  user: string
  kind: CareKind | string
  /** recall=用户安全话题;probe=维度 key;petlife=今日叙事文本 */
  topic: string | null
  text: string
  /** 秒级时间戳 */
  created_ts: number
}

/** 各类型的展示名(仅用于界面标注,不参与提示词) */
export const CARE_KIND_LABEL: Record<string, string> = {
  checkin: '问候',
  recall: '想起你说过的事',
  petlife: '分享今天的生活',
  soothe: '温柔回访',
  probe: '随口关心',
  warm: '注意到你的变化',
  agenda: '日程提醒',
}

/** GET /api/care/status */
export interface CareStatus {
  user: string
  enabled: boolean
  date: string
  date_count: number
  last_proactive_ts: number | null
  pending_count: number
}

/** GET /api/pet/settings */
export interface PetStatus {
  user: string
  /** 每日生活叙事生成开关 */
  daily_enabled: boolean
  /** 最近一次成功生成的自然日(YYYY-MM-DD) */
  last_generated: string | null
}

/** GET /api/pet/daily :今日叙事(未生成为 text=null;source=llm|user_edited|unavailable) */
export interface PetDaily {
  day: string | null
  text: string | null
  source: string | null
  /** 今日已显式刷新次数(用户点「刷新」的次数;首次自动生成为 0) */
  regen_count?: number
  /** 用户最后一次编辑的时间戳(秒;仅 user_edited 有) */
  edited_ts?: number
}

/** GET /api/memory/trace :检索留痕(读侧组装决策的按次审计,只读) */
export interface RetrievalTraceRecord {
  ts: number
  purpose: 'reply' | 'care' | string
  query: string
  budget: number | null
  chars: number | null
  fts_engaged: boolean
  /** 是否真的进入初筛;小库(候选未超过门槛)不筛时为 false */
  fts_applied?: boolean
  fts_hits: number
  /** FTS 初筛是否真的减少了候选(engaged 只表示命中并进入初筛) */
  fts_narrowed?: boolean
  /** 命中集为空、退回了全集 */
  fts_fallback?: boolean
  /** 各命名空间初筛前/后条数:raw → eligible → after_fts → kept */
  candidates?: Record<string, {
    raw: number
    eligible: number
    after_fts: number
    kept: number
    /** FTS 命中落在 list_items 窗口之外、被 get_item 直取回来的条数 */
    beyond_window?: number
    filtered: boolean
    fallback: boolean
  }>
  /** 本轮 query 命中的主题(排障用;空数组=未识别到主题) */
  intent_topics?: string[]
  lanes_all: Record<string, number>
  lanes_selected: Record<string, number>
  n_considered: number
  n_selected: number
  selected: { lane: string; base: number; rel: number; text: string }[]
  episode_keys: string[]
  drop_reasons: Record<string, number>
}

/* ================= 长期记忆调试(GET /api/memory/debug,只读) =================
 * 排障用:主动回复里的 recall / probe 依赖这里的 facts / episodes。
 * 这些数值**只用于内部排障**,按设计绝不进入任何对用户可见的文案。
 */

/** 趋势层:个人基线 + 恶化检测(数据不足以建立基线时为 null) */
export interface MemoryBaseline {
  mu: number
  sigma: number
  baseline_days: number
  drift_days: { day: string; neg: number; z: number }[]
  above_count: number
  consecutive_above: number
  /** 连续漂移日达阈值 → 持续恶化(单日波动永不触发) */
  sustained_worsening: boolean
}

/** 画像层:用户特质卡(窗口内无数据为 null) */
export interface TraitCard {
  traits: {
    baseline_mood: string
    stability: string
    negative_tendency: string
    social_orientation: string
  }
  dominant_emotion: string | null
  evidence: {
    days: number
    turn_count: number
    neg_avg: number | null
    volatility: number | null
    negative_share: number | null
    risk_rank_max: number
    topic_episodes: number
    social_episodes: number
  }
}

/** 画像层:话题 → 情绪下行占比(可溯源计数) */
export interface TriggerMining {
  topic: string
  topic_zh: string
  n_total: number
  n_neg: number
  rate: number
}

/** 调试视图里的单个用户 */
export interface MemoryDebugUser {
  user: string
  /** 已累积但尚未固化的对话轮数(达门槛才固化成事实/片段) */
  pending_turns: number
  fact_count: number
  facts: string[]
  episode_count: number
  episodes: string[]
  tick_days_7: number
  tick_turns_7: number
  recent_summary: string | null
  /** 危机分面(仅窗口内;遵循 MEMORY_RISK_EVENTS_RECENT_DAYS) */
  risk_facets: Record<string, number>
  /** 危机分面(全时段,排障用) */
  risk_facets_total: Record<string, number>
  baseline: MemoryBaseline | null
  trait_card: TraitCard | null
  trigger_mining: TriggerMining[]
}

/** GET /api/memory/debug */
export interface MemoryDebug {
  /** 记忆存储后端(SqliteStore / InMemoryStore) */
  backend: string
  users: MemoryDebugUser[]
}

/* ============ 长期记忆完整洞察(GET /api/memory/insight,只读) ============
 * 面向**全量分析**而非轻量排障:逐日日桶全字段、facts/episodes 全字段
 * (含 memory_score 与读侧配额入选)、待固化原话、守护层 A/B/C 通道真值与逐日回放、
 * 维度账本、读侧上下文预览。
 * 与 DebugSettings 的分工见 web/routes_admin.py 的 docstring。
 */

/** 逐日日桶(全部原始字段) */
export interface InsightBucket {
  day: string
  count: number
  neg_avg: number | null
  neg_n: number
  /** 强度均值(0-1);该字段不参与趋势聚合,仅排障观察 */
  inten_avg: number | null
  /** 当日词表通道均值 = lex_sum / lex_n */
  lex_avg: number | null
  lex_n: number
  lex_sum: number
  /** 深夜活跃轮次(0:00-6:00) */
  night_count: number
  /** 镜像确认信号:用户对上轮负向判定的自然纠错计数 */
  confirm_count: number
  deny_count: number
  risk_max_rank: number
  risk_max_name: string
  emotions: Record<string, number>
  /** 出现过 high/crisis → 该日桶受保护,不参与过期清理 */
  protected: boolean
  last_ts: string | null
}

/** 守护层四通道真值(逐条对应 guard.decide_watch / decide_elevated 源码) */
export interface GuardReplayDay {
  day: string
  /** 该日 23:59 时刻的判定结果(完整数据集 + 时刻回拨,无前视偏差) */
  tier: string
}

/** "资料够不够"的依据(看最近 7 天有记录天数) */
export interface GuardDataBasis {
  basis: 'recent'
  /** 最近 7 天有记录的天数 */
  recent_n_days?: number
  days_total?: number
  min_recent_days?: number
  coverage?: number | null
  idle_days?: number | null
}

export interface GuardView {  tier: string
  /** 纯分数的瞬时档(不含停留期、沉默保持);用于解释"为什么还没降档" */
  tier_instant?: string
  /** 分数分解;没有数据时为 null */
  score?: GuardScore | null
  available: boolean
  reason?: string
  /** 状态机模式:normal / lapse / lapse_decay / insufficient */
  mode?: string | null
  /** 档位为什么停在这一档(排障用;绝不进用户可见文案) */
  tier_reason?: string | null
  /** 数据是否够做判断(依据见 data_basis) */
  data_sufficient: boolean
  data_basis?: GuardDataBasis
  /** 流水线各段耗时与关键输出(排障用) */
  trace?: { stage: string; note: string; ts: number }[]
  replay: GuardReplayDay[]
}

/** 打分层可审计分解(仅用于排障;模板里必须翻成人话) */
export interface GuardScore {
  /** 状态机模式:normal / lapse / lapse_decay / insufficient */
  mode?: string | null
  reason?: string | null
  scores: {
    s_total?: number
    s_change?: number
    s_severity?: number
    s_chronic?: number
    s_behavior?: number
    s_fast?: number
    s_dimension?: number
    s_precursor?: number
    quality?: number
  }
  /** 评分路径取到的值,面板用来说明"是哪条路把它推上去的" */
  paths?: { emotion?: number; chronic?: number; behavior?: number; fast?: number; dimension?: number; precursor?: number } | null
  windows?: {
    recent?: { n_days?: number; neg_mean?: number | null; neg_median?: number | null }
    base?: { n_days?: number; neg_mean?: number | null; neg_median?: number | null }
    fast?: { n_days?: number; neg_mean?: number | null }
  } | null
  families?: Record<string, number | null>
  /** 症状族密度的真实分母(max(5, 近窗有记录天数));把 0.4 翻成"几天"时必须乘它 */
  family_span?: number | null
  detail?: Record<string, unknown> | null
}

export interface InsightFact {
  key: string
  text: string
  category: string
  importance: number | null
  mentions: number | null
  /** importance ×(1+ln 提及次数)× 新鲜度衰减 —— 决定读侧谁能入选 */
  memory_score: number
  archived: boolean
  protected: boolean
  first_seen: string | null
  last_seen: string | null
  /** 是否有资格被系统主动提起(recall 的素材来源) */
  initiable: boolean
  /** 是否落在读侧前 N 条配额内(即模型本轮真能看到) */
  in_reply_quota: boolean
  /** 记忆分道:archive(档案,不变) / state(状态,会变) / event(事件,带日期) */
  lane?: 'archive' | 'state' | 'event'
  lane_zh?: string
  /** 与"用户当前这句话"的相关分(0-1);无问题语境时为 null */
  relevance?: number | null
  /** 是否进入"命中档"(相关问题优先于非命中档) */
  on_topic?: boolean | null
  /** 落选原因:budget / state_lane_cap / duplicate_in_context / cap / not_reached */
  drop_reason?: string | null
  /** 是否含风险原话(含则不受任何读侧限流) */
  risk_content?: boolean
}

export interface InsightEpisode {
  key: string
  digest: string
  importance: number | null
  mentions: number | null
  memory_score: number
  archived: boolean
  protected: boolean
  risk_note: string
  topics: string[]
  topics_zh: string[]
  dominant_emotion: string | null
  valence_arc: string | null
  emotion_shift: string | null
  arousal: string | null
  dimension_evidence: Record<string, { name: string; level: number | null; quote: string | null }>
  triggers: string[]
  coping: string[]
  turn_count: number | null
  ts_start: string | null
  ts_end: string | null
  in_reply_quota: boolean
}

export interface InsightPendingTurn {
  user: string
  ai: string
  thread: string | null
  ts: string | null
  risk_level: string | null
  emotion: string | null
  negative_score: number | null
  lex_neg: number | null
}

export interface InsightUser {
  user: string
  totals: {
    backend: string
    day_buckets: number
    turns: number
    fact_count: number
    fact_active: number
    episode_count: number
    episode_active: number
    risk_event_count: number
    pending_turns: number
    first_day: string | null
    last_day: string | null
  }
  /** 回话时一次带得动多少记忆(面板上"几件事/几段回忆"那句) */
  retrieve_budget: {
    facts: number
    episodes: number
  }
  lexicon_ladder: {
    base_no_hit: number
    base_hit: number
    step_per_word: number
    pos_step: number
    note: string
  }
  buckets: InsightBucket[]
  trend: {
    window_3d: Record<string, unknown> | null
    window_trait: Record<string, unknown> | null
    baseline: MemoryBaseline | null
    recent_summary: string | null
  }
  guard: GuardView
  /** 当前心理风险标记(内部;未标记过为 null) */
  risk_state: RiskState | null
  /** 标记跃迁历史(最近 50 条) */
  risk_history: RiskHistoryEntry[]
  facts: InsightFact[]
  episodes: InsightEpisode[]
  pending: {
    exists: boolean
    turn_count: number
    is_due: boolean
    is_urgent: boolean
    min_turns_threshold: number
    idle_seconds_threshold: number
    turns: InsightPendingTurn[]
  }
  risk_events: { facet: string; quote: string; ts: string | null }[]
  dimension_ledger: {
    dim_names: Record<string, string>
    covered: Record<string, { count: number; level: number; max_level: number; last_ts: number; quote: string }>
    uncovered: string[]
  }
  profile: {
    trait_card: TraitCard | null
    trigger_mining: TriggerMining[]
  }
  context_preview: {
    reply: string
    care: string
    debug: string
    reply_chars: number
    care_chars: number
    debug_chars: number
  }
}

/** 心理风险标记(内部留痕;不对外通知、不对用户展示内部标签) */
export interface RiskState {
  user: string
  tier: string
  tier_rank: number
  updated_at: number
  /** 当前档位是从什么时候开始的 */
  since: number
  /** 标记依据(内部排障用):分数分解(s_total/s_change/s_chronic/…) */
  evidence: Record<string, unknown>
  note: string
}

/** 标记跃迁历史(只在档位发生变化时记录) */
export interface RiskHistoryEntry {
  ts: number
  from: string | null
  to: string
  reason: string
}

/** GET /api/memory/insight */
export interface MemoryInsight {
  generated_at: string
  backend: string
  memory_dir: string
  /** 边界声明:本标记只在本机内部使用 */
  risk_policy: {
    scope: string
    external_notification: boolean
    user_visible: boolean
    means: string
    does_not: string
    crisis_path: string
    see: string
  }
  users: InsightUser[]
}

/* ==================== 统一设置面(见 PLAN_NEXT.md §7) ==================== */

/** 打扰等级的状态视图(与后端 pet.runtime.mode_status 对应) */
export interface ModeSettings {
  level: string
  source: string
  reason: string
  until: number | null
  default_level: string
  manual_level: string | null
  manual_until: number | null
  night_sleep: boolean
  choices: string[]
  levels: string[]
  preset_minutes: number[]
  night_window: number[]
}

/** GET /api/settings(按域分组;各域仍是唯一真相,这里只是聚合视图) */
export interface AppSettings {
  care: {
    enabled: boolean
    /** 只读:让用户知道「系统会多久找我一次」 */
    max_per_day: number
    cooldown_hours: number
    quiet_window: number[]
  }
  pet: {
    daily_enabled: boolean
    greeting: string
    active_card: string | null
  }
  mode: ModeSettings
  privacy: MemoryPrivacy
  connection: {
    base_url: string
    api_key_set: boolean
    api_key_masked: string
    active: boolean
  }
}

/* ==================== 记忆隐私控制面(见 PLAN_NEXT.md §6) ==================== */

/** GET/PATCH /api/memory/privacy(不受 MEMORY_INSIGHT_ENABLED 约束:属知情权底线) */
export interface MemoryPrivacy {
  user: string
  /** false = 已暂停记录(只影响写入,对话不受影响) */
  recording: boolean
  memory_dir: string
  files: { name: string; bytes: number }[]
}

/** DELETE /api/memory/all 的回执:实际删了哪些命名空间、各多少条 */
export interface MemoryWipeResult {
  ok: boolean
  removed: Record<string, number>
}

/* ==================== 角色卡(人格唯一来源,见 PET_POLICY) ==================== */

/* ---- 角色卡模块（已迁至 modules/cards/，此处只保留出口，避免调用方跟着改路径） ---- */
export type { CardItem, CardsPayload } from './modules/cards/types'
