# -*- coding: utf-8 -*-
"""算法层骨架:对外唯一入口 + 流水线接线 + 逐段 trace。

流水线:fetch(I/O) → features(特征) → score(五路径分数) → machine(状态机定档)。
每段执行完往 trace 记一条(阶段 / 耗时 / 关键输出摘要),随 analyze 返回,
档位异常时可逐段回看中间量。新组件按触发条件插在对应段之间,不改既有段。
"""
from __future__ import annotations

import time
from typing import Optional

from gzhu_cup2.agent.algo.contracts import RawData, to_plain

# 供外部(洞察页"数据够不够")引用的数据充足度常量。
from gzhu_cup2.agent.algo.components.score import MIN_RECENT_DAYS  # noqa: F401

__all__ = ["analyze", "tier_of", "MIN_RECENT_DAYS"]


class _Trace:
    """逐段耗时与关键输出的收集器。"""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def step(self, stage: str, note: str = "") -> None:
        self.items.append({"stage": stage, "note": note,
                           "ts": round(time.time(), 3)})


def analyze(user: str, now: Optional[float] = None,
            prev_tier: Optional[str] = None,
            prev_since: Optional[float] = None,
            protected_until: Optional[float] = None,
            _trace: Optional[_Trace] = None) -> Optional[dict]:
    """统一分析入口,供 risk_state / 评估 / 洞察消费。

    返回:
      tier          对外档位(stable/watch/elevated),已含状态机迟滞。
      tier_instant  纯分数的瞬时档(不含迟滞),用于审计抖动
      mode          normal / lapse / lapse_decay / insufficient
      reason        档位成因(排障用,绝不进用户可见文案)
      hold          True = 档位由"沉默/数据不足/保护期"保持,调用方不应再套迟滞
      score         分数分解(完整可审计)
      features      特征快照
      trace         各阶段耗时与关键输出(排障用)
    """
    if not user:
        return None
    from gzhu_cup2.agent.algo.components import features as feat
    from gzhu_cup2.agent.algo.components import machine as mach
    from gzhu_cup2.agent.algo.components import score as sc_mod
    from gzhu_cup2.agent.algo.components import fetch as fetch_mod

    tr = _trace or _Trace()
    now = float(now) if now is not None else time.time()

    t0 = time.perf_counter()
    raw = fetch_mod.fetch(user, now=now)
    tr.step("fetch", "buckets=%s" % (len(raw.buckets) if raw else 0))
    if raw is None:
        return {"tier": str(prev_tier or "stable"), "tier_instant": "stable",
                "mode": "insufficient", "reason": "no_buckets", "hold": True,
                "score": None, "features": None, "trace": tr.items}

    t0 = time.perf_counter()
    f = feat.compute(raw)
    tr.step("features", "coverage=%s" % (f or {}).get("coverage"))
    if f is None:
        return {"tier": str(prev_tier or "stable"), "tier_instant": "stable",
                "mode": "insufficient", "reason": "no_buckets", "hold": True,
                "score": None, "features": None, "trace": tr.items}

    t0 = time.perf_counter()
    sc = sc_mod.compute(f)
    tr.step("score", "s_total=%s" % sc.get("s_total"))

    t0 = time.perf_counter()
    res = mach.apply(f, sc, prev_tier, prev_since, now, protected_until)
    tr.step("machine", "%s/%s" % (res.get("tier"), res.get("reason")))

    p = res["params"]
    s = sc["s_total"]
    instant = ("elevated" if s >= p["enter_elevated"]
               else "watch" if s >= p["enter_watch"] else "stable")
    return {"tier": res["tier"], "tier_instant": instant, "mode": res["mode"],
            "reason": res["reason"], "hold": res["hold"],
            "score": to_plain(sc), "features": to_plain(f), "trace": tr.items}


def tier_of(user: str, now: Optional[float] = None) -> str:
    """对外主入口:返回用户当前的纵向守护档位 stable / watch / elevated。

    本模块不产生 crisis —— 对话内危机由 triage→handle_crisis 固定协议处理。

    ⚠ 这是无状态分数判定,不含落盘标记的降档保护;要"用户当前标记"
      请走 ``risk_state.update()`` / ``effective_tier()``。
    """
    r = analyze(user, now=now)
    return str((r or {}).get("tier") or "stable")
