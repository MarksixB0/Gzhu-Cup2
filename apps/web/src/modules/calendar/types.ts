/**
 * 日程的数据形状。
 *
 * 与后端 agenda 命名空间同构（见 `agent/agenda/store.py`）：
 * `{ id, kind, title, date, time, yearly }`；后端另有 `created_ts` /
 * `updated_ts` / `last_notified`，属于内部字段，前端不读不写。
 */
export interface CalEvent {
  id: string
  /** todo=待办；anni=纪念日 */
  kind: 'todo' | 'anni'
  title: string
  /** YYYY-MM-DD */
  date: string
  /** HH:MM；为空 = 当天待办，到点不会提醒 */
  time: string | null
  /** 每年重复；纪念日默认 true */
  yearly: boolean
}

/** 新建/修改提交的字段（id 由服务端生成，内部时间戳不归前端管） */
export interface CalEventPayload {
  kind: 'todo' | 'anni'
  title: string
  date: string
  time: string | null
  yearly: boolean
}

/** 六周 × 七列的一个格子 */
export interface CalCell {
  iso: string
  day: number
  /** 不属于当前月（上月末尾 / 下月开头） */
  out: boolean
  /** 0=周日 */
  wd: number
}
