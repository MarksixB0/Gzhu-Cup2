# -*- coding: utf-8 -*-
"""daytools 组件:日桶层的两个确定性工具(纯函数、零 IO、零 LLM)。

供复核调查通道的确定性抓手与观测面消费:
  - slot_baseline / compare_to_baseline:分时点个人基线 ——
    回答"这个时段他通常什么水平",避免把固定作息模式当异常;
  - find_similar_days:按(轮次,负面水平,作息相位)找历史相似日。

输入 ``day_buckets`` 接受 {day: bucket} 或 [(day, bucket)],与
ticks.list_day_buckets 的返回兼容;旧日桶没有分段字段时,相应时段
显式降级(不产生基线样本),不把缺省当真值。
"""
from __future__ import annotations

import math
from typing import Optional

# 时段划分:每 6 小时一段(2026-10-24 用户裁定 4 段方案)
SLOTS = ("dawn", "morning", "afternoon", "evening")
SLOT_BOUNDS = {"dawn": (0, 6), "morning": (6, 12),
               "afternoon": (12, 18), "evening": (18, 24)}

SLOT_MIN_N = 3          # 时段基线至少要有的样本天数,不足则该段不可用
SCALE_FLOOR = 0.05      # 基线尺度地板:常量基线下 MAD/std 均为 0,z 无意义,不许爆
BASELINE_DAYS = 28      # 时段基线回看窗(自然日)
SIMILAR_DAYS = 14       # 相似日检索回看窗(自然日)
SIMILAR_TOP_K = 3       # 相似日返回条数

# 相似日距离权重:负面水平为主,轮次与相位为辅
W_COUNT = 1.0
W_NEG = 2.0
W_PHASE = 1.0


def _slot_of_hour(hour: int) -> str:
    for slot, (lo, hi) in SLOT_BOUNDS.items():
        if lo <= hour < hi:
            return slot
    return SLOTS[0]


def _norm_buckets(day_buckets) -> list:
    """归一输入为按天升序的 [(day_key, bucket)]。"""
    items = day_buckets.items() if isinstance(day_buckets, dict) else day_buckets
    return sorted(((str(k), v or {}) for k, v in items), key=lambda kv: kv[0])


def _median(vals: list) -> float:
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _mad_scale(vals: list) -> float:
    """稳健尺度:1.4826·MAD,回退标准差,再兜绝对地板(常量基线 z 不爆)。"""
    m = _median(vals)
    mad = _median([abs(v - m) for v in vals])
    scale = 1.4826 * mad
    if scale < 1e-6 and len(vals) >= 2:
        mu = sum(vals) / len(vals)
        scale = math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))
    return max(scale, SCALE_FLOOR)


def _phase_hours(b: dict) -> Optional[float]:
    """日桶的作息重心小时(0-24);合力太弱(作息不集中)返回 None。"""
    sin_sum = float(b.get("hour_sin_sum") or 0.0)
    cos_sum = float(b.get("hour_cos_sum") or 0.0)
    n = math.hypot(sin_sum, cos_sum)
    if n < 1e-6:
        return None
    h = math.atan2(sin_sum, cos_sum) * 24.0 / (2.0 * math.pi)
    return (h + 24.0) % 24.0


def slot_baseline(day_buckets, days: int = BASELINE_DAYS) -> dict:
    """分时点个人基线:{slot: {"mu", "n"}};样本不足的时段不出现。

    每个样本 = 某天该时段的负面均值(slot_neg_sum/slot_neg_n;
    该天该时段无负面观测则不产生样本)。mu 取中位数。
    """
    items = _norm_buckets(day_buckets)[-max(1, int(days)):]
    samples = {s: [] for s in SLOTS}
    for _, b in items:
        for slot in SLOTS:
            n = int(b.get("slot_%s_neg_n" % slot, 0) or 0)
            if n <= 0:
                continue
            samples[slot].append(float(b.get("slot_%s_neg_sum" % slot, 0.0)) / n)
    return {s: {"mu": round(_median(v), 4), "n": len(v)}
            for s, v in samples.items() if len(v) >= SLOT_MIN_N}


def compare_to_baseline(day_buckets, value: float, now=None,
                        days: int = BASELINE_DAYS) -> dict:
    """当前负面水平相对其所在时段基线的偏差。

    返回 {"slot", "value", "mu", "delta", "z", "n"};该时段基线样本
    不足 SLOT_MIN_N 时返回 {"slot", "ok": False, "n"}(数据不足,
    不下结论)。
    """
    from datetime import datetime
    hour = datetime.fromtimestamp(float(now)).hour if now is not None \
        else datetime.now().hour
    slot = _slot_of_hour(int(hour))
    base = slot_baseline(day_buckets, days=days).get(slot)
    value = float(value)
    if base is None:
        return {"slot": slot, "ok": False, "value": round(value, 4),
                "n": slot_baseline(day_buckets, days=days).get(slot, {}).get("n", 0)}
    mu = base["mu"]
    vals = []
    for _, b in _norm_buckets(day_buckets)[-max(1, int(days)):]:
        n = int(b.get("slot_%s_neg_n" % slot, 0) or 0)
        if n > 0:
            vals.append(float(b.get("slot_%s_neg_sum" % slot, 0.0)) / n)
    delta = value - mu
    return {"slot": slot, "ok": True, "value": round(value, 4),
            "mu": round(mu, 4), "delta": round(delta, 4),
            "z": round(delta / _mad_scale(vals), 4), "n": base["n"]}


def find_similar_days(day_buckets, target: Optional[dict] = None,
                      days: int = SIMILAR_DAYS, k: int = SIMILAR_TOP_K) -> list:
    """按(轮次,负面均值,作息相位)找历史相似日,返回距离升序 top-k。

    ``target`` 缺省取回看窗内最近一个有负面观测的日桶;形如
    {"count", "neg_avg", "phase_hours"}。距离 = 加权欧氏:轮次用
    log1p 差、负面用原值差、相位用环形小时差(/12 归一);某侧缺
    相位时相位项不计入并按剩余权重重归一。目标日本身不参与候选。
    """
    items = _norm_buckets(day_buckets)[-max(1, int(days)):]
    # 目标:显式传入,或取最近有负面观测的日
    tgt = dict(target) if target else None
    tgt_day = None
    if tgt is None:
        for day, b in reversed(items):
            if int(b.get("neg_n", 0) or 0) > 0:
                tgt_day = day
                tgt = {"count": int(b.get("count", 0) or 0),
                       "neg_avg": float(b["neg_sum"]) / int(b["neg_n"]),
                       "phase_hours": _phase_hours(b)}
                break
    if tgt is None:
        return []
    tc = math.log1p(max(0.0, float(tgt.get("count") or 0.0)))
    tn = float(tgt.get("neg_avg") or 0.0)
    tp = tgt.get("phase_hours")

    out = []
    for day, b in items:
        if tgt_day is not None and day == tgt_day:
            continue
        n = int(b.get("neg_n", 0) or 0)
        if n <= 0:                      # 无负面观测的日子不可比
            continue
        cn = float(b.get("neg_sum", 0.0)) / n
        cp = _phase_hours(b)
        num, den = 0.0, 0.0
        num += W_COUNT * (math.log1p(max(0.0, float(b.get("count", 0) or 0.0))) - tc) ** 2
        den += W_COUNT
        num += W_NEG * (cn - tn) ** 2
        den += W_NEG
        if tp is not None and cp is not None:
            dh = abs(cp - tp) % 24.0
            num += W_PHASE * (min(dh, 24.0 - dh) / 12.0) ** 2
            den += W_PHASE
        out.append({"day": day,
                    "count": int(b.get("count", 0) or 0),
                    "neg_avg": round(cn, 4),
                    "phase_hours": round(cp, 2) if cp is not None else None,
                    "distance": round(math.sqrt(num / den), 4)})
    out.sort(key=lambda r: (r["distance"], r["day"]))
    return out[:max(1, int(k))]
