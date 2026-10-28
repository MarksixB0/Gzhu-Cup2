# -*- coding: utf-8 -*-
"""桌宠素材清单与动作表必须一致（离线、秒级）。

守六件事，任何一条破了都会在现场表现为「破图 / 动作不播 / 白带一坨素材」：

1. **代码要播的变体，素材必须在** —— 缺失即整段动画不播（`resolveSpec` 跳过该段）。
2. **结构化取值必须成立**：``states[状态][变体]`` 要**精确**取得到。
   这条是本文件最要紧的：曾经剪枝重建清单时把多级状态名 ``MOVE/walk.left`` 压成了
   ``MOVE`` + 变体 ``walk.left/B_Nomal``，而运行期是按 ``states["MOVE/walk.left"]`` 取的
   —— 于是走路动画解析失败、桌宠移动时**只有平移**。只比较「压平后的字符串」抓不到它。
3. **状态键必须恰好是动作表引用的状态集合**（多级状态名整体出现，不多不少）。
4. **素材里不该有代码从不播放的变体** —— PNG 没有可压缩空间，多带的每个变体都是
   纯粹的包体积负担（见 2026-09-15 那轮剪枝：删 153 个变体省 23 MB）。
5. **帧名必须符合 VPet 时长契约** —— 时长写在文件名最后一个下划线之后
   （``A_000_125.png`` = 125ms）。解析不出会静默退化成 100ms/帧，动画节奏全错。
6. **清单声明的 frameSize 必须与实际 PNG 边长一致** —— 剪枝时曾把它误写回默认 512。

白名单的**唯一真相**是 `apps/desktop/src/pet/behavior.ts`，本文件不重复维护第二份清单。
"""
import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ASSETS = REPO / "pet" / "assets" / "vup"
MANIFEST = ASSETS / "manifest.json"
SYNC_SCRIPT = REPO / "pet" / "sync_vup_assets.py"

# VPet 帧时长契约：文件名主名最后一段必须是整数
_FRAME_OK = re.compile(r".*_\d+\.png$")


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("_sync_vup_assets", SYNC_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _keep_map() -> dict:
    """动作表引用的 ``{状态: {变体}}`` —— 保留结构化信息，才能校验清单的层级。"""
    return _load_sync_module().used_variants()


def _keep_flat() -> set:
    return _load_sync_module().flatten_variants(_keep_map())


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _disk_variants() -> set:
    """磁盘上实际存在的变体（叶子目录里含 png 的），压平形式。"""
    out: set[str] = set()
    for state_dir in sorted(p for p in ASSETS.iterdir() if p.is_dir()):
        for leaf in sorted(p for p in state_dir.rglob("*") if p.is_dir()):
            if list(leaf.glob("*.png")):
                out.add(f"{state_dir.name}/{leaf.relative_to(state_dir).as_posix()}")
    return out


def _png_size(path: Path) -> tuple[int, int]:
    """直接读 PNG 的 IHDR，避免为一条测试引入 Pillow 依赖。"""
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"不是 PNG: {path.name}"
    assert head[12:16] == b"IHDR", f"PNG 头异常: {path.name}"
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def test_manifest_matches_disk():
    """清单与磁盘必须完全一致 —— 否则前端枚举到的帧会 404。"""
    listed = {
        f"{state}/{variant}"
        for state, variants in _manifest()["states"].items()
        for variant in variants
    }
    disk = _disk_variants()
    assert listed == disk, (
        f"清单缺: {sorted(disk - listed)}  清单多余: {sorted(listed - disk)}"
    )


def test_every_referenced_variant_exists():
    """动作表引用的变体，一个都不能少（否则点了没反应）。"""
    keep = _keep_flat()
    assert keep, "没能从 behavior.ts 解析出任何变体 —— 动作表路径或格式变了"
    missing = keep - _disk_variants()
    assert not missing, f"动作表引用了但素材缺失: {sorted(missing)}"


def test_every_referenced_variant_exists_structurally():
    """``states[状态][变体]`` 必须**精确**取得到（状态名不能被压平）。

    压平写法（``states["MOVE"]["walk.left/B_Nomal"]``）在"压平字符串"这一层与正确写法
    完全一样，所以只有按状态名精确取值才能钉死它。
    """
    states = _manifest()["states"]
    missing: list[str] = []
    for state, variants in sorted(_keep_map().items()):
        bucket = states.get(state)
        for variant in sorted(variants):
            if not (isinstance(bucket, dict) and bucket.get(variant)):
                missing.append(f"states[{state!r}][{variant!r}]")
    assert not missing, (
        "清单层级不对，运行期解析不出这些动作（会退回待机 → 看起来像'平移'）: "
        f"{missing}"
    )


def test_manifest_state_keys_are_exact():
    """清单的状态键必须**恰好**等于动作表引用的状态集合（不多不少）。

    多级状态名（``MOVE/walk.left``）必须整体作为键出现；被拆成 ``MOVE`` 会立刻报出来。
    """
    listed = set(_manifest()["states"])
    expected = set(_keep_map())
    assert listed == expected, (
        f"清单少了状态: {sorted(expected - listed)}  清单多了状态: {sorted(listed - expected)}"
    )


def test_no_unreferenced_variants_shipped():
    """不许带代码从不播放的变体（安装包体积红线）。"""
    extra = _disk_variants() - _keep_flat()
    assert not extra, f"带了不会被播放的变体(白占体积): {sorted(extra)}"


def test_frames_follow_duration_contract():
    """每个帧文件名都要能被解析出时长（最后一段整数）。"""
    bad = [
        f.relative_to(ASSETS).as_posix()
        for f in ASSETS.rglob("*.png")
        if not _FRAME_OK.match(f.name)
    ]
    assert not bad, f"帧名不符合时长契约(会静默退化成 100ms): {bad[:10]}"


def test_manifest_frame_size_matches_actual_png():
    """清单声明的 frameSize 必须与实际 PNG 边长一致（剪枝时曾被误写回默认 512）。"""
    declared = int(_manifest()["frameSize"])
    sample = next(ASSETS.rglob("*.png"))
    w, h = _png_size(sample)
    assert (w, h) == (declared, declared), (
        f"清单声明 frameSize={declared}，实际帧是 {w}x{h}（{sample.name}）"
    )


def test_manifest_frames_all_exist():
    """清单里列出的每一帧都真的在磁盘上。"""
    missing: list[str] = []
    for state, variants in _manifest()["states"].items():
        for variant, frames in variants.items():
            for name in frames:
                if not (ASSETS / state / variant / name).is_file():
                    missing.append(f"{state}/{variant}/{name}")
    assert not missing, f"清单列出但文件不存在: {missing[:10]}"
