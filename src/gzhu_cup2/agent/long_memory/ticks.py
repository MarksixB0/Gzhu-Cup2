# -*- coding: utf-8 -*-
"""时序层:把每轮情绪/风险的数值信号沉淀为「按天」日桶(ticks)。

背景:待固化段(pending)在固化后被清空,若不另存数值信号,
跨会话/跨天计算情绪起伏就没有数据源(固化后的 episodes 只留摘要)。
因此本模块在 capture 时(无 LLM、毫秒级)把每轮 meta 聚合进当日桶,
供近况摘要(long_memory.profile)与未来的主动关怀消费。

设计约定:
  - 命名空间 ("memory", <user>, "ticks"),key = "YYYY-MM-DD";
  - 只聚合「有值」的字段,None 不计入,避免把缺失当 0 稀释均值;
  - 逐轮 phq/gad 伪量表分不再聚合(噪声源,已移除)——量表推断改由固化时的
    维度证据(episode 标签卡)与 trend 层窗口聚合承担;
  - 出现过 high/crisis 的日桶标记 protected,不参与过期清理
    (与 facts/episodes 的受保护集同一思路:风险相关数据不轻易丢);
  - 本模块只经由 long_memory.store 触碰 Store。
"""
import math
import time
from datetime import datetime, timedelta

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore

logger = __import__("logging").getLogger(__name__)

_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "crisis": 3}


def ns_ticks(user: str):
    return ("memory", user, "ticks")


def _day_str(ts: float) -> str:
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")


def _to_float(v):
    try:
        f = float(v)
        return f if f == f else None  # 排除 NaN
    except (TypeError, ValueError):
        return None


def bump_day_bucket(user: str, meta: dict | None, ts: float | None = None) -> None:
    """把一轮 meta 聚合进当日桶(幂等累加,由 store._LOCK 保护)。

    ``risk_level == "crisis"`` 的轮次不聚合 negative_score 与 lex_neg,
    避免单日危机推高纵向趋势;其余字段照常聚合。
    """
    meta = dict(meta or {})
    now = float(ts) if ts is not None else time.time()
    day = _day_str(now)
    bucket = dict(mstore.get_item(ns_ticks(user), day) or {})
    bucket["count"] = int(bucket.get("count", 0)) + 1

    risk = str(meta.get("risk_level") or "").strip().lower()
    is_crisis = risk == "crisis"

    # 连续量:negative_score / intensity(有值才累计);危机轮的 negative_score 不累计。
    # 日桶存充分统计量,供上层算均值、方差与作息相位:
    #   neg_sum / neg_n → 均值;neg_sumsq → 合并方差
    #   hour_sin_sum/cos → 作息相位(圆周均值)
    for key, sum_key, n_key in (
        ("negative_score", "neg_sum", "neg_n"),
        ("intensity", "inten_sum", "inten_n"),
    ):
        if is_crisis and key == "negative_score":
            continue
        val = _to_float(meta.get(key))
        if val is not None:
            bucket[sum_key] = round(float(bucket.get(sum_key, 0.0)) + val, 4)
            bucket[n_key] = int(bucket.get(n_key, 0)) + 1
            if key == "negative_score":
                bucket["neg_sumsq"] = round(
                    float(bucket.get("neg_sumsq", 0.0)) + val * val, 6)

    # 词表旁路通道(lex_neg,确定性):与启发式 neg 并列存均值;危机轮不聚合。
    lex_val = None if is_crisis else _to_float(meta.get("lex_neg"))
    if lex_val is not None:
        bucket["lex_neg_sum"] = round(float(bucket.get("lex_neg_sum", 0.0)) + lex_val, 4)
        bucket["lex_neg_n"] = int(bucket.get("lex_neg_n", 0)) + 1

    emo = str(meta.get("emotion") or "").strip()
    if emo:
        emotions = dict(bucket.get("emotions") or {})
        emotions[emo] = int(emotions.get(emo, 0)) + 1
        bucket["emotions"] = emotions

    # 深夜活跃标记(0:00-6:00 的交互轮)
    hour = datetime.fromtimestamp(now).hour
    if hour < 6:
        bucket["night_count"] = int(bucket.get("night_count", 0)) + 1

    # 分时段累积(凌晨0-6/上午6-12/下午12-18/晚上18-24):供
    # algo.components.daytools 的分时点基线消费;负面与日级同规则
    # (危机轮不聚合 negative_score)。旧桶无这些字段,读取侧降级。
    if hour < 6:
        slot = "dawn"
    elif hour < 12:
        slot = "morning"
    elif hour < 18:
        slot = "afternoon"
    else:
        slot = "evening"
    bucket["slot_%s_count" % slot] = int(bucket.get("slot_%s_count" % slot, 0)) + 1
    slot_neg = None if is_crisis else _to_float(meta.get("negative_score"))
    if slot_neg is not None:
        bucket["slot_%s_neg_sum" % slot] = round(
            float(bucket.get("slot_%s_neg_sum" % slot, 0.0)) + slot_neg, 4)
        bucket["slot_%s_neg_n" % slot] = int(bucket.get("slot_%s_neg_n" % slot, 0)) + 1

    # 作息相位(圆周均值):把"几点说话"压成一个角度。sin/cos 分开累加,可跨日合并。
    _ang = 2.0 * math.pi * (float(hour) + 0.5) / 24.0
    bucket["hour_sin_sum"] = round(
        float(bucket.get("hour_sin_sum", 0.0)) + math.sin(_ang), 6)
    bucket["hour_cos_sum"] = round(
        float(bucket.get("hour_cos_sum", 0.0)) + math.cos(_ang), 6)
    bucket["hour_n"] = int(bucket.get("hour_n", 0)) + 1

    # 行为/症状族(确定性词表,零 LLM):退缩 / 冲突 / 快感缺失 / 躯体化。
    # 兼容历史格式:None / "withdraw" / "withdraw+anhedonia" / 集合。
    raw_beh = meta.get("behavior")
    if isinstance(raw_beh, (list, tuple, set, frozenset)):
        beh_set = {str(x).strip().lower() for x in raw_beh}
    else:
        beh_set = {s for s in str(raw_beh or "").strip().lower().replace(",", "+").split("+") if s}
    if "both" in beh_set:
        beh_set = (beh_set - {"both"}) | {"withdraw", "conflict"}
    for fam in ("withdraw", "conflict", "anhedonia", "somatic"):
        if fam in beh_set:
            k = "%s_count" % fam
            bucket[k] = int(bucket.get(k, 0)) + 1

    rank = _RISK_RANK.get(risk, 0)
    if rank:
        bucket["risk_max_rank"] = max(int(bucket.get("risk_max_rank", 0)), rank)
    if int(bucket.get("risk_max_rank", 0)) >= 2:  # high / crisis 当天受保护
        bucket["protected"] = True

    # 镜像确认信号(对话内自然纠错):confirm/deny 计数,供后续校准负向证据可信度
    rk = str(meta.get("affect_reply") or "").strip().lower()
    if rk in ("confirm", "deny"):
        bucket[f"{rk}_count"] = int(bucket.get(f"{rk}_count", 0)) + 1

    bucket["last_ts"] = now
    mstore.put_item(ns_ticks(user), day, bucket)


def list_day_buckets(user: str, days: int | None = None,
                     now: float | None = None) -> list:
    """按天升序返回 [(day_key, bucket)]。

    参数语义(务必注意 `now` 是**上界**):
      - days=N : 返回距 now 最近 N 个自然日内的日桶;
      - days=None: 返回 now **之前**的全部日桶(不设下界),但**绝不返回 now 之后的桶**。

    now 是上界,days=None 时也不返回 now 之后的桶(历史回放依赖这一点)。
    """
    now = now if now is not None else time.time()
    now_key = datetime.fromtimestamp(float(now)).strftime("%Y-%m-%d")
    items = mstore.list_items(ns_ticks(user))
    out = []
    if days is not None:
        start = datetime.fromtimestamp(float(now)).date() - timedelta(days=max(1, int(days)) - 1)
        start_key = start.strftime("%Y-%m-%d")
    for it in items:
        key = str(it.key)
        if key > now_key:            # 上界:未来日桶永不参与任何判定
            continue
        if days is not None and key < start_key:
            continue
        out.append((key, it.value))
    return sorted(out, key=lambda kv: kv[0])


def prune_expired(user: str, now: float | None = None) -> dict:
    """清理超保留期的非保护日桶(保护日桶 = 出现过 high/crisis,长期保留)。"""
    now = now if now is not None else time.time()
    keep_sec = float(cfg.MEMORY_TICK_KEEP_DAYS) * 86400.0
    stats = {"checked": 0, "pruned": 0, "kept": 0}
    for it in mstore.list_items(ns_ticks(user)):
        v = it.value or {}
        stats["checked"] += 1
        if v.get("protected"):
            stats["kept"] += 1
            continue
        last = float(v.get("last_ts") or 0.0)
        if last and (now - last) > keep_sec:
            mstore.delete_item(ns_ticks(user), it.key)
            stats["pruned"] += 1
        else:
            stats["kept"] += 1
    return stats


def last_activity_ts(user: str, now: float | None = None) -> float | None:
    """该用户最近一次有对话记录的本地时间戳(由日桶推导),无记录返回 None。

    供 care 决策判断「用户多久没来」;只读轻量扫描。

    now 是上界:只统计该时刻之前的活跃记录。
    """
    ts = None
    for _, b in list_day_buckets(user, days=None, now=now):
        if not b:
            continue
        last = float(b.get("last_ts") or 0.0)
        if last > 0 and (ts is None or last > ts):
            ts = last
    return ts


def last_risk_ts(user: str, min_rank: int = 3,
                 now: float | None = None) -> float | None:
    """最近一次出现过 ≥min_rank 风险日桶的时间戳(rank: low0/medium1/high2/crisis3)。

    用于危机后脆弱窗。日桶保留 risk_max_rank,故可直接取到。
    """
    ts = None
    for _, b in list_day_buckets(user, days=None, now=now):
        if not b:
            continue
        if int(b.get("risk_max_rank", 0) or 0) < int(min_rank):
            continue
        last = float(b.get("last_ts") or 0.0)
        if last > 0 and (ts is None or last > ts):
            ts = last
    return ts
