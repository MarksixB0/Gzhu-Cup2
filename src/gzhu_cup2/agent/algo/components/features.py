# -*- coding: utf-8 -*-
"""features 组件:把日桶与维度账本换算成可比较的量。

三条约定:
1. **时间轴是自然日**。按自然日切片,缺失日单独计数
   (coverage / idle_days 都是独立信号)。
2. **尺度用收缩估计**。日级 neg_avg 只有几个离散档位,个体 σ 常坍缩到 0,
   z 会退化。用 σ²=(n·s²+κ·σ0²)/(n+κ) 给 σ 一个下界。
3. **日桶必须是充分统计量**。方差/峰值/作息相位若不在写入时算就补不回来;
   旧桶缺字段按"缺失"处理,不伪造 0。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

from gzhu_cup2.agent.algo.components import bocpd
from gzhu_cup2.agent.algo.contracts import Features, RawData

# ------------------------------------------------------------------ 窗口常量
# 近窗取 7 个自然日:容纳"隔天来一次"的稀疏用户,并与周节律对齐
# (周末掉线是正常行为,靠覆盖度分项与真退缩区分)。
RECENT_DAYS = 7
FAST_DAYS = 3
# 快尺度要求"至少 2 天单日高出基线 0.10":单日波动不算。
FAST_DAY_DELTA = 0.10
# 基线锚定 90 天(与日桶保留期一致),刨去最近 14 天作隔离带,
# 隔离带长度可自适应放宽(见 _pick_base_keys)。
ANCHOR_DAYS = 90
BASE_EXCLUDE_DAYS = 14
# 收缩先验:σ0=0.08 是平稳人格日间波动的量级;κ=4 表示先验约等于 4 个观测日。
K_PRIOR = 4.0
SIGMA_PRIOR = 0.08
# 基线最少需要的记录日数;不足时逐级缩短隔离带。
BASE_MIN_DAYS = 6
# 参与度变点检测的序列窗:近 28 天日轮次(缺失日计 0 —— 不来本身就是参与度)。
ACTIVITY_CP_DAYS = 28
# 隔离带候选(天),从长到短试。
BASE_EXCLUDE_LADDER = (BASE_EXCLUDE_DAYS, 10, 7, 4, 2)
# 症状族密度的分母下限:按"至少 5 天"算,不随样本量缩小。
MIN_DENSITY_SPAN = 5
# ---- 维度证据的衰减累积 ----
# 当前档:5 天半衰期的加权平均,只统计最近 14 天内的证据 ——
# 加权平均只反映"分布"不反映"强度",窗口不截断的话,一个月前的一条
# 孤点证据在"当前档"里仍是满分。
DIM_CUR_HALF_LIFE = 5.0
DIM_CUR_WINDOW_DAYS = 14
# 基线档:28 天半衰期,反映"平时是什么样"。
DIM_BASE_HALF_LIFE = 28.0
# 基线隔离带:最近 7 天不参与基线,避免当前值抬高自己的基线。
DIM_BASE_GAP_DAYS = 7


def _day_key(d) -> str:
    return d.strftime("%Y-%m-%d")


def _day_keys(anchor, days: int) -> list:
    """从 anchor 往前数 days 个自然日,升序。"""
    return [_day_key(anchor - timedelta(days=i)) for i in range(days - 1, -1, -1)]


def _f(v, default=None):
    try:
        x = float(v)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def _i(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _phase_hours(sin_sum: float, cos_sum: float) -> Optional[float]:
    """圆周均值 → 一天的"重心小时"(0-24)。合力太弱(作息不集中)时返回 None。

    合力长度 R 度量作息集中度;R 小时"重心小时"没有意义,不能拿来算相位位移。
    """
    n = math.hypot(sin_sum, cos_sum)
    if n < 1e-6:
        return None
    ang = math.atan2(sin_sum, cos_sum)      # 注意 atan2(sin, cos) 才是角度
    h = ang * 24.0 / (2.0 * math.pi)
    return (h + 24.0) % 24.0


def _wrap_hours(delta: float) -> float:
    """把小时差折到 [-12, 12](相位是环形的:23 点 → 1 点是 +2 小时,不是 −22)。"""
    return (delta + 12.0) % 24.0 - 12.0


def _window(buckets: dict, keys: list) -> dict:
    """统计一个自然日窗口。

    ``buckets`` 是 {day: bucket};缺失日**保留在 keys 里**但不产生观测 ——
    这样 ``coverage = n_days / len(keys)`` 才有意义(它是独立信号,不是噪声)。
    """
    neg_daily = []          # 每日 neg 均值(只含有记录且 neg_n>0 的日)
    floor_daily = []        # 每日低水位:各分段 neg 均值的最小值(状态最好的时段)
    n_turns = 0
    n_days = 0
    withdraw_days = 0
    conflict_days = 0
    anhedonia_days = 0
    somatic_days = 0
    night_turns = 0
    sin_sum = 0.0
    cos_sum = 0.0
    hour_n = 0
    within_ss = 0.0         # 组内离差平方和(用于池化方差)
    within_df = 0

    for k in keys:
        b = buckets.get(k)
        if not b:
            continue
        c = _i(b.get("count"))
        if c <= 0:
            continue
        n_days += 1
        n_turns += c
        nn = _i(b.get("neg_n"))
        if nn > 0:
            s = _f(b.get("neg_sum"), 0.0) or 0.0
            avg = s / nn
            neg_daily.append(avg)
            slots = []
            for slot in ("dawn", "morning", "afternoon", "evening"):
                sn = _i(b.get("slot_%s_neg_n" % slot))
                if sn > 0:
                    slots.append(
                        (_f(b.get("slot_%s_neg_sum" % slot), 0.0) or 0.0) / sn)
            # 无分段数据的旧桶回退为日均值(低水位与均值同值,不产生新信息)。
            floor_daily.append(min(slots) if slots else avg)
            ss = _f(b.get("neg_sumsq"))
            if ss is not None:
                # Σ(x−x̄)² = Σx² − (Σx)²/n
                within_ss += max(0.0, ss - s * s / nn)
                within_df += max(0, nn - 1)
        if _i(b.get("withdraw_count")) > 0:
            withdraw_days += 1
        if _i(b.get("conflict_count")) > 0:
            conflict_days += 1
        if _i(b.get("anhedonia_count")) > 0:
            anhedonia_days += 1
        if _i(b.get("somatic_count")) > 0:
            somatic_days += 1
        night_turns += _i(b.get("night_count"))
        hn = _i(b.get("hour_n"))
        if hn > 0:
            sin_sum += _f(b.get("hour_sin_sum"), 0.0) or 0.0
            cos_sum += _f(b.get("hour_cos_sum"), 0.0) or 0.0
            hour_n += hn

    def _mean(v):
        return (sum(v) / len(v)) if v else None

    def _var(v):
        if len(v) < 2:
            return None
        m = sum(v) / len(v)
        return sum((x - m) ** 2 for x in v) / (len(v) - 1)

    neg_mean = _mean(neg_daily)
    neg_var_between = _var(neg_daily)
    # 日级均值的方差在稀疏数据上极不稳(1~2 个点),所以另有池化组内方差;
    # 取二者较大值做 s²:既不低估"日间差异",也不低估"日内波动"。
    var_within = (within_ss / within_df) if within_df > 0 else None
    cands = [v for v in (neg_var_between, var_within) if v is not None]
    s2 = max(cands) if cands else 0.0

    phase = _phase_hours(sin_sum, cos_sum) if hour_n > 0 else None
    r_len = (math.hypot(sin_sum, cos_sum) / hour_n) if hour_n > 0 else 0.0

    return {
        "n_days": n_days,
        "n_turns": n_turns,
        "neg_days": len(neg_daily),
        "neg_daily": [round(v, 4) for v in neg_daily],
        "neg_mean": none_round(neg_mean),
        "neg_median": none_round(_median(neg_daily)),
        "floor_median": none_round(_median(floor_daily)),
        "neg_var": none_round(s2),
        "withdraw_days": withdraw_days,
        "conflict_days": conflict_days,
        "anhedonia_days": anhedonia_days,
        "somatic_days": somatic_days,
        "night_turns": night_turns,
        "phase_h": none_round(phase, 3),
        "phase_r": round(r_len, 3),
        "turn_rate": none_round(n_turns / len(keys)) if keys else None,
    }


def _pick_base_keys(today, buckets: dict) -> list:
    """选基线窗口:优先"离变化区足够远",数据不够时逐级缩短隔离带。

    固定 14 天隔离带会让短历史用户在前期完全没有基线(delta 恒为 None),
    故历史不足时自动退让到更短的隔离带。
    """
    best, best_n = [], -1
    for exclude in BASE_EXCLUDE_LADDER:
        end = today - timedelta(days=exclude)
        start = today - timedelta(days=ANCHOR_DAYS - 1)
        if end < start:
            continue
        keys = [_day_key(start + timedelta(days=i))
                for i in range((end - start).days + 1)]
        n = sum(1 for k in keys
                if _i((buckets.get(k) or {}).get("count")) > 0)
        if n > best_n:
            best, best_n = keys, n
        if n >= BASE_MIN_DAYS:
            return keys
    return best


def _median(vals: list) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def none_round(v, nd=4):
    return None if v is None else round(float(v), nd)


def _dim_decayed(daily: dict) -> dict:
    """把逐日维度证据折算成当前档、基线档与两者之差。

    daily 形如 {维度: [(age_days, level), ...]},同维度同一天已去重取最高档。
    权重按天指数衰减:两天后的 level 2 比上周的 level 2 更算数。
    基线排除最近 DIM_BASE_GAP_DAYS 天,当前值不会抬高自己的基线。

    level_base = 0 表示扫描窗口内该维度没有基线期证据(以前没提过这件事)。
    """
    out: dict[str, dict] = {}
    for key, pts in (daily or {}).items():
        if not pts:
            continue
        cur_n = cur_d = 0.0
        base_n = base_d = 0.0
        days = 0
        latest = 0
        for age, level in pts:
            try:
                age = int(age)
                level = int(level)
            except (TypeError, ValueError):
                continue
            if level <= 0:
                continue
            days += 1
            if age == 0 or latest == 0:
                latest = level
            if age < DIM_CUR_WINDOW_DAYS:
                w_cur = 0.5 ** (age / DIM_CUR_HALF_LIFE)
                cur_n += level * w_cur
                cur_d += w_cur
            if age >= DIM_BASE_GAP_DAYS:
                w_base = 0.5 ** (age / DIM_BASE_HALF_LIFE)
                base_n += level * w_base
                base_d += w_base
        if days <= 0:
            continue
        level_w = cur_n / cur_d if cur_d > 0 else 0.0
        level_base = base_n / base_d if base_d > 0 else 0.0
        out[key] = {
            "level": latest,
            "days": days,
            "level_w": round(level_w, 3),
            "level_base": round(level_base, 3),
            "shift": round(max(0.0, level_w - level_base), 3),
        }
    return out


def compute(raw: RawData) -> Optional[Features]:
    """算出该用户当前的全部特征。无任何日桶 → None。

    主要字段见 contracts.Features;全部是原始量,不做阈值判断(判断在 score)。
    """
    buckets = raw.buckets
    if not buckets:
        return None
    now = raw.now
    dims = _dim_decayed(raw.dims_daily)

    today = datetime.fromtimestamp(now).date()
    recent_keys = _day_keys(today, RECENT_DAYS)
    fast_keys = _day_keys(today, FAST_DAYS)
    base_keys = _pick_base_keys(today, buckets)

    recent = _window(buckets, recent_keys)
    fast = _window(buckets, fast_keys)
    base = _window(buckets, base_keys)

    # ---- 变化幅度(两个时间尺度) ----
    # 慢尺度答"这一周是不是比之前差",快尺度答"这三天是不是突然差";
    # 快尺度在 score 里封顶 0.75。基线中心取中位数。
    base_center = base["neg_median"]
    delta = None
    if recent["neg_mean"] is not None and base_center is not None:
        delta = recent["neg_mean"] - base_center
    delta_fast = None
    fast_drift_days = 0
    if fast["neg_mean"] is not None and base_center is not None:
        # 快尺度还要求窗口里**至少 2 天**单日明显高于基线。
        fast_drift_days = sum(
            1 for v in (fast.get("neg_daily") or [])
            if v >= float(base_center) + FAST_DAY_DELTA)
        if fast_drift_days >= 2:
            delta_fast = fast["neg_mean"] - base_center

    # ---- 收缩尺度 ----
    # s² 为 0 时(日间完全一致)σ 仍等于先验下界,不会坍缩到 0。
    n = max(1, int(base.get("neg_days") or 0))
    s2 = float(base.get("neg_var") or 0.0)
    sigma = math.sqrt((n * s2 + K_PRIOR * SIGMA_PRIOR ** 2) / (n + K_PRIOR))

    d_effect = (delta / sigma) if (delta is not None and sigma > 1e-9) else None
    z = None
    if delta is not None and sigma > 1e-9:
        nr = max(1, int(recent.get("neg_days") or 0))
        nb = max(1, n)
        z = delta / (sigma * math.sqrt(1.0 / nr + 1.0 / nb))

    # ---- 缺失 / 参与度 ----
    idle = None
    if raw.last_ts:
        idle = int((now - float(raw.last_ts)) // 86400.0)
    coverage = (recent["n_days"] / float(RECENT_DAYS)) if RECENT_DAYS else 0.0

    engagement_drop = None
    rr = recent.get("turn_rate")
    br = base.get("turn_rate")
    if rr is not None and br:
        engagement_drop = max(0.0, min(1.0, 1.0 - rr / br))

    # ---- 作息相位位移 ----
    phase_shift = None
    pr, pb = recent.get("phase_h"), base.get("phase_h")
    # 两边作息都够集中(合力长度 ≥0.25,约 4 小时内)才比较相位。
    if pr is not None and pb is not None \
            and float(recent.get("phase_r") or 0) >= 0.25 \
            and float(base.get("phase_r") or 0) >= 0.25:
        phase_shift = _wrap_hours(pr - pb)

    # ---- 参与度变点(BOCPD):近 28 天日轮次序列,缺失日计 0 ----
    # 只产出置信度摘要;阈值判断在 score。门槛是 28 天总轮次 ≥5:
    # 低于此的稀疏用户 activity_cp=None,前驱路径与 lapse_signal
    # 例外放行对该用户整段关闭。
    activity_cp = None
    act_keys = _day_keys(today, ACTIVITY_CP_DAYS)
    act = [float(_i((buckets.get(k) or {}).get("count"))) for k in act_keys]
    if sum(act) >= 5:
        activity_cp = bocpd.change_summary(act)

    # 密度分母见 MIN_DENSITY_SPAN(至少按 5 天算,不跟着样本变小)。
    span = max(MIN_DENSITY_SPAN, recent["n_days"])
    span_f = FAST_DAYS if fast["n_days"] >= 2 else 0
    fams = ("withdraw", "conflict", "anhedonia", "somatic")
    return {
        "recent": recent,
        "fast": fast,
        "base": base,
        "delta": none_round(delta),
        "delta_fast": none_round(delta_fast),
        "sigma_shrunk": round(float(sigma), 4),
        "d_effect": none_round(d_effect, 3),
        "z": none_round(z, 3),
        "coverage": round(float(coverage), 3),
        "idle_days": idle,
        "engagement_drop": none_round(engagement_drop, 3),
        "phase_shift_h": none_round(phase_shift, 2),
        "activity_cp": activity_cp,
        # 近窗夜间轮次占比:观测面用(进 evidence)。
        "night_rate": (round(recent["night_turns"] / recent["n_turns"], 3)
                       if recent["n_turns"] else 0.0),
        # 密度分母一并返回:前端把 0.4 翻成"大约几天"时必须用它。
        # 分母是 max(5, n_days),不等于 RECENT_DAYS(7),按 7 乘会虚报。
        "density_span": int(span),
        "withdraw_rate": round(recent["withdraw_days"] / span, 3),
        "conflict_rate": round(recent["conflict_days"] / span, 3),
        "anhedonia_rate": round(recent["anhedonia_days"] / span, 3),
        "somatic_rate": round(recent["somatic_days"] / span, 3),
        # 近 3 天的同类密度:与情绪 fast 尺度同构,样本少、权重更低。
        "rates_fast": {f: (round(fast["%s_days" % f] / span_f, 3) if span_f else 0.0)
                       for f in fams},
        # 维度证据的衰减折算;原文引用不进入特征(它可能很敏感)。
        "dims": dims,
        "dim_hist_days": len(buckets),
    }
