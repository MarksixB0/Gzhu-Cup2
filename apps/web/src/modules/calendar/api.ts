/** 日程接口:后端 `/api/agenda/events`。 */
import { request } from '../../shell/api/http'
import type { CalEvent, CalEventPayload } from './types'

export function listEvents() {
  return request<{ events: CalEvent[] }>('/api/agenda/events')
}

export function createEvent(payload: CalEventPayload) {
  return request<{ event: CalEvent }>('/api/agenda/events', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateEvent(id: string, payload: CalEventPayload) {
  return request<{ event: CalEvent }>(`/api/agenda/events/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export function deleteEvent(id: string) {
  return request<{ ok: boolean; removed: number }>(
    `/api/agenda/events/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  )
}
