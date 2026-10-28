/**
 * PNG 序列播放运行时。
 *
 * 帧时长契约**取自 VPet**（`VPet-Simulator.Core/Graph/PNGAnimation.cs`）：
 * 每帧的显示时长写在文件名最后一个下划线之后，例如 `A_000_125.png` = 125ms。
 * 素材经 `pet/sync_vup_assets.py` 复刻时保留了文件名，所以这里直接沿用同一契约，
 * 不需要额外的时长表。
 *
 * 播放器用 setTimeout 逐帧推进而非 requestAnimationFrame：VPet 的帧时长是固定的
 * 毫秒数（常见 125ms），不追求刷新率对齐；且桌宠不需要 60fps 的插值。
 */

import { ASSET_SCHEME, type PetManifest } from './manifest'

export interface PetFrame {
  /** 可加载的帧 URL */
  url: string
  /** 显示时长（ms） */
  time: number
  /** 源文件名，便于调试 */
  name: string
}

/** 解析失败时的兜底帧时长，避免整段动画卡死。 */
export const FALLBACK_FRAME_MS = 100

/**
 * 从帧文件名解析显示时长。
 * 契约：取文件主名最后一个下划线之后的整数。解析不出则回落到 `fallback`。
 */
export function frameDuration(fileName: string, fallback = FALLBACK_FRAME_MS): number {
  const base = fileName.replace(/\.[^.]+$/, '')
  const idx = base.lastIndexOf('_')
  if (idx < 0) return fallback
  const parsed = Number.parseInt(base.slice(idx + 1), 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

/** 把文件名编码进 URL（素材里有中文名，如「循环A_000_125.png」）。 */
function frameUrl(asset: string, state: string, variant: string, fileName: string): string {
  const path = `${state}/${variant}/${fileName}`
    .split('/')
    .map(encodeURIComponent)
    .join('/')
  return `${ASSET_SCHEME}://${asset}/${path}`
}

/**
 * 按清单取出某个状态/变体的帧序列。
 * 变体不存在时返回空数组（调用方负责回落到别的变体）。
 */
export function buildFrames(
  manifest: PetManifest,
  state: string,
  variant: string,
): PetFrame[] {
  const files = manifest.states[state]?.[variant]
  if (!files) return []
  return files.map((name) => ({
    name,
    time: frameDuration(name),
    url: frameUrl(manifest.name, state, variant, name),
  }))
}

/** 列出某状态下的全部变体名。 */
export function variantsOf(manifest: PetManifest, state: string): string[] {
  return Object.keys(manifest.states[state] ?? {})
}

/** 按候选顺序挑第一个存在的变体。 */
export function pickVariant(
  manifest: PetManifest,
  state: string,
  preferred: string[],
): string | null {
  const available = variantsOf(manifest, state)
  if (available.length === 0) return null
  for (const want of preferred) {
    const hit = available.find((v) => v.includes(want))
    if (hit) return hit
  }
  return available[0] ?? null
}

/**
 * 预加载帧，避免切换时闪空帧。
 * 单帧失败不阻塞整段（`onerror` 也 resolve）——宁可少一帧，不可整段不播。
 */
export function preloadFrames(frames: PetFrame[]): Promise<void> {
  return Promise.all(
    frames.map(
      (frame) =>
        new Promise<void>((resolve) => {
          const img = new Image()
          // 与展示用 <img> 保持一致：带 CORS 加载，避免缓存里存的是被污染版本
          img.crossOrigin = 'anonymous'
          img.onload = () => resolve()
          img.onerror = () => resolve()
          img.src = frame.url
        }),
    ),
  ).then(() => undefined)
}

/**
 * 帧序列播放器。一次播放一个变体（长度 12–70 帧），因此可以整体预加载。
 */
export class PngSequencePlayer {
  private frames: PetFrame[] = []
  private timer: ReturnType<typeof setTimeout> | null = null
  private index = 0
  private loop = false
  private loopFrom = 0
  private running = false

  /** 每次切帧时回调。 */
  constructor(private readonly emit: (frame: PetFrame, index: number) => void) {}

  get isRunning(): boolean {
    return this.running
  }

  get currentFrame(): PetFrame | null {
    return this.frames[this.index] ?? null
  }

  /**
   * 播放一段序列。
   * `loop=false` 时播完停在最后一帧；
   * `loop=true` 时从 `loopFrom` 起无限循环（`loopFrom=0` 即整段循环）。
   * `loopFrom>0` 用于「进入段只播一次、循环段无限播」的姿势型动作（如睡觉）。
   */
  play(frames: PetFrame[], loop: boolean, loopFrom = 0): void {
    this.stop()
    if (frames.length === 0) return
    this.frames = frames
    this.loop = loop
    this.loopFrom = loopFrom > 0 && loopFrom < frames.length ? loopFrom : 0
    this.index = 0
    this.running = true
    this.tick()
  }

  stop(): void {
    this.running = false
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }

  private tick(): void {
    const frame = this.frames[this.index]
    if (!frame) {
      this.stop()
      return
    }
    this.emit(frame, this.index)
    this.timer = setTimeout(this.advance, frame.time)
  }

  private readonly advance = (): void => {
    const next = this.index + 1
    if (next >= this.frames.length) {
      if (!this.loop) {
        // 单次动画结束：保留最后一帧画面，停在已结束状态
        this.stop()
        return
      }
      this.index = this.loopFrom
    } else {
      this.index = next
    }
    this.tick()
  }
}
