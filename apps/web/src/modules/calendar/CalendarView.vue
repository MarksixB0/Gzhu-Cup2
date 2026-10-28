<script setup lang="ts">
/**
 * 日历视图：一个月一屏的挂板，纪念日贴爱心、提醒贴小闹钟。
 *
 * 数据在 useCalendar（走后端），这里只管画与交互。
 * 版式沿用 `.workbuddy/proto/calendar-q.html`。
 */
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import EventSheet, { type CalForm } from './components/EventSheet.vue'
import ThemeToggle from '../../shell/components/ThemeToggle.vue'
import { MON, WD, festOf, useCalendar, type CalEvent } from './useCalendar'
/* 边角贴图：左上站姿、右下蹲姿，同一角色的两帧 */
import petStand from '../../assets/pet/pet-stand.png'
import petSquat from '../../assets/pet/pet-squat.png'

const {
  y,
  m,
  today,
  events,
  error,
  cells,
  eventsOn,
  tipOf,
  upsert,
  remove,
  reload,
  prevMonth,
  nextMonth,
  prevYear,
  nextYear,
} = useCalendar()

const monthLabel = computed(() => MON[m.value])

/* ---------- 弹层 ---------- */
const form = ref<CalForm | null>(null)
/** 提交中：按钮置灰，避免连点重复建/重复删 */
const busy = ref(false)

function openCreate(iso: string) {
  form.value = {
    mode: 'create',
    id: null,
    kind: 'todo',
    title: '',
    date: iso || today,
    time: '',
    yearly: false,
  }
}

function openEdit(id: string) {
  const e = events.value.find((x) => x.id === id)
  if (!e) return
  form.value = {
    mode: 'edit',
    id: e.id,
    kind: e.kind,
    title: e.title,
    date: e.date,
    time: e.time ?? '',
    yearly: e.yearly,
  }
}

async function save() {
  const f = form.value
  if (!f || busy.value) return
  busy.value = true
  const ok = await upsert({
    id: f.mode === 'edit' ? f.id : null,
    kind: f.kind,
    title: f.title.trim(),
    date: f.date || today,
    time: f.kind === 'anni' ? null : f.time || null,
    yearly: f.kind === 'anni' ? true : f.yearly,
  })
  busy.value = false
  // 失败就留在弹层里：错误显示在按钮上方，改完能直接再试
  if (ok) form.value = null
}

async function removeEvent(id: string) {
  if (busy.value) return
  busy.value = true
  const ok = await remove(id)
  busy.value = false
  if (ok) form.value = null
}

/* ---------- 悬浮提示 ---------- */
const tip = ref<{ text: string; left: number; top: number } | null>(null)
const tipEl = ref<HTMLElement | null>(null)
const cardEl = ref<HTMLElement | null>(null)

function showTip(ev: MouseEvent, group: CalEvent[]) {
  const el = ev.currentTarget as HTMLElement
  const r = el.getBoundingClientRect()
  tip.value = { text: group.map((e) => tipOf(e)).join('\n'), left: -9999, top: -9999 }
  // 先在屏幕外排版量出真实宽高，再定位 —— 多行文案也能正好贴住图标
  nextTick(() => {
    const t = tipEl.value
    if (!t) return
    const w = t.offsetWidth
    const h = t.offsetHeight
    const left = Math.min(Math.max(r.left + r.width / 2 - w / 2, 10), window.innerWidth - w - 10)
    const cardTop = cardEl.value?.getBoundingClientRect().top ?? 0
    const above = r.top - h - 9
    tip.value = { text: tip.value?.text ?? '', left, top: above < cardTop + 4 ? r.bottom + 9 : above }
  })
}

function hideTip() {
  tip.value = null
}

/* ---------- 一类只出一枚图标，多条时标 ×N ---------- */
function annisOn(iso: string) {
  return eventsOn(iso).filter((e) => e.kind === 'anni')
}
function todosOn(iso: string) {
  return eventsOn(iso).filter((e) => e.kind === 'todo')
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    hideTip()
    if (form.value) form.value = null
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  void reload()
})
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="cal">
    <!-- 顶栏：手帐标题 + 月/年翻页 + today + 记一件 -->
    <header class="cal-top">
      <div class="cal-title">CALENDAR</div>
      <div class="cal-nav">
        <div class="pill">
          <button class="arrow" title="上个月" @click="prevMonth">‹</button>
          <div class="ptxt mon">{{ monthLabel }}</div>
          <button class="arrow" title="下个月" @click="nextMonth">›</button>
        </div>
        <div class="pill">
          <button class="arrow" title="上一年" @click="prevYear">‹</button>
          <div class="ptxt yr">{{ y }}</div>
          <button class="arrow" title="下一年" @click="nextYear">›</button>
        </div>
      </div>
      <div class="cal-tools">
        <!-- 主题切换:跟随系统 + 昼夜,与对话/设置页同一组件 -->
        <ThemeToggle class="cal-theme" />
        <button class="addbtn" title="提醒事项" @click="openCreate(today)">
          <span>+</span>
        </button>
      </div>
    </header>

    <!-- 读写失败：弹层开着时错误就地显示在它内部，这里只管页面级失败 -->
    <div v-if="error && !form" class="cal-err">{{ error }}</div>

    <!-- 挂板 -->
    <div class="cal-body">
      <div class="cal-stage">
        <img class="pet-decor pet-tl" :src="petStand" alt="" aria-hidden="true" />
        <img class="pet-decor pet-br" :src="petSquat" alt="" aria-hidden="true" />

        <div ref="cardEl" class="cal-card">
          <div class="wk">
            <span v-for="w in WD" :key="w">{{ w }}</span>
          </div>

          <div class="grid">
            <div
              v-for="cell in cells"
              :key="cell.iso"
              class="cell"
              :class="{ wkend: cell.wd === 0 || cell.wd === 6, out: cell.out, today: cell.iso === today }"
              @click="openCreate(cell.iso)"
            >
              <div class="ctop">
                <span class="num">{{ cell.day }}</span>
                <span v-if="festOf(cell.iso)" class="fest">{{ festOf(cell.iso) }}</span>
              </div>

              <div v-if="annisOn(cell.iso).length" class="hearts">
                <span
                  class="chip anni"
                  @click.stop="openEdit(annisOn(cell.iso)[0].id)"
                  @mouseenter="showTip($event, annisOn(cell.iso))"
                  @mouseleave="hideTip"
                >
                  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path
                      d="M12 20.5s-7.2-4.4-8.9-8.7C1.5 8 3.5 4.6 6.9 4.6c1.9 0 3.4 1 4.9 2.8 1.5-1.8 3-2.8 4.9-2.8 3.4 0 5.4 3.4 3.8 7.2C18.8 16.1 12 20.5 12 20.5z"
                      fill="#F6A5A5"
                      stroke="#FFFFFF"
                      stroke-width="2.4"
                      stroke-linejoin="round"
                      paint-order="stroke"
                    />
                  </svg>
                  <span v-if="annisOn(cell.iso).length > 1" class="cnt">×{{ annisOn(cell.iso).length }}</span>
                </span>
              </div>

              <div v-if="todosOn(cell.iso).length" class="stk">
                <span
                  class="chip"
                  @click.stop="openEdit(todosOn(cell.iso)[0].id)"
                  @mouseenter="showTip($event, todosOn(cell.iso))"
                  @mouseleave="hideTip"
                >
                  <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <g fill="#78A6C8" stroke="#FFFFFF" stroke-width="2.2" stroke-linejoin="round" paint-order="stroke">
                      <circle cx="12" cy="14" r="8.2" />
                      <circle cx="12" cy="3.4" r="1.9" />
                    </g>
                    <g fill="none" stroke="#FFFFFF" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M12 9.9v4.2l3 2" />
                    </g>
                  </svg>
                  <span v-if="todosOn(cell.iso).length > 1" class="cnt">×{{ todosOn(cell.iso).length }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 空态提示：一条都没有时不假装有内容，只告诉用户入口在哪 -->

    <!-- 悬浮提示 -->
    <div v-if="tip" ref="tipEl" class="cal-tip" :style="{ left: tip.left + 'px', top: tip.top + 'px' }">
      {{ tip.text }}
    </div>

    <EventSheet
      v-if="form"
      :form="form"
      :error="error"
      :busy="busy"
      @save="save"
      @remove="removeEvent"
      @close="form = null"
    />
  </div>
</template>

<style scoped>
/* ---------- 配色：与全站令牌同源，日历另存一套是为了让网格线/纸面能独立微调 ---------- */
.cal {
  --cal-paper: #fafcfd;
  --cal-paper-2: #f7fbfd;
  --cal-wood: #4c728e;
  /* 标题/年月文字：深色下要单独提亮，挂板底色不能跟着亮 */
  --cal-title: #4c728e;
  --cal-hard: #cbddeb;
  --cal-ink: #273847;
  --cal-ink-2: #5a7286;
  --cal-ink-3: #8ca3b4;
  --cal-line: #e4eff7;
  --cal-line-2: #dce8f2;
  --cal-peach: #78a6c8;
  --cal-peach-soft: #e0eef8;
  --cal-fest: #c0893e;
  --cal-wkend: #f4fafe;
  --cal-out: #f2f7fa;
  --cal-hover: #f0f6fa;
  --cal-field: #f7fbfd;
  /* 读写失败提示：浅色下是淡红纸条，深色下换成暗底暖字 */
  --cal-warn-bg: #fdf1f0;
  --cal-warn-line: #e7b7b4;
  --cal-warn-ink: #a94b48;
  /* 格子高度基准：窗口高度扣掉顶栏/表头/内边距后六等分，整块随窗口等比缩放 */
  --cell: clamp(46px, calc((100vh - 210px) / 6), 104px);
  --num: calc(var(--cell) * 0.385);
  --ic: calc(var(--cell) * 0.365);
  --ic-anni: calc(var(--cell) * 0.4);
  --fest-size: calc(var(--cell) * 0.173);
  --cnt: calc(var(--cell) * 0.202);
  --pad-v: calc(var(--cell) * 0.07);
  --pad-h: calc(var(--cell) * 0.115);

  display: flex;
  height: 100%;
  flex-direction: column;
  overflow: hidden;
  background: var(--c-page);
  font-family: var(--font-hand);
  color: var(--cal-ink);
  -webkit-font-smoothing: antialiased;
}

/* 深色：纸面整体压成深海蓝墨，挂板提亮一档，描边与硬影跟着变暗 */
.dark .cal {
  --cal-paper: #172531;
  --cal-paper-2: #1b2a38;
  --cal-wood: #35607d;
  --cal-title: #a8c9de;
  --cal-hard: #0c1620;
  --cal-ink: #e6eff6;
  --cal-ink-2: #a9c2d3;
  --cal-ink-3: #7c96a8;
  --cal-line: #22374a;
  --cal-line-2: #263b4c;
  --cal-peach-soft: #22374a;
  --cal-wkend: #1b2a38;
  --cal-out: #131f29;
  --cal-hover: #22374a;
  --cal-field: #1b2a38;
  --cal-warn-bg: #33232a;
  --cal-warn-line: #5e3a3d;
  --cal-warn-ink: #e8b4ac;
}

/* 读写失败提示条：不占挂板的空间感，只是顶栏下的一行纸条 */
.cal-err {
  flex: none;
  margin: 0 24px 6px;
  padding: 8px 14px;
  border: 2px solid var(--cal-warn-line);
  border-radius: 10px;
  background: var(--cal-warn-bg);
  color: var(--cal-warn-ink);
  font-size: 13px;
  line-height: 1.5;
  -webkit-text-stroke: 0.42px currentColor;
}

/* 中文补一点描边，与厚实的数字同一视觉重量 */
.fest, .ptxt {
  -webkit-text-stroke: 0.42px currentColor;
}

/* ---------- 顶栏 ---------- */
.cal-top {
  display: flex;
  height: 88px;
  flex: 0 0 88px;
  align-items: center;
  gap: 20px;
  /* 左侧留出贴图的位置，标题从贴图右边起排 */
  padding: 10px 26px 0 88px;
}

.cal-title {
  font-size: 46px;
  line-height: 1;
  letter-spacing: 3px;
  color: var(--cal-title);
  text-transform: uppercase;
  user-select: none;
  -webkit-text-stroke: 0.5px currentColor;
  text-shadow: 0 2px 0 rgba(255, 255, 255, 0.7);
}
.dark .cal-title { text-shadow: none; }

.cal-nav {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  gap: 12px;
}

.pill {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px 4px;
  border: 1.5px solid var(--cal-line-2);
  border-radius: 999px;
  background: var(--cal-peach-soft);
}

.arrow {
  display: inline-flex;
  width: 30px;
  height: 30px;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--cal-ink-3);
  font-size: 20px;
  line-height: 1;
  transition: color 0.14s, background 0.14s, transform 0.14s;
}
.arrow:hover { color: #fff; background: var(--cal-peach); transform: scale(1.06); }

.ptxt {
  color: var(--cal-title);
  text-align: center;
  text-transform: uppercase;
  white-space: nowrap;
  user-select: none;
}
.mon { font-size: 25px; letter-spacing: 3px; min-width: 170px; line-height: 1.1; }
.yr { font-size: 21px; letter-spacing: 3px; min-width: 86px; line-height: 1.1; }

.cal-tools { display: flex; align-items: center; gap: 10px; }
/* 主题切换在日历页沿用页面自己的胶囊观感(颜色变量随明暗切换) */
.cal-theme :deep(.tt-btn) { color: #4c728e; }
.dark .cal-theme :deep(.tt-btn) { color: var(--cal-ink); }

.addbtn {
  display: inline-flex;
  width: 46px;
  height: 46px;
  align-items: center;
  justify-content: center;
  border-radius: 15px;
  background: var(--cal-peach);
  color: #fff;
  box-shadow: 3px 3px 0 #5c8dae;
  transition: background 0.14s, transform 0.12s, box-shadow 0.12s;
}
.addbtn span {
  margin-top: -6px;
  font-size: 33px;
  line-height: 1;
  letter-spacing: 0;
  user-select: none;
  text-shadow: 0 1px 0 rgba(35, 70, 95, 0.35);
}
.addbtn:hover { background: #8fbad8; transform: translateY(-1px); }
.addbtn:active { transform: translate(2px, 2px); box-shadow: 1px 1px 0 #5c8dae; }

/* ---------- 挂板 ---------- */
.cal-body { display: flex; min-height: 0; flex: 1; padding: 4px 28px 30px; }

/* 贴图的定位参照系：与卡片外框完全重合，贴图才能咬住卡片边角 */
.cal-stage { position: relative; display: flex; min-width: 0; flex: 1; }

.pet-decor {
  position: absolute;
  z-index: 4;
  pointer-events: none;
  user-select: none;
  filter: drop-shadow(0 6px 9px rgba(76, 114, 142, 0.2));
}
/* 左上：站在挂板角上探头，脚踩在表头上沿 */
.pet-tl { left: -22px; top: -46px; width: clamp(50px, 5.5vw, 64px); transform: scaleX(-1); }
/* 右下：蹲在角上，只压最后一列的下沿，避开上一行的提醒图标 */
.pet-br { right: -18px; bottom: -8px; width: clamp(52px, 5.9vw, 70px); }
/* 窄窗口下贴图会压到日期，直接不显示 */
@media (max-width: 820px) {
  .pet-decor { display: none; }
}

.cal-card {
  position: relative;
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1.5px solid var(--cal-line-2);
  border-radius: 18px;
  background: var(--cal-paper-2);
  box-shadow: 6px 6px 0 var(--cal-hard);
}

.wk {
  position: relative;
  display: grid;
  flex: 0 0 50px;
  grid-template-columns: repeat(7, 1fr);
  border-radius: 16px 16px 0 0;
  background: var(--cal-wood);
  background-image: repeating-linear-gradient(
    90deg,
    rgba(255, 255, 255, 0.05) 0 2px,
    rgba(255, 255, 255, 0) 2px 11px
  );
  box-shadow: inset 0 -3px 0 rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.1);
}
/* 两颗挂钉 */
.wk::before, .wk::after {
  content: '';
  position: absolute;
  top: 7px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #bbd6e8;
  box-shadow: inset 0 -1px 1px rgba(60, 100, 130, 0.35);
}
.wk::before { left: 10px; }
.wk::after { right: 10px; }

.wk span {
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-hand);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 2.5px;
  color: #fff;
  text-transform: uppercase;
  text-shadow: 0 1px 0 rgba(23, 48, 66, 0.28);
}
.wk span + span { border-left: 1px solid rgba(255, 255, 255, 0.18); }

.grid {
  display: grid;
  min-height: 0;
  flex: 1;
  grid-template-columns: repeat(7, 1fr);
  grid-template-rows: repeat(6, 1fr);
}

.cell {
  position: relative;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: var(--pad-v) var(--pad-h);
  border-left: 1px solid var(--cal-line);
  border-top: 1px solid var(--cal-line);
  background: var(--cal-paper);
  cursor: pointer;
  transition: background 0.12s;
}
.cell:nth-child(7n + 1) { border-left: none; }
.cell:nth-child(-n + 7) { border-top: none; }
.cell.wkend { background: var(--cal-wkend); }
.cell.out { background: var(--cal-out); }
.cell:hover { background: var(--cal-hover); }

.ctop { display: flex; width: 100%; align-items: baseline; gap: calc(var(--pad-h) * 0.67); }

.num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--num);
  line-height: 1;
  letter-spacing: 0.5px;
  color: var(--cal-ink);
}
.cell.out .num { color: var(--cal-ink-3); opacity: 0.65; }
.cell.today .num {
  width: calc(var(--cell) * 0.42);
  height: calc(var(--cell) * 0.42);
  padding: 0;
  border-radius: 50%;
  background: var(--cal-peach);
  box-shadow: 0 3px 0 rgba(76, 114, 142, 0.28);
  color: #fff;
  font-size: calc(var(--num) * 0.82);
}

.fest {
  min-width: 0;
  margin-left: auto;
  overflow: hidden;
  font-size: var(--fest-size);
  line-height: 1.1;
  color: var(--cal-fest);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cell.out .fest { opacity: 0.5; }

.hearts {
  display: flex;
  align-items: center;
  gap: calc(var(--cell) * 0.04);
  margin-top: calc(var(--cell) * 0.04);
}

.stk {
  position: absolute;
  right: var(--pad-h);
  bottom: calc(var(--pad-v) * 1.1);
  display: flex;
  align-items: center;
  gap: calc(var(--cell) * 0.04);
}
.cell.out .stk, .cell.out .hearts { opacity: 0.55; }

.chip {
  display: inline-flex;
  align-items: center;
  gap: calc(var(--cell) * 0.02);
  cursor: pointer;
  transition: transform 0.14s;
}
/* flex:0 0 auto 保证任何情况下 SVG 都不会被压小 */
.chip svg { width: var(--ic); height: var(--ic); flex: 0 0 auto; }
.chip.anni svg { width: var(--ic-anni); height: var(--ic-anni); }
.chip:hover { transform: translateY(-3px) scale(1.12); }

.cnt {
  padding-top: calc(var(--cell) * 0.038);
  font-size: var(--cnt);
  line-height: 1;
  letter-spacing: 0.5px;
  color: var(--cal-ink-3);
  -webkit-text-stroke: 0.3px currentColor;
}

/* ---------- 悬浮提示 ---------- */
.cal-tip {
  position: fixed;
  z-index: 90;
  max-width: 300px;
  padding: 9px 15px 10px;
  border: 2px solid var(--cal-wood);
  border-radius: 13px;
  background: var(--cal-paper);
  box-shadow: 3px 3px 0 var(--cal-hard);
  color: var(--cal-ink);
  font-size: 16px;
  line-height: 1.45;
  letter-spacing: 0.3px;
  white-space: pre-line;
  pointer-events: none;
}

</style>
