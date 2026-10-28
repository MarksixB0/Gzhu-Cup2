/**
 * 角色卡模块的后端接口。
 *
 * 写操作一律返回**最新的完整列表**：界面上「建完 / 改完 / 删完 / 换用」之后
 * 都能直接用回包刷新，不必再补一次 GET（也就不会出现列表与状态不同步的中间态）。
 *
 * 三条边界：只经 /api/pet/cards 读写卡文件，不碰对话与记忆；
 * 「使用」是运行时的选择（存数据目录的 active_card.json），不改 config；
 * 删除要二次确认 —— 卡是人格的唯一副本，删了就真没了。
 */
import { request } from '../../shell/api/http'
import type { CardsPayload } from './types'

/** 列出可用角色卡（含提示词正文与开场白），并标出当前启用的一张 */
export function listCards(): Promise<CardsPayload> {
  return request<CardsPayload>('/api/pet/cards')
}

/** 新建一张卡（重名会被后端 400 拒绝，不会静默覆盖） */
export function createCard(draft: {
  name: string
  description: string
  first_mes: string
}): Promise<CardsPayload> {
  return request<CardsPayload>('/api/pet/cards', {
    method: 'POST',
    body: JSON.stringify(draft),
  })
}

/** 改一张卡（只提交传进来的字段，未传的保持原样） */
export function updateCard(
  name: string,
  draft: { description?: string; first_mes?: string },
): Promise<CardsPayload> {
  return request<CardsPayload>(`/api/pet/cards/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(draft),
  })
}

/** 删除一张卡（删掉的若是当前启用的，启用选择会一并清空） */
export function deleteCard(name: string): Promise<CardsPayload> {
  return request<CardsPayload>(`/api/pet/cards/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}

/** 切换当前启用的卡；传空串 = 不搭载任何角色卡 */
export function setActiveCard(name: string): Promise<CardsPayload> {
  return request<CardsPayload>('/api/pet/cards/active', {
    method: 'POST',
    body: JSON.stringify({ name }),
  })
}
