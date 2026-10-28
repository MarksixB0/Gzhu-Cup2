/** 素材清单的类型定义。清单由 `pet/sync_vup_assets.py` 生成（纯前端无法列目录）。 */

export interface PetManifest {
  /** 素材代号，也是 pet-asset:// 的 host，例如 vup */
  name: string
  /** 帧边长（px） */
  frameSize: number
  /** 调色板色数，0 表示全彩 */
  colors: number
  /** 原始素材的帧边长，便于对照 */
  sourceFrameSize: number
  /** 素材来源说明 */
  source: string
  /** 帧命名契约说明 */
  frameNaming: string
  /** 状态 -> 变体相对路径 -> 帧文件名列表 */
  states: Record<string, Record<string, string[]>>
}

/** 素材在自定义协议下的根 URL */
export const ASSET_SCHEME = 'pet-asset'
