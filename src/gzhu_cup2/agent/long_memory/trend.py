# -*- coding: utf-8 -*-
"""趋势层(冷轨):把时序层日桶聚合成窗口指标 —— 纯算术、无 LLM、确定性。

设计约定:
  - 准确度来源是「多次粗判的统计聚合」,不是单次精判;
  - 只保留三个原始量(整体心情 mood_index / 负面占比 negative_share / 危险事件 risk),
    其余(波动、走向)全部由此派生,便于测试与审计;
  - 本模块只读 ticks 日桶,零 IO 副作用;供 profile(近况/特质/触发)与未来守护引擎消费。
"""
import math
import time
from typing import Optional

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import ticks
from gzhu_cup2.agent.long_memory import tags


def _avg_of(bucket: dict, sum_key: str, n_key: str) -> Optional[float]:
    n = int(bucket.get(n_key, 0))
    if n <= 0:
        return None
    return float(bucket.get(sum_key, 0.0)) / n


def _stdev(vals: list) -> float:
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return math.sqrt(var)


def _median(sorted_vals: list) -> float:
    """已排序列表的中位数(偶数取两中值平均)。"""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def _quantile(sorted_vals: list, q: float) -> float:
    """已排序列表的 q 分位数(线性插值)。

    q=0.5 与 _median 一致(偶数仍取两中值平均),q=0.25 即下四分位(Q1)。
    采用 pos=q×(n−1) 线性插值 —— 定义简单、确定性、便于手算与测试锁住。
    """
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    pos = float(q) * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def _center_scale(vals: list) -> tuple[float, float, str]:
    """返回 (中心, 尺度, 估计量名)。

    robust  = Q1(下四分位,cfg.MEMORY_BASELINE_QUANTILE) / 1.4826×MAD ——
              抗离群,且**不会"升进"持续恶化里**:median 只要窗口内 >50% 的日子
              都是低落日就会被拖进低谷;Q1 要 >75% 的日子低到同水平才会被拖走。
              配合 90 天锚定窗口(见 baseline_and_drift),健康史占窗口 ≥25% 时
              Q1 就钉在健康水平上,多周级恶化无法把基线整体拖下去。
    classic = mean / 总体 σ —— 旧口径(对少数极端点敏感)。
    1.4826 使 MAD 在正态分布下与 σ 可比,因此下游 z 阈值口径保持不变。
    按调用时刻读 cfg.MEMORY_BASELINE_ROBUST(默认 True),便于运行时切换对照;
    cfg.MEMORY_BASELINE_QUANTILE=0.5 时 robust 完全回到旧 median 口径。
    """
    q = float(getattr(cfg, "MEMORY_BASELINE_QUANTILE", 0.5) or 0.5)
    name = "median_mad" if q == 0.5 else "q%d_mad" % int(round(q * 100))
    vals = [float(v) for v in vals]
    n = len(vals)
    if n == 0:
        return 0.0, 0.0, name
    if getattr(cfg, "MEMORY_BASELINE_ROBUST", True):
        ordered = sorted(vals)
        center = _quantile(ordered, q)
        mad = _median(sorted(abs(v - center) for v in ordered))
        return center, 1.4826 * mad, name
    m = sum(vals) / n
    var = sum((v - m) ** 2 for v in vals) / n
    return m, math.sqrt(var), "mean_sigma"


def window_stats(user: str, days: Optional[int] = None,
                 now: Optional[float] = None) -> Optional[dict]:
    """近 N 天窗口指标(无任何数据返回 None)。

    返回:
      days / turn_count / neg_avg(按天等权) / mood_index(=1-neg_avg)
      negative_share(负面情绪标签占比) / dominant_emotion / emotions(直方图)
      volatility(逐日均值波动) / trend_dir(worsening/improving/stable) / risk_max_rank
    """
    days = int(days or cfg.MEMORY_TREND_RECENT_DAYS)
    buckets = ticks.list_day_buckets(user, days=days, now=now)
    if not buckets:
        return None

    turn_count = sum(int((b or {}).get("count", 0)) for _, b in buckets)

    # 逐日负向均值(只有当天有负向值才参与;缺失不等于 0)
    daily_neg: list[tuple[str, float]] = []
    for day, b in buckets:
        avg = _avg_of(b or {}, "neg_sum", "neg_n")
        if avg is not None:
            daily_neg.append((str(day), avg))
    neg_avg = (sum(v for _, v in daily_neg) / len(daily_neg)) if daily_neg else None
    volatility = _stdev([v for _, v in daily_neg]) if len(daily_neg) >= 2 else 0.0

    # 情绪直方图 / 主导情绪 / 负面占比
    emotions: dict[str, int] = {}
    for _, b in buckets:
        for emo, n in ((b or {}).get("emotions") or {}).items():
            emotions[emo] = emotions.get(emo, 0) + int(n)
    dominant = "neutral"
    if emotions:
        dominant = max(emotions, key=emotions.get)
    neg_count = sum(n for e, n in emotions.items() if e in tags.NEG_EMOTIONS)
    total_count = sum(emotions.values())
    negative_share = (neg_count / total_count) if total_count else None

    # 走向:逐日负向序列前/后两半对比(负向升 = 变差)
    trend_dir = "stable"
    if len(daily_neg) >= 2:
        seq = [v for _, v in daily_neg]
        half = max(1, len(seq) // 2)
        early = sum(seq[:half]) / len(seq[:half])
        late = sum(seq[half:]) / len(seq[half:])
        diff = late - early
        if diff > 0.08:
            trend_dir = "worsening"
        elif diff < -0.08:
            trend_dir = "improving"

    risk_max = max((int((b or {}).get("risk_max_rank", 0)) for _, b in buckets),
                   default=0)
    mood_index = None
    if neg_avg is not None:
        mood_index = max(0.0, min(1.0, 1.0 - neg_avg))

    return {
        "days": len(buckets),
        "turn_count": turn_count,
        "neg_avg": round(neg_avg, 4) if neg_avg is not None else None,
        "mood_index": round(mood_index, 4) if mood_index is not None else None,
        "negative_share": round(negative_share, 4) if negative_share is not None else None,
        "dominant_emotion": dominant,
        "emotions": emotions,
        "volatility": round(volatility, 4),
        "trend_dir": trend_dir,
        "risk_max_rank": risk_max,
    }


def _daily_neg_series(user: str, days: int, now: float) -> list:
    """近 days 天内有负向记录的各日 [(day, avg)],按日升序。"""
    out = []
    for day, b in ticks.list_day_buckets(user, days=max(1, int(days)), now=now):
        avg = _avg_of(b or {}, "neg_sum", "neg_n")
        if avg is not None:
            out.append((str(day), avg))
    out.sort(key=lambda kv: kv[0])
    return out


def baseline_and_drift(user: str, now: Optional[float] = None) -> Optional[dict]:
    """个人基线 + 恶化检测(纯算术,零 LLM;相对用户自身,单轮波动永不触发)。

    口径:
      - 基线 = 近 MEMORY_BASELINE_ANCHOR_WINDOW_DAYS(90)天内、刨去最近
        MEMORY_DRIFT_WINDOW_DAYS 个"有记录日"之后,较早部分的逐日负向
        中心 μ 与尺度 σ。估计量由 MEMORY_BASELINE_ROBUST 选择:
        默认 Q1 + 1.4826×MAD(见 _center_scale);置 False 用均值 + 总体 σ。
        返回值带 estimator 标记口径;
      - 漂移 = 最近至多 MEMORY_DRIFT_WINDOW_DAYS 个有记录日相对基线的 z=(avg−μ)/σ,
        且绝对位移 (avg−μ) ≥ MEMORY_DRIFT_MIN_ABS_DELTA,只判"变差"方向;
        兜底闸:位移 ≥ MEMORY_DRIFT_ABS_ALERT 时,即使 z 不显著也判漂移;
        连续漂移日 ≥ MEMORY_DRIFT_CONSECUTIVE_ALERT → sustained_worsening=True。

    返回 None = 数据不足以建立基线(基线点 <2)。无负向记录的日期不参与计算。
    """
    if not user:
        return None
    now = float(now) if now is not None else time.time()
    drift_n = max(1, int(cfg.MEMORY_DRIFT_WINDOW_DAYS))
    base_n = max(drift_n + 1, int(getattr(cfg, "MEMORY_BASELINE_ANCHOR_WINDOW_DAYS",
                                          cfg.MEMORY_BASELINE_WINDOW_DAYS)))
    series = _daily_neg_series(user, base_n, now)
    if len(series) < 2:
        return None
    split_at = max(0, len(series) - drift_n)
    base_pts = series[:split_at]
    drift_pts = series[split_at:]
    if len(base_pts) < 2:
        return None  # 基线点不足,不冒险判定

    mu, sigma, _estimator = _center_scale([v for _, v in base_pts])
    alert_z = float(cfg.MEMORY_DRIFT_Z_ALERT)
    # 绝对位移地板:z 之外再要求"确实变差了足够多",用于拦住 σ 坍缩时的噪声。
    # 阈值校准见 config 的 MEMORY_DRIFT_MIN_ABS_DELTA。
    min_abs = float(getattr(cfg, "MEMORY_DRIFT_MIN_ABS_DELTA", 0.0) or 0.0)
    # 绝对位移兜底闸:长期恶化膨胀 MAD 时 z 会钝化(z<1.0 但位移真实且大)。
    # 见 config.MEMORY_DRIFT_ABS_ALERT。置 0 = 关闭兜底(回到旧口径)。
    abs_alert = float(getattr(cfg, "MEMORY_DRIFT_ABS_ALERT", 0.0) or 0.0)

    rows = []
    for day, v in drift_pts:
        if sigma > 1e-9:
            z = (v - mu) / sigma
        else:
            z = 999.0 if v > mu else 0.0  # 基线零波动:高于基线即视为显著
        delta = v - mu
        # 同时记原始 z 与位移:排障/答辩时可逐条审计"这一天为什么算/不算漂移日"
        is_drift = (delta >= min_abs) and (
            z >= alert_z or (abs_alert > 0 and delta >= abs_alert)
        )
        rows.append({"day": day, "neg": round(v, 4), "z": round(z, 3),
                     "delta": round(delta, 4), "is_drift": bool(is_drift)})

    above = [r for r in rows if r["is_drift"]]
    consecutive = 0
    for r in reversed(rows):
        if r["is_drift"]:
            consecutive += 1
        else:
            break
    return {
        "mu": round(mu, 4),
        "sigma": round(sigma, 4),
        "estimator": _estimator,
        "baseline_days": len(base_pts),
        "drift_days": rows,
        "above_count": len(above),
        "consecutive_above": consecutive,
        "sustained_worsening": consecutive >= int(cfg.MEMORY_DRIFT_CONSECUTIVE_ALERT),
        "min_abs_delta": min_abs,
        "abs_alert": abs_alert,
    }

