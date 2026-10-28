# -*- coding: utf-8 -*-
"""fetch 组件:算法层唯一的 I/O 入口,读日桶、维度账本与活跃时刻。"""
from __future__ import annotations

import time
from typing import Optional

from gzhu_cup2.agent.algo.contracts import RawData
from gzhu_cup2.agent.long_memory import ledger, ticks

# 维度证据的扫描范围:与日桶保留期一致(90 天),更早的衰减后贡献不足 2%。
DIM_SCAN_DAYS = 90


def fetch(user: str, now: Optional[float] = None) -> Optional[RawData]:
    """读出该用户的全部原始观测;无任何日桶 → None。"""
    if not user:
        return None
    now = float(now) if now is not None else time.time()
    buckets = {k: v for k, v in ticks.list_day_buckets(user, days=None, now=now)}
    if not buckets:
        return None
    return RawData(
        now=now,
        buckets=buckets,
        dims_daily=ledger.scan_daily(user, days=DIM_SCAN_DAYS, now=now),
        last_ts=ticks.last_activity_ts(user, now=now),
    )
