<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../../shell/api'
import type { MemoryDebug, RetrievalTraceRecord } from '../../../types'
import Icon from '../../../shell/components/Icon.vue'
import PetStatusPanel from './PetStatusPanel.vue'

/**
 * 长期记忆排障视图（只读）。
 *
 * 数据来源：GET /api/memory/debug 与 GET /api/memory/trace。
 * 职责限于轻量计数与检索留痕展示；完整洞察（逐日曲线、判定回放、上下文预览、
 * 待固化原话）由「记忆」分页（MemoryPanel）承担，两者共用同一只读数据面。
 * 全部数值仅用于排障，不得进入对用户可见的对话文案。
 */

const emit = defineEmits<{ openMemory: [] }>()

const debug = ref<MemoryDebug | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const trace = ref<RetrievalTraceRecord[]>([])
const traceLoading = ref(false)

const TRACE_LIMIT = 20

/** Record<string, number> → 键值对数组；null/undefined 视为空。 */
function entries(map: Record<string, number> | null | undefined): [string, number][] {
  return Object.entries(map ?? {})
}

/** Unix 秒 → 'MM-DD HH:mm:ss'；空值显示占位符。 */
function fmtTs(ts: number | null | undefined): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 拉取记忆计数。失败时置 error，保留上次数据。 */
async function load() {
  loading.value = true
  error.value = null
  try {
    debug.value = await api.getMemoryDebug()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

/** 拉取检索留痕。留痕读取失败不阻塞主视图，置空即可。 */
async function loadTrace() {
  traceLoading.value = true
  try {
    trace.value = (await api.getRetrievalTrace(TRACE_LIMIT)).records
  } catch {
    trace.value = []
  } finally {
    traceLoading.value = false
  }
}

onMounted(() => {
  void load()
  void loadTrace()
})
</script>

<template>
  <section>
    <div class="mb-1 flex items-center justify-between">
      <h3 class="text-[13px] font-medium" style="color: var(--c-text-2)">长期记忆 · 排障视图</h3>
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
    <p class="mb-3 text-[12px] leading-relaxed" style="color: var(--c-text-3)">
      桌宠的主动话题与主动关怀均读自<b>事实 / 片段</b>两类记忆条目。
      两类计数均为 0 表示记忆尚未整理（需数轮对话或间隔静默后触发）。
      本页数值仅用于排障核对，不进入对话内容。
    </p>

    <button
      class="mb-4 flex w-full items-center gap-2 rounded-xl border px-3 py-2.5 text-left text-[12px] transition ui-hover"
      style="border-color: var(--c-border); color: var(--c-text-2)"
      @click="emit('openMemory')"
    >
      <Icon name="chart" :size="15" style="color: var(--c-accent)" />
      <span class="flex-1">
        <b style="color: var(--c-text)">打开「记忆」分页 → 完整洞察</b><br />
        <span style="color: var(--c-text-3)">
          逐日曲线 · 判定回放 · 配额命中明细 · 上下文预览 · 待固化原话 · 话题覆盖
        </span>
      </span>
      <Icon name="chevronDown" :size="14" class="-rotate-90" />
    </button>

    <div
      v-if="error"
      class="mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-[12px]"
      style="border-color: #e5484d; color: #e5484d; background: var(--c-input)"
    >
      <Icon name="alert" :size="14" class="mt-0.5 shrink-0" />
      <span>{{ error }}</span>
    </div>

    <div v-if="debug" class="space-y-3">
      <div
        v-for="u in debug.users"
        :key="u.user"
        class="rounded-xl border p-3.5"
        style="border-color: var(--c-border)"
      >
        <!-- 头部 -->
        <div class="mb-2.5 flex flex-wrap items-center gap-2">
          <span class="text-[13px] font-medium" style="color: var(--c-text)">{{ u.user }}</span>
          <span
            class="rounded px-1.5 py-0.5 font-mono text-[10px]"
            style="background: var(--c-input); color: var(--c-text-3)"
          >{{ debug.backend }}</span>
          <span
            v-if="u.baseline?.sustained_worsening"
            class="rounded px-1.5 py-0.5 text-[10px]"
            style="background: var(--c-accent-soft); color: var(--c-accent)"
          >持续下行信号</span>
        </div>

        <!-- 计数 -->
        <div class="grid grid-cols-5 gap-1.5 text-center">
          <div class="rounded-lg px-1 py-1.5" style="background: var(--c-input)">
            <div class="font-mono text-[14px]" style="color: var(--c-accent)">{{ u.pending_turns }}</div>
            <div class="text-[10px]" style="color: var(--c-text-3)">待固化</div>
          </div>
          <div class="rounded-lg px-1 py-1.5" style="background: var(--c-input)">
            <div class="font-mono text-[14px]" style="color: var(--c-accent)">{{ u.fact_count }}</div>
            <div class="text-[10px]" style="color: var(--c-text-3)">事实</div>
          </div>
          <div class="rounded-lg px-1 py-1.5" style="background: var(--c-input)">
            <div class="font-mono text-[14px]" style="color: var(--c-accent)">{{ u.episode_count }}</div>
            <div class="text-[10px]" style="color: var(--c-text-3)">片段</div>
          </div>
          <div class="rounded-lg px-1 py-1.5" style="background: var(--c-input)">
            <div class="font-mono text-[14px]" style="color: var(--c-text-2)">{{ u.tick_days_7 }}</div>
            <div class="text-[10px]" style="color: var(--c-text-3)">7 日有记录</div>
          </div>
          <div class="rounded-lg px-1 py-1.5" style="background: var(--c-input)">
            <div class="font-mono text-[14px]" style="color: var(--c-text-2)">{{ u.tick_turns_7 }}</div>
            <div class="text-[10px]" style="color: var(--c-text-3)">7 日轮次</div>
          </div>
        </div>

        <!-- 近况 -->
        <div v-if="u.recent_summary" class="mt-2.5 text-[11px] leading-relaxed" style="color: var(--c-text-2)">
          <span style="color: var(--c-text-3)">近期状态</span> · {{ u.recent_summary }}
        </div>

        <!-- 事实 / 片段 -->
        <div class="mt-2.5 grid grid-cols-1 gap-2 md:grid-cols-2">
          <div>
            <div class="mb-1 text-[11px]" style="color: var(--c-text-3)">事实（前 10 条）</div>
            <ul v-if="u.facts.length" class="space-y-0.5">
              <li v-for="(f, i) in u.facts" :key="i" class="truncate text-[11px]" style="color: var(--c-text-2)">
                · {{ f }}
              </li>
            </ul>
            <div v-else class="text-[11px]" style="color: var(--c-text-3)">(无)</div>
          </div>
          <div>
            <div class="mb-1 text-[11px]" style="color: var(--c-text-3)">片段（前 10 条）</div>
            <ul v-if="u.episodes.length" class="space-y-0.5">
              <li v-for="(e, i) in u.episodes" :key="i" class="truncate text-[11px]" style="color: var(--c-text-2)">
                · {{ e }}
              </li>
            </ul>
            <div v-else class="text-[11px]" style="color: var(--c-text-3)">(无)</div>
          </div>
        </div>

        <!-- 特质卡 -->
        <div v-if="u.trait_card" class="mt-2.5 rounded-lg px-2.5 py-2" style="background: var(--c-input)">
          <div class="mb-1 flex items-center gap-2 text-[11px]" style="color: var(--c-text-3)">
            特质卡
            <span class="font-mono">{{ u.trait_card.evidence.days }} 天 /
              {{ u.trait_card.evidence.turn_count }} 轮</span>
          </div>
          <div class="space-y-0.5 text-[11px]" style="color: var(--c-text-2)">
            <div>情绪基线:{{ u.trait_card.traits.baseline_mood }}</div>
            <div>稳定性:{{ u.trait_card.traits.stability }}</div>
            <div>负向倾向:{{ u.trait_card.traits.negative_tendency }}</div>
            <div>社会支持:{{ u.trait_card.traits.social_orientation }}</div>
          </div>
        </div>

        <!-- 触发模式 -->
        <div v-if="u.trigger_mining.length" class="mt-2.5">
          <div class="mb-1 text-[11px]" style="color: var(--c-text-3)">
            触发模式（话题 → 情绪下行占比；观察量，不参与判定）
          </div>
          <div class="space-y-0.5">
            <div
              v-for="t in u.trigger_mining"
              :key="t.topic"
              class="flex items-center gap-2 text-[11px]"
              style="color: var(--c-text-2)"
            >
              <span class="w-16 shrink-0">{{ t.topic_zh }}</span>
              <span class="font-mono" style="color: var(--c-text-3)">
                {{ t.n_neg }}/{{ t.n_total }}
              </span>
              <span class="font-mono" style="color: var(--c-accent)">{{ t.rate }}</span>
            </div>
          </div>
        </div>

        <!-- 趋势：观察量，不参与档位判定 -->
        <div v-if="u.baseline" class="mt-2.5 flex flex-wrap gap-3 text-[11px]" style="color: var(--c-text-3)">
          <span
            class="rounded px-1.5 py-0.5"
            style="background: var(--c-input); color: var(--c-text-3)"
          >观察量 · 不参与判定</span>
          <span v-if="u.baseline">
            基线：均值 {{ u.baseline.mu }} / 波动 {{ u.baseline.sigma }}
            （样本 {{ u.baseline.baseline_days }} 天，连续高于基线 {{ u.baseline.consecutive_above }} 日）
          </span>
        </div>

        <!-- 风险原话计数：受保护留痕，仅排障可见 -->
        <div v-if="entries(u.risk_facets_total).length" class="mt-2.5 text-[11px]" style="color: var(--c-text-3)">
          <span>风险原话计数（受保护）</span>
          <span v-for="[k, v] in entries(u.risk_facets)" :key="'r' + k" class="ml-1.5 font-mono">
            {{ k }}={{ v }}
          </span>
          <span class="ml-1.5">| 全时段</span>
          <span v-for="[k, v] in entries(u.risk_facets_total)" :key="'t' + k" class="ml-1.5 font-mono">
            {{ k }}={{ v }}
          </span>
        </div>
      </div>

      <div v-if="!debug.users.length" class="text-[12px]" style="color: var(--c-text-3)">
        暂无记忆数据。
      </div>
    </div>

    <!-- 检索留痕：读侧组装决策的按次审计，独立命名空间，非记忆本体 -->
    <div class="mt-5 rounded-xl border p-3.5" style="border-color: var(--c-border)">
      <div class="mb-2 flex items-center justify-between">
        <span class="text-[12px] font-medium" style="color: var(--c-text-2)">
          检索留痕（最近 {{ TRACE_LIMIT }} 次）
        </span>
        <button
          class="flex items-center gap-1 text-[11px] ui-hover"
          style="color: var(--c-text-3)"
          :disabled="traceLoading"
          @click="loadTrace"
        >
          <Icon name="refresh" :size="12" :class="{ 'animate-spin': traceLoading }" />
          刷新
        </button>
      </div>
      <p class="mb-2.5 text-[11px] leading-relaxed" style="color: var(--c-text-3)">
        每次回复前记忆选取过程的审计记录：候选范围、最终入选与落选原因。
        仅排障可见，随记忆清空一并删除。
      </p>
      <div v-if="!trace.length" class="text-[11px]" style="color: var(--c-text-3)">
        暂无留痕记录。
      </div>
      <div v-else class="space-y-1.5">
        <div
          v-for="(r, i) in trace"
          :key="i"
          class="rounded-lg px-2.5 py-2 text-[11px] leading-relaxed"
          style="background: var(--c-input)"
        >
          <div class="mb-0.5 flex flex-wrap items-center gap-1.5">
            <span class="font-mono" style="color: var(--c-text-3)">{{ fmtTs(r.ts) }}</span>
            <span
              class="rounded px-1.5 py-0.5 text-[10px]"
              style="background: var(--c-accent-soft); color: var(--c-accent)"
            >{{ r.purpose }}</span>
            <span
              v-if="r.fts_engaged"
              class="rounded px-1.5 py-0.5 text-[10px]"
              style="background: var(--c-input); color: var(--c-text-3)"
            >关键词补充 ×{{ r.fts_hits }}</span>
            <span
              v-if="r.fts_engaged && r.fts_applied === false"
              class="rounded px-1.5 py-0.5 text-[10px]"
              style="background: var(--c-input); color: var(--c-text-3)"
            >未介入（语料不足）</span>
            <span
              v-if="r.fts_fallback"
              class="rounded px-1.5 py-0.5 text-[10px]"
              style="background: var(--c-input); color: var(--c-text-3)"
            >全量兜底</span>
            <span style="color: var(--c-text-3)">
              选 {{ r.n_selected }}/{{ r.n_considered }}<template
                v-if="r.candidates?.facts"
              > · 候选 {{ r.candidates.facts.raw }}→{{ r.candidates.facts.kept }}<template
                  v-if="r.candidates.facts.beyond_window"
                >（窗外 +{{ r.candidates.facts.beyond_window }}）</template></template>
              · {{ r.chars ?? '—' }}/{{ r.budget ?? '∞' }} 字符
            </span>
          </div>
          <div v-if="r.query" class="truncate" style="color: var(--c-text-2)">
            问:{{ r.query }}<span
              v-if="r.intent_topics?.length"
              style="color: var(--c-text-3)"
            > · 主题 {{ r.intent_topics.join('/') }}</span>
          </div>
          <div v-if="r.selected.length" class="truncate" style="color: var(--c-text-3)">
            选:
            <span v-for="(s, j) in r.selected.slice(0, 3)" :key="j">
              {{ j > 0 ? ' / ' : '' }}{{ s.text }}<template v-if="s.rel">&nbsp;({{ s.lane }},rel={{ s.rel }})</template>
            </span>
            <span v-if="r.selected.length > 3"> …</span>
          </div>
          <div v-if="entries(r.drop_reasons).length" style="color: var(--c-text-3)">
            落选:
            <span v-for="[k, v] in entries(r.drop_reasons)" :key="k" class="font-mono mr-1.5">
              {{ k }}={{ v }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 桌宠运行状态与联调入口：运行状态非设置项，随调试视图呈现 -->
    <PetStatusPanel />
  </section>
</template>
