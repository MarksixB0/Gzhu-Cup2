/**
 * 日历状态：月份导航、事件增删改、节假日查表。
 *
 * 事件存在后端（`/api/agenda/events`），字段见 `types.ts`。
 */
import { computed, ref } from 'vue'

import { createEvent, deleteEvent, listEvents, updateEvent } from './api'
import type { CalCell, CalEvent, CalEventPayload } from './types'

export type { CalCell, CalEvent, CalEventPayload } from './types'

const pad = (n: number) => String(n).padStart(2, '0')
const isoOf = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const parseISO = (s: string) => {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export const MON = [
  'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE',
  'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER',
]
export const WD = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']

/* 节假日：公历固定 + 农历按年查表（表只备了 2025–2027，跨出去只是不显示） */
const SOLAR_FEST: Record<string, string> = {
  '01-01': '元旦', '02-14': '情人节', '03-08': '妇女节', '04-01': '愚人节',
  '05-01': '劳动节', '05-04': '青年节', '06-01': '儿童节', '07-01': '建党节',
  '08-01': '建军节', '09-10': '教师节', '10-01': '国庆节', '10-31': '万圣夜',
  '12-24': '平安夜', '12-25': '圣诞节',
}
const LUNAR_FEST: Record<string, Record<string, string>> = {
  2025: { '01-29': '春节', '02-12': '元宵节', '04-04': '清明节', '05-31': '端午节', '08-29': '七夕节', '10-06': '中秋节', '10-29': '重阳节' },
  2026: { '02-17': '春节', '03-03': '元宵节', '04-05': '清明节', '06-19': '端午节', '08-19': '七夕节', '09-25': '中秋节', '10-18': '重阳节' },
  2027: { '02-06': '春节', '02-20': '元宵节', '04-05': '清明节', '06-09': '端午节', '08-08': '七夕节', '09-15': '中秋节', '10-08': '重阳节' },
}

/** 某天的节日名（没有则空串） */
export function festOf(iso: string): string {
  const y = iso.slice(0, 4)
  const md = iso.slice(5)
  return LUNAR_FEST[y]?.[md] ?? SOLAR_FEST[md] ?? ''
}

/** 六周 × 七列；周日为一周之首（与挂板表头一致） */
function monthMatrix(y: number, m: number): CalCell[] {
  const offset = new Date(y, m, 1).getDay()
  const cells: CalCell[] = []
  for (let r = 0; r < 6; r++) {
    for (let c = 0; c < 7; c++) {
      const d = new Date(y, m, 1 - offset + r * 7 + c)
      cells.push({ iso: isoOf(d), day: d.getDate(), out: d.getMonth() !== m, wd: c })
    }
  }
  return cells
}

export function useCalendar() {
  const now = new Date()
  const today = isoOf(now)
  const y = ref(now.getFullYear())
  const m = ref(now.getMonth())
  const events = ref<CalEvent[]>([])
  /** 首次拉取中（网格照常显示，只是还没事件） */
  const loading = ref(false)
  /** 读写失败的一句话说明；成功后自动清空 */
  const error = ref<string | null>(null)

  const cells = computed(() => monthMatrix(y.value, m.value))

  /** 从后端重拉全部日程（进入页面时一次，写入失败后用于回滚） */
  async function reload() {
    loading.value = true
    try {
      const payload = await listEvents()
      events.value = payload.events
      error.value = null
    } catch (e) {
      error.value = `没能读取日程：${errText(e)}`
    } finally {
      loading.value = false
    }
  }

  /** 某天的事件：当天 + 每年重复的（按「月-日」比对），有时刻的排前面 */
  function eventsOn(iso: string): CalEvent[] {
    const md = iso.slice(5)
    return events.value
      .filter((e) => e.date === iso || (e.yearly && e.date.slice(5) === md))
      .sort((a, b) => {
        if (a.time && b.time) return a.time < b.time ? -1 : 1
        if (a.time) return -1
        if (b.time) return 1
        return a.kind === 'anni' ? -1 : 1
      })
  }

  /** 悬浮提示文案：纪念日按年份算「第 N 年」，提醒带时刻 */
  function tipOf(e: CalEvent): string {
    if (e.kind === 'anni') {
      const n = e.yearly ? y.value - Number(e.date.slice(0, 4)) + 1 : 0
      return `纪念日 · ${e.title}` + (n > 1 ? ` · 第 ${n} 年` : '')
    }
    return (e.time ? `${e.time} · ` : '') + e.title
  }

  /**
   * 新建或修改：等后端保存成功后再更新本地列表，返回是否成功。
   */
  async function upsert(draft: {
    id?: string | null
    kind: 'todo' | 'anni'
    title: string
    date: string
    time: string | null
    yearly: boolean
  }): Promise<boolean> {
    const payload: CalEventPayload = {
      kind: draft.kind,
      title: draft.title,
      date: draft.date,
      time: draft.time,
      yearly: draft.yearly,
    }
    try {
      if (draft.id) {
        const { event } = await updateEvent(draft.id, payload)
        const i = events.value.findIndex((x) => x.id === draft.id)
        if (i >= 0) events.value[i] = event
        else events.value.push(event)
      } else {
        const { event } = await createEvent(payload)
        events.value.push(event)
      }
      error.value = null
    } catch (e) {
      error.value = `没能保存：${errText(e)}`
      return false
    }
    // 存完跳到该事件所在月份
    const d = parseISO(draft.date)
    y.value = d.getFullYear()
    m.value = d.getMonth()
    return true
  }

  /** 删除：先从列表移除，失败则放回并记录错误 */
  async function remove(id: string): Promise<boolean> {
    const backup = events.value.find((x) => x.id === id)
    events.value = events.value.filter((x) => x.id !== id)
    try {
      await deleteEvent(id)
      error.value = null
      return true
    } catch (e) {
      if (backup) events.value.push(backup)
      error.value = `没能删除：${errText(e)}`
      return false
    }
  }

  function prevMonth() {
    if (m.value === 0) {
      m.value = 11
      y.value -= 1
    } else m.value -= 1
  }
  function nextMonth() {
    if (m.value === 11) {
      m.value = 0
      y.value += 1
    } else m.value += 1
  }

  return {
    y,
    m,
    today,
    events,
    loading,
    error,
    cells,
    eventsOn,
    tipOf,
    upsert,
    remove,
    reload,
    prevMonth,
    nextMonth,
    prevYear: () => (y.value -= 1),
    nextYear: () => (y.value += 1),
  }
}
