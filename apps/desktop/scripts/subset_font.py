# -*- coding: utf-8 -*-
"""把桌宠字体 ``loli.ttf`` 子集化到「ASCII + 常用标点 + GB2312 全部」。

为什么需要
----------
主字体原始 **11.4MB / 35893 字形**,而界面实际只会用到常用汉字。它占 ``app.asar``
的约 99%,是安装包里除 Electron 本体外**最大的可压缩项** —— 体积上限吃紧时它是
第一刀。子集化后 **2.88MB**(省 75%),且**常用字一个不少**。

缺字会怎样(安全边界)
--------------------
``apps/desktop/src/style.css`` 的字体栈带完整回退::

    font-family: 'LoliFont', system-ui, -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;

所以**缺字不会变豆腐块**,只会回退成微软雅黑 —— 代价仅是"个别生僻字字体不一致"。
GB2312 全集(7445 字)覆盖简体常用字与全角标点;**繁体字 / 日文假名 / emoji 不在其中**,
会走回退。

用法
----
    python apps/desktop/scripts/subset_font.py            # 就地覆盖 loli.ttf
    python apps/desktop/scripts/subset_font.py --dry-run  # 只报体积,不写盘

需要 ``fontTools``(本机 ``py -3.7`` 已具备)。原始 11.4MB 字体在 git 历史里,
需要还原时 ``git checkout -- apps/desktop/assets/fonts/loli.ttf``。
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FONT = REPO / "apps" / "desktop" / "assets" / "fonts" / "loli.ttf"

#: ASCII 可见字符
ASCII = [chr(c) for c in range(0x20, 0x7F)]
#: 中文常见标点与全角符号(GB2312 里也有,这里显式列出以防字体缺该码位)
PUNCT = list("，。！？、；：“”‘’（）《》〈〉【】…—·～￥％℃±×÷°")


def gb2312_chars() -> list[str]:
    """GB2312 全部可解码字符(含一级 3755 + 二级 3689 + 符号区)。"""
    out: list[str] = []
    for hi in range(0xA1, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                out.append(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return out


def build_subset(src: Path, chars: list[str]) -> bytes:
    from fontTools import subset

    opts = subset.Options()
    opts.hinting = False          # 屏幕显示不需要 hinting(还能再省一点)
    opts.glyph_names = False
    opts.notdef_outline = True    # 保留 .notdef,万一仍缺字也有占位而不是崩溃
    opts.recalc_bounds = True
    opts.drop_tables += ["DSIG"]

    font = subset.load_font(str(src), opts)
    subsetter = subset.Subsetter(options=opts)
    subsetter.populate(text="".join(chars))
    subsetter.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="子集化桌宠字体")
    ap.add_argument("--font", type=Path, default=FONT, help="目标字体文件")
    ap.add_argument("--dry-run", action="store_true", help="只报体积,不写盘")
    args = ap.parse_args(argv)

    if not args.font.is_file():
        print(f"错误:找不到字体 {args.font}", file=sys.stderr)
        return 2

    before = args.font.stat().st_size
    chars = ASCII + PUNCT + gb2312_chars()
    data = build_subset(args.font, chars)

    print(f"字符集: ASCII {len(ASCII)} + 标点 {len(PUNCT)} + GB2312 {len(gb2312_chars())}")
    print(f"体积  : {before / 1048576:.2f} MB → {len(data) / 1048576:.2f} MB"
          f" (省 {100 * (1 - len(data) / before):.0f}%)")

    if args.dry_run:
        print("(--dry-run:未写盘)")
        return 0

    args.font.write_bytes(data)
    print(f"已写回: {args.font}")
    print("还原方法: git checkout -- apps/desktop/assets/fonts/loli.ttf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
