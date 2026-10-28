# -*- coding: utf-8 -*-
"""把 VPet 的桌宠素材复刻进本仓库。

**为什么需要复刻**:``reference/`` 是本地参考资料目录,已在 .gitignore 中,不属于
仓库内容。桌宠运行时**不得**依赖它,因此必须把真正需要的帧素材复制进仓库。

VPet 的素材契约(读自 ``VPet-Simulator.Core/Graph/PNGAnimation.cs``):

* 一个动画 = 一个目录下的 ``*.png`` 序列;
* 每帧的显示时长 = **文件名最后一个下划线之后**的整数,
  例如 ``A_000_125.png`` 表示该帧显示 125ms;
* 目录里只有一张图时退化为静态图。

本脚本按该契约复制 ``reference/VPet`` 的 vup 素材:

1. **保留目录结构与文件名**,因此运行时的帧时长解析逻辑不受缩放影响;
2. 把 1000px 的帧等比缩小到 512px;
3. 调色板量化到 256 色(含透明),把入库体积从 ≈159MB 压到 ≈31MB;
4. 生成 ``manifest.json`` 供运行时枚举动画与帧(纯前端无法列目录,必须预生成)。

用法::

    python pet/sync_vup_assets.py                 # 复刻默认核心状态集
    python pet/sync_vup_assets.py --states Say    # 只复刻部分状态(快速验证)
    python pet/sync_vup_assets.py --clean         # 先清空目标目录再复刻
    python pet/sync_vup_assets.py --colors 0      # 关闭量化(输出全彩 RGBA)
    python pet/sync_vup_assets.py --prune         # 就地删除「代码从不播放」的变体并重建清单

按引用剪枝(``--prune`` 与默认的复制白名单)
------------------------------------------
VPet 每个状态下有大量变体(全套 202 个 / 30.4MB),而桌宠实际只会播放其中一小部分
(由 ``apps/desktop/src/pet/behavior.ts`` 的 ``ACTIONS`` + ``IDLE_MICRO_ACTIONS`` 决定,
当前 49 个 / 8.4MB)。**PNG 已无可压缩空间**,所以多带的变体是纯粹的包体负担 —— 而且
用户永远看不到它们。故本脚本以 behavior.ts 为**唯一真相**提取白名单,只保留被引用的变体。

白名单**在运行期从 behavior.ts 解析**,不写死在脚本里 —— 否则改了动作表却忘了改清单,
就会出现「代码要播、素材却没打进包」的破图。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SRC = (
    REPO
    / "reference"
    / "VPet"
    / "VPet-Simulator.Windows"
    / "mod"
    / "0000_core"
    / "pet"
    / "vup"
)
DEFAULT_DST = REPO / "pet" / "assets" / "vup"

#: 动作表的唯一真相:桌宠会播哪些动画由它决定,素材白名单也从它解析出来。
LOCAL_BEHAVIOR = REPO / "apps" / "desktop" / "src" / "pet" / "behavior.ts"

#: 匹配 behavior.ts 里的动作条目 ``{ state: 'X', a: 'V1', b: 'V2', c: 'V3' }``。
#: b/c 可有可无;变体名是精确键(可能自带子路径,如 ``Squat/B_Nomal/1``)。
_VARIANT_RE = re.compile(
    r"state:\s*'([^']+)'\s*,\s*a:\s*'([^']+)'"
    r"(?:\s*,\s*b:\s*'([^']+)')?"
    r"(?:\s*,\s*c:\s*'([^']+)')?"
)


def used_variants(behavior_file: Path = LOCAL_BEHAVIOR) -> dict[str, set[str]]:
    """解析动作表,返回**代码真正会播放**的 ``{状态: {变体, ...}}``。

    刻意返回**结构化**结果而不是拼接好的 ``"状态/变体"`` 字符串:状态名本身可能带
    子路径(``MOVE/walk.left``),一旦压成字符串就再也分不清边界 —— 而清单里
    ``states`` 的键**必须**是完整状态名。曾经就因为丢了这条边界,剪枝重建出的清单
    把 ``MOVE/walk.left`` 写成了 ``MOVE`` + 变体 ``walk.left/B_Nomal``,运行期取不到
    走路动画(现象:桌宠移动时只有平移)。测试也依赖这里保留的结构。

    找不到文件时返回空 dict —— 调用方据此退化为「不过滤」(宁可多带素材,也不能少带
    导致破图)。这也让脚本在没有桌面端的纯后端环境里仍可运行。
    """
    try:
        text = behavior_file.read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, set[str]] = {}
    for m in _VARIANT_RE.finditer(text):
        state, a, b, c = m.group(1), m.group(2), m.group(3), m.group(4)
        for variant in (a, b, c):
            if variant:
                found.setdefault(state, set()).add(variant)
    return found


def flatten_variants(keep: dict[str, set[str]]) -> set[str]:
    """把 ``{状态: {变体}}`` 压成 ``{"状态/变体"}`` —— 仅用于磁盘路径的成员判断。"""
    return {f"{state}/{variant}" for state, variants in keep.items() for variant in variants}


def prune_unused(dst: Path, keep: set[str]) -> tuple[int, int]:
    """就地删除目标目录里**不在白名单**的变体目录。

    只删「直接含 png 的叶子目录」(与 :func:`scan` 的口径一致),上层分组目录留空即删。
    返回 ``(删除的变体数, 释放的字节数)``。
    """
    removed = 0
    freed = 0
    for leaf in sorted((p for p in dst.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not list(leaf.glob("*.png")):
            continue
        rel = leaf.relative_to(dst).as_posix()
        if rel in keep:
            continue
        freed += sum(f.stat().st_size for f in leaf.rglob("*") if f.is_file())
        shutil.rmtree(leaf)
        removed += 1
    # 清掉因此变空的分组目录
    for d in sorted((p for p in dst.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    return removed, freed


def rescan(dst: Path, states: list[str], keep: set[str] | None = None) -> dict[str, dict[str, list[str]]]:
    """按磁盘**现状**重建 ``{状态: {变体: [帧...]}}``(用于 --prune 后重建清单)。

    ⚠ **必须按 ``states`` 清单切分,不能按顶层目录切** —— 状态名可以带子路径
    (``MOVE/walk.left``),按顶层目录切会把它压成 ``MOVE`` + 变体 ``walk.left/B_Nomal``,
    于是运行期 ``manifest.states["MOVE/walk.left"]`` 取不到、走路动画解析失败
    (现象:桌宠移动时**只有平移、没有走路动画**,因为它退回了待机)。

    实现上直接复用 :func:`scan` 的同一套口径,保证「复刻」与「剪枝」产出的清单结构一致。
    """
    return scan(dst, states, keep, quiet=True)

#: 桌宠实际要用到的状态(排除 WORK / Music / 养成类等大而无用的素材)
DEFAULT_STATES = [
    "IDEL",        # 待机(含多种小动作)
    "Say",         # 说话
    "Think",       # 思考
    "Touch_Head",  # 摸头
    "Touch_Body",  # 摸身体
    "Pinch",       # 捏/拖拽
    "Raise",       # 被提起
    "Sleep",       # 睡觉
    "StartUP",     # 启动
    "Default",     # 默认站立
    # 走动(VPet MOVE 状态的走路子集):用于「桌宠主动靠近」。各 15 帧,很小。
    "MOVE/walk.left",
    "MOVE/walk.right",
    "MOVE/walk.left.faster",
    "MOVE/walk.right.faster",
]

FRAME_SIZE = 512
SOURCE_FRAME_SIZE = 1000

#: 调色板量化色数。512px 全彩 RGBA 约 85KB/帧(全量 ≈159MB),超出可接受的入库体积;
#: 量化到 256 色后约 17KB/帧(全量 ≈31MB)。实测 PSNR≈42dB、alpha 最大偏差 31/255
#: 且只影响约 2% 的半透明边缘像素,视觉上无可察觉差异。设为 0 则关闭量化。
FRAME_COLORS = 256


def scan(
    src: Path,
    states: list[str],
    keep: set[str] | None = None,
    quiet: bool = False,
) -> dict[str, dict[str, list[str]]]:
    """扫描源素材,返回 ``{状态: {变体相对路径: [帧文件名...]}}``。

    只有直接含 ``*.png`` 的目录才算"变体"(叶子);上层目录仅作分组。
    ``keep`` 非空时只保留其中的变体(即"代码会播放的"),其余丢弃。
    ``quiet`` 用于 ``--prune``(目标目录里本就不存在 ``.faster`` 这类状态,不必刷警告)。
    """
    result: dict[str, dict[str, list[str]]] = {}
    skipped = 0
    for state in states:
        root = src / state
        if not root.is_dir():
            if not quiet:
                print(f"  [跳过] 源目录不存在: {state}", file=sys.stderr)
            continue
        variants: dict[str, list[str]] = {}
        for leaf in sorted(p for p in root.rglob("*") if p.is_dir()):
            frames = sorted(f.name for f in leaf.glob("*.png"))
            if not frames:
                continue
            rel = leaf.relative_to(root).as_posix()
            if keep and f"{state}/{rel}" not in keep:
                skipped += 1
                continue
            variants[rel] = frames
        if variants:
            result[state] = variants
        else:
            print(f"  [跳过] 无帧素材: {state}", file=sys.stderr)
    if keep and skipped:
        print(f"  按引用剪枝: 跳过 {skipped} 个未被 behavior.ts 引用的变体")
    return result



def _convert_one(job: tuple[str, str, int, int]) -> tuple[str, int]:
    """缩放(可选量化)单帧。返回 ``(目标文件名, 字节数)``。

    ``PIL`` 延迟导入:``--prune`` 不转换任何帧,因而不该要求装图像库。
    """
    from PIL import Image

    src_name, dst_name, size, colors = job
    src, dst = Path(src_name), Path(dst_name)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            frame = im.convert("RGBA")
            if frame.size != (size, size):
                frame = frame.resize((size, size), Image.LANCZOS)
            if colors:
                # FASTOCTREE 对 RGBA 做四维联合量化,PNG 以 tRNS 保留调色板透明度
                frame = frame.quantize(colors=colors, method=Image.FASTOCTREE)
            frame.save(dst, "PNG", optimize=True)
    return dst.name, dst.stat().st_size


def build_jobs(
    scanned: dict[str, dict[str, list[str]]],
    src: Path,
    dst: Path,
    size: int,
    colors: int,
) -> list[tuple[str, str, int, int]]:
    jobs: list[tuple[str, str, int, int]] = []
    for state, variants in scanned.items():
        for variant, frames in variants.items():
            for name in frames:
                rel = f"{state}/{variant}/{name}"
                jobs.append((str(src / rel), str(dst / rel), size, colors))
    return jobs


def build_manifest(
    scanned: dict[str, dict[str, list[str]]], size: int, colors: int
) -> dict:
    return {
        "name": "vup",
        "frameSize": size,
        "colors": colors,
        "sourceFrameSize": SOURCE_FRAME_SIZE,
        "source": "VPet `0000_core/pet/vup`(来自 reference/VPet,该目录不入库)",
        "frameNaming": "<名称>_<帧号>_<时长ms>.png;帧时长 = 文件名最后一段整数",
        "states": scanned,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 VPet vup 素材复刻进仓库")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="VPet vup 素材目录")
    ap.add_argument("--dst", type=Path, default=DEFAULT_DST, help="仓库内的目标目录")
    ap.add_argument("--size", type=int, default=FRAME_SIZE, help="输出帧边长(px)")
    ap.add_argument(
        "--colors", type=int, default=FRAME_COLORS, help="量化色数,0=不量化"
    )
    ap.add_argument(
        "--states", nargs="*", default=DEFAULT_STATES, help="要复刻的状态(默认核心集)"
    )
    ap.add_argument("--jobs", type=int, default=0, help="并行进程数,0=CPU 核数")
    ap.add_argument("--clean", action="store_true", help="复刻前清空目标目录")
    ap.add_argument(
        "--prune", action="store_true",
        help="就地删除目标目录里未被 behavior.ts 引用的变体并重建清单(不需要源素材)",
    )
    args = ap.parse_args(argv)

    keep_map = used_variants()
    keep = flatten_variants(keep_map)
    if keep:
        print(f"引用白名单(来自 {LOCAL_BEHAVIOR.name}): "
              f"{len(keep)} 个变体 / {len(keep_map)} 个状态")
    else:
        print("警告:读不到动作表,本次不做按引用剪枝(退化为全量)", file=sys.stderr)

    # --prune:只动目标目录 —— 不读源素材、不做任何图像转换(故无需 PIL)
    if args.prune:
        if not args.dst.is_dir():
            print(f"错误:目标目录不存在: {args.dst}", file=sys.stderr)
            return 2
        # 帧边长/色数**沿用现有清单**:剪枝不改变分辨率,若用 CLI 默认值(512)会把
        # 清单里的 frameSize 写成与实际不符的 512(帧其实是 256px 的)。
        prev: dict = {}
        manifest_path = args.dst / "manifest.json"
        if manifest_path.is_file():
            try:
                prev = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prev = {}
        size = int(prev.get("frameSize") or args.size)
        colors = int(prev.get("colors") or args.colors)
        removed, freed = prune_unused(args.dst, keep)
        scanned = rescan(args.dst, args.states, keep)
        manifest = build_manifest(scanned, size, colors)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        after = sum(f.stat().st_size for f in args.dst.rglob("*.png"))
        v = sum(len(x) for x in scanned.values())
        print(
            f"剪枝完成: 删除 {removed} 个变体, {freed / 1e6:.1f} MB\n"
            f"  剩余 {v} 个变体 / {len(scanned)} 个状态, {after / 1e6:.1f} MB"
        )
        print(f"  清单已重建: {args.dst / 'manifest.json'}")
        return 0

    if not args.src.is_dir():
        print(f"错误:源素材目录不存在: {args.src}", file=sys.stderr)
        return 2

    if args.clean and args.dst.exists():
        print(f"清空 {args.dst} ...")
        shutil.rmtree(args.dst)

    print(f"源: {args.src}")
    print(f"目标: {args.dst}")
    print(f"状态: {', '.join(args.states)}")
    print(f"帧边长: {args.size}px, 色数: {args.colors or '全彩'}")

    scanned = scan(args.src, args.states, keep)
    if not scanned:
        print("错误:没有扫描到任何素材", file=sys.stderr)
        return 2

    total_frames = sum(
        len(frames) for variants in scanned.values() for frames in variants.values()
    )
    total_variants = sum(len(v) for v in scanned.values())
    print(f"扫描到 {len(scanned)} 个状态 / {total_variants} 个变体 / {total_frames} 帧")

    jobs = build_jobs(scanned, args.src, args.dst, args.size, args.colors)
    args.dst.mkdir(parents=True, exist_ok=True)

    started = time.time()
    done = 0
    bytes_total = 0
    with ProcessPoolExecutor(max_workers=args.jobs or None) as pool:
        for _name, nbytes in pool.map(_convert_one, jobs, chunksize=16):
            done += 1
            bytes_total += nbytes
            if done % 200 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)} 帧 ...", flush=True)

    manifest = build_manifest(scanned, args.size, args.colors)
    (args.dst / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    elapsed = time.time() - started
    print(
        f"完成: {done} 帧, {bytes_total / 1e6:.1f} MB, 用时 {elapsed:.1f}s\n"
        f"清单: {args.dst / 'manifest.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
