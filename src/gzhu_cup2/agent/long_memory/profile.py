# -*- coding: utf-8 -*-
"""画像层:把趋势层(冷轨)与固化记忆(episode 标签卡)聚合成「用户画像」。

对外提供三类只读产物:
  1. recent_mood_summary —— 近 N 天状态摘要(自然语言,给生成节点注入,不含内部分数);
  2. trait_card —— 特质卡(粗分档 + 证据计数,给主动关怀/守护做阈值判断,不直接进 prompt);
  3. trigger_mining —— 触发模式挖掘(「顺藤摸瓜」:哪类话题伴随情绪下行)。

设计约定:
  - 数值一律不进面向用户的自然语言文本(由模板本身保证,不泄漏内部标记);
  - 画像的全部结论都可溯源:每个档位都附带「依据的样本量/窗口」字段。
"""
from typing import Optional

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory import tags
from gzhu_cup2.agent.long_memory import ticks
from gzhu_cup2.agent.long_memory import trend
from gzhu_cup2.agent.long_memory.tags import EMOTION_ZH, TOPIC_ZH, SOCIAL_TOPICS


def _neg_band(avg: float) -> str:
    if avg < 0.33:
        return "整体还算平稳"
    if avg < 0.60:
        return "情绪有些低落"
    return "情绪明显低落"


def recent_mood_summary(user: str, days: Optional[int] = None,
                        now: Optional[float] = None) -> Optional[str]:
    """近 N 天(默认 MEMORY_TREND_RECENT_DAYS)的状态摘要,无数据返回 None。"""
    st = trend.window_stats(user, days=days, now=now)
    if st is None:
        return None
    days_covered = st["days"]
    span = "今天" if days_covered <= 1 else f"最近 {days_covered} 天"
    parts = [f"{span}你们共聊了 {st['turn_count']} 轮。"]

    dom = st["dominant_emotion"]
    zh = EMOTION_ZH.get(dom)
    if zh and dom != "neutral":
        parts.append(f"情绪以「{zh}」为主。")

    if st["neg_avg"] is not None:
        parts.append(_neg_band(st["neg_avg"]) + "。")

    # 走向描述(负向升=变差),仅窗口>=2 天时有意义
    if st["days"] >= 2:
        if st["trend_dir"] == "worsening":
            parts.append("这段时间情绪似乎在往下走，请多些耐心、轻柔地陪伴。")
        elif st["trend_dir"] == "improving":
            parts.append("整体似乎在慢慢好转。")

    if st["risk_max_rank"] >= 2:
        parts.append("期间出现过情绪比较危险的时刻，请以更谨慎、轻柔的方式陪伴。")

    return "".join(parts)


def long_horizon_summary(user: str, days: Optional[int] = None,
                         now: Optional[float] = None) -> Optional[str]:
    """长期趋势摘要(默认 21 天窗):回答"这几周整体如何",与 3 天窗的近期摘要互补。

    口径(全部来自已有的时序层,零 LLM):
      · 窗口取 cfg.MEMORY_TREND_LONG_DAYS(21)天的逐日负向均值;
      · 参照系是窗口之外、更早的那段(同在 90 天锚定窗内);
      · 比较绝对位移后分档成自然语言,不泄漏内部数字;
      · 更早那段不足 2 个有记录日 / 本窗有记录日不足 MIN_DAYS 时,退回纯档位描述或 None。
    """
    days = int(days or getattr(cfg, "MEMORY_TREND_LONG_DAYS", 21))
    now = now if now is not None else __import__("time").time()
    st = trend.window_stats(user, days=days, now=now)
    if st is None or st.get("neg_avg") is None:
        return None
    if int(st.get("days") or 0) < int(getattr(cfg, "MEMORY_TREND_LONG_MIN_DAYS", 5)):
        return None

    span = "近三周" if days >= 21 else ("近两周" if days >= 14 else "最近 %d 天" % days)
    neg = float(st["neg_avg"])
    parts = [f"{span}你们聊了 {st['turn_count']} 轮。"]

    ref = _older_daily_neg_mean(user, days, now)
    if ref is None:
        parts.append(_neg_band(neg) + "。")
    else:
        delta = neg - ref
        if delta >= 0.15:
            parts.append("整体比平时低沉不少，请多些耐心、轻柔地陪伴。")
        elif delta >= 0.06:
            parts.append("比平时偏低一些。")
        elif delta <= -0.06:
            parts.append("比平时轻松一些。")
        else:
            parts.append("和平时差不多。")
    if st.get("trend_dir") == "worsening":
        parts.append("这几周整体在往下走。")
    elif st.get("trend_dir") == "improving":
        parts.append("这几周整体在慢慢好转。")
    if int(st.get("risk_max_rank") or 0) >= 2:
        parts.append("这段时间里出现过情绪比较危险的时刻，请格外轻柔。")
    return "".join(parts)


def _older_daily_neg_mean(user: str, window_days: int, now: float):
    """窗口**之外、更早**那段的逐日负向均值(参照系;无足够数据返回 None)。

    只读日桶、零 LLM。取 90 天锚定窗内的全部日桶,减掉最近 window_days 天。
    """
    anchor = int(getattr(cfg, "MEMORY_BASELINE_ANCHOR_WINDOW_DAYS", 90))
    recent = ticks.list_day_buckets(user, days=window_days, now=now)
    if not recent:
        return None
    start_key = min(k for k, _ in recent)
    vals = []
    for key, b in ticks.list_day_buckets(user, days=anchor, now=now):
        if key >= start_key:
            continue
        n = int((b or {}).get("neg_n", 0) or 0)
        if n > 0:
            vals.append(float((b or {}).get("neg_sum", 0.0) or 0.0) / n)
    if len(vals) < 2:
        return None
    return sum(vals) / len(vals)


# ============================== 特质卡 ==============================
def _level_of(v: Optional[float], lo: float, hi: float,
              low: str, mid: str, high: str) -> str:
    if v is None:
        return "unknown"
    if v < lo:
        return low
    if v < hi:
        return mid
    return high


def _episodes(user: str, days: Optional[int] = None,
              now: Optional[float] = None) -> list:
    from gzhu_cup2.agent.long_memory.ledger import iter_episodes
    now = now if now is not None else __import__("time").time()
    start_ts = (now - float(days) * 86400.0) if days else None
    return [v for ts, v in iter_episodes(user)
            if start_ts is None or ts >= start_ts]


def trait_card(user: str, days: Optional[int] = None,
               now: Optional[float] = None) -> Optional[dict]:
    """用户特质卡(粗分档,全部带证据计数,可溯源;供内部决策使用)。

    返回 None 表示窗口内无数据。字段:
      traits: {baseline_mood, stability, negative_tendency, social_orientation}
      evidence: {days, turn_count, neg_avg, volatility, negative_share,
                 topic_episodes, social_episodes, risk_rank_max}
      dominant_emotion: 窗口主导情绪
    """
    days = int(days or cfg.MEMORY_TRAIT_WINDOW_DAYS)
    st = trend.window_stats(user, days=days, now=now)
    if st is None or st["turn_count"] <= 0:
        return None

    baseline = _level_of(st["neg_avg"], 0.4, 0.62,
                         "整体偏积极平稳", "中等(偶有低落)", "偏低(明显低落)")
    stability = _level_of(st["volatility"], 0.12, 0.25,
                          "情绪较平稳", "有一定起伏", "起伏明显")
    negative_tendency = _level_of(st["negative_share"], 0.25, 0.5,
                                  "负面情绪占比不高", "负面情绪较多", "负面情绪偏主导")

    # 社会/支持话题密度(基于有主题标签的 episode 段数)
    eps = _episodes(user, days=days, now=now)
    topic_episodes = [e for e in eps if (e.get("topics") or [])]
    social_episodes = [
        e for e in topic_episodes
        if SOCIAL_TOPICS.intersection(e.get("topics") or [])
    ]
    share = (len(social_episodes) / len(topic_episodes)) if topic_episodes else None
    social = _level_of(share, 0.25, 0.5,
                       "较少提及(也可能只是没聊到)",
                       "有一定社会/家庭/关系话题", "较多社会/家庭/关系话题")

    return {
        "traits": {
            "baseline_mood": baseline,
            "stability": stability,
            "negative_tendency": negative_tendency,
            "social_orientation": social,
        },
        "dominant_emotion": st["dominant_emotion"],
        "evidence": {
            "days": st["days"],
            "turn_count": st["turn_count"],
            "neg_avg": st["neg_avg"],
            "volatility": st["volatility"],
            "negative_share": st["negative_share"],
            "risk_rank_max": st["risk_max_rank"],
            "topic_episodes": len(topic_episodes),
            "social_episodes": len(social_episodes),
        },
    }


# ============================== 触发模式挖掘 ==============================
def trigger_mining(user: str, limit: int = 3, days: Optional[int] = None,
                   now: Optional[float] = None) -> list:
    """「顺藤摸瓜」:统计哪类话题更常伴随情绪下行/风险。

    对每段带主题标签的 episode 判负(risk/protected、主导负情绪、弧 falling),
    计算 主题→负向片段占比 P(下行|主题),返回 top(默认3)。
    结果 {topic, topic_zh, n_total, n_neg, rate} —— 全部可溯源计数,不猜。
    """
    eps = _episodes(user, days=days, now=now)
    agg: dict[str, dict] = {}
    for e in eps:
        topics = set(e.get("topics") or [])
        topics.discard("other")
        if not topics:
            continue
        neg = tags.episode_is_negative(e)
        for t in topics:
            row = agg.setdefault(t, {"n_total": 0, "n_neg": 0})
            row["n_total"] += 1
            if neg:
                row["n_neg"] += 1
    out = []
    for t, row in agg.items():
        out.append({
            "topic": t,
            "topic_zh": TOPIC_ZH.get(t, t),
            "n_total": row["n_total"],
            "n_neg": row["n_neg"],
            "rate": round(row["n_neg"] / row["n_total"], 3) if row["n_total"] else 0.0,
        })
    out.sort(key=lambda x: (x["rate"], x["n_total"]), reverse=True)
    return out[: max(1, int(limit))]
