# -*- coding: utf-8 -*-
"""一次性回填:给「相对时间锚定」上线**之前**写入的事实补上 event_date。

为什么需要
----------
``long_memory.dates.resolve_event_date`` 是随「写入时锚定」一起上线的,只对
**之后**新固化的事实生效。此前落库的事实文本里若含「明天/下周」等相对时间词,
却没有 event_date —— 检索侧无法锚定,过一天就错位(例:「用户明天要参加考试」
第二天仍被读成「明天」)。

本脚本对现有事实做**幂等回填**:
  - 用 ``first_seen``(首次落库时刻,最接近用户说出那句话的时间)作锚点;
  - 只处理**尚无 event_date** 的事实,已有的跳过;
  - 解析不出时间词的保持原样(不改动)。
可反复运行,不会重复改写。

用法::

    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/backfill_event_dates.py            # dry-run
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/backfill_event_dates.py --apply    # 真正写库
    .venv\\Scripts\\python.exe src/gzhu_cup2/agent/tools/backfill_event_dates.py --user xxx
"""
import argparse
import os
import sys
from datetime import datetime


# 默认走 sqlite 持久化后端(与运行时一致);测试/演示可另设 LONG_MEMORY_DIR。
os.environ.setdefault("LONG_MEMORY_BACKEND", "sqlite")

from gzhu_cup2.agent.config import config as cfg          # noqa: E402
from gzhu_cup2.agent.long_memory import dates as mdates   # noqa: E402
from gzhu_cup2.agent.long_memory import store as mstore   # noqa: E402


def _anchor(value: dict) -> datetime:
    """回填锚点:优先 first_seen,回落 last_seen / now(不抛异常)。"""
    ts = value.get("first_seen") or value.get("last_seen")
    try:
        return datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填事实的 event_date(相对时间锚定)")
    ap.add_argument("--user", default=cfg.MEMORY_USER_KEY, help="用户键(默认 config 的当前用户)")
    ap.add_argument("--apply", action="store_true", help="真正写库(缺省只 dry-run)")
    args = ap.parse_args(argv)

    ns = mstore.ns_facts(args.user)
    items = mstore.list_items(ns)
    print(f"后端: {mstore.store_backend_name()}   用户: {args.user}   事实数: {len(items)}")

    planned: list[tuple[str, dict, str, str]] = []  # (key, value, anchor_day, event_date)
    skipped = 0
    unresolved: list[tuple[str, str]] = []
    for it in items:
        v = dict(it.value or {})
        text = str(v.get("text") or "")
        if v.get("event_date"):
            skipped += 1
            continue
        anchor = _anchor(v)
        ed = mdates.resolve_event_date(text, anchor)
        if ed:
            planned.append((it.key, v, anchor.date().isoformat(), ed))
        else:
            unresolved.append((it.key, text))

    tag = "APPLY" if args.apply else "DRY  "
    print(f"\n将回填 {len(planned)} 条  (已有 event_date 跳过 {skipped} 条, 无时间词 {len(unresolved)} 条)")
    for key, _v, anchor_day, ed in planned:
        print(f"  [{tag}] {key}  {_v.get('text')!r}  锚点={anchor_day} → event_date={ed}")

    if unresolved:
        print("\n无时间词 / 解析不出(保持原样):")
        for key, text in unresolved:
            print(f"  - {key}  {text!r}")

    if planned and args.apply:
        for key, v, _anchor_day, ed in planned:
            v["event_date"] = ed
            mstore.put_item(ns, key, v)
        print(f"\n已写库 {len(planned)} 条。")
    elif planned:
        print("\n(dry-run,未写库;加 --apply 落库)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
