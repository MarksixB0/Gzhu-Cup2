# -*- coding: utf-8 -*-
"""读侧检索留痕:把「这次读侧到底按什么选出了这些记忆」按次落盘,可翻阅、可审计。

定位(与既有审计面的分工):
  - ``retrieve.select_context`` 的 ``explain`` 是**当次**的逐条解释,算完即弃;
    本模块把它持久化,回答"昨天那轮回复/关怀,模型实际看到了什么、为什么是这些";
  - 与 FTS 接入后的排障直接相关:FTS 是否介入、初筛后候选还剩多少,不落盘就不可见。

红线(为什么是独立命名空间):
  - 只进 ``("memory", user, "retrieval_trace")``,**绝不写成 fact/episode** ——
    检索痕迹一旦进记忆本体,下次检索就会被当成记忆读出来,形成
    "系统在想什么"的自我污染(与 risk_state / risk_events 分层同一逻辑);
  - 不参与遗忘扫描(forget 只扫 facts/episodes):滚动上限与 TTL 自己管自己;
  - 记录含用户 query 原话与记忆摘录,与 /api/memory/debug 同级敏感数据面
    (展示受 MEMORY_INSIGHT_ENABLED 门控;清空记忆 wipe() 一并删除);
  - **用户暂停记录(privacy.recording_enabled=False)时一律不写**:留痕虽不是记忆本体,
    但含 query 原文与记忆摘录,继续写会绕过"不再记录"这个承诺;
  - 写入失败只告警:留痕是增强,绝不影响回复。
"""
import logging
import time
import uuid

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mstore

logger = logging.getLogger(__name__)

# 只记录会进提示词的两种用途;debug 是排障视图,记它会把排障本身变成污染源
_PURPOSES = ("reply", "care")
_MAX_SELECTED = 12   # 每条记录最多保留多少个选中条目明细(防单条记录膨胀)
_SNIPPET = 40        # 条目文本截断长度


def ns(user: str):
    return mstore.ns_retrieval_trace(user)


def _snippet(text, limit: int = _SNIPPET) -> str:
    t = " ".join(str(text or "").split())
    return t[:limit] + ("…" if len(t) > limit else "")


def record(user: str, purpose: str, sel: dict, now: float | None = None) -> None:
    """把一次 ``select_context`` 的结果落成留痕;任何失败只告警。

    sel:select_context 的返回 dict(facts/episodes/lanes/explain/fts/budget/...)。
    """
    if not user or purpose not in _PURPOSES:
        return
    if not bool(getattr(cfg, "MEMORY_TRACE_ENABLED", True)):
        return
    # 暂停记录 = 不再写入任何含用户内容的东西。留痕不是记忆本体,但存着 query 原文
    # 与记忆摘录,继续写等于绕过用户按下的那个开关;读不到状态时按"暂停"处理(fail-closed)。
    try:
        from gzhu_cup2.agent.long_memory import privacy as mprivacy
        if not mprivacy.recording_enabled(user):
            return
    except Exception as exc:  # noqa: BLE001 - 隐私状态不可判定时不写
        logger.warning("检索留痕跳过(隐私状态不可读) user=%s: %s", user, exc)
        return
    try:
        _record_inner(user, purpose, sel, float(now if now is not None else time.time()))
    except Exception as exc:  # noqa: BLE001 - 留痕失败绝不影响回复
        logger.warning("检索留痕写入失败 user=%s: %s", user, exc)


def _record_inner(user: str, purpose: str, sel: dict, now: float) -> None:
    sel = sel or {}
    explain = sel.get("explain") or []
    drop_reasons: dict = {}
    n_selected = 0
    selected = []
    for r in explain:
        if r.get("selected"):
            n_selected += 1
            if len(selected) < _MAX_SELECTED:
                selected.append({
                    "lane": r.get("lane"),
                    "base": r.get("base"),
                    "rel": r.get("rel"),
                    "text": _snippet(r.get("text")),
                })
        else:
            reason = str(r.get("reason") or "not_reached")
            drop_reasons[reason] = drop_reasons.get(reason, 0) + 1

    lanes = sel.get("lanes") or {}
    fts = sel.get("fts") or {}
    rec = {
        "ts": round(now, 3),
        "purpose": purpose,
        "query": _snippet(sel.get("query"), 80),
        "budget": sel.get("budget"),
        "chars": sel.get("chars"),
        # FTS 初筛观测:engaged=是否命中;applied=是否真的进入初筛(小库不筛时为 False);
        # narrowed=候选是否真的被收窄;fallback=是否因命中集为空退回全集;
        # candidates=各命名空间初筛前/后条数(raw/eligible/after_fts/kept)。
        "fts_engaged": bool(fts.get("engaged")),
        "fts_applied": bool(fts.get("applied")),
        "fts_hits": int(fts.get("n_hits") or 0),
        "fts_narrowed": bool(fts.get("narrowed")),
        "fts_fallback": bool(fts.get("fallback")),
        "candidates": dict(fts.get("candidates") or {}),
        "intent_topics": list((sel.get("intent") or {}).get("topics") or []),
        "lanes_all": dict(lanes.get("all") or {}),
        "lanes_selected": dict(lanes.get("selected") or {}),
        "n_considered": len(explain),
        "n_selected": n_selected,
        "selected": selected,
        "episode_keys": [str(k)[:80] for k in (sel.get("episode_keys") or [])][:_MAX_SELECTED],
        "drop_reasons": drop_reasons,
    }
    key = "%013d-%s-%s" % (int(now * 1000), purpose, uuid.uuid4().hex[:6])
    mstore.put_item(ns(user), key, rec)
    _prune(user, now)


def _prune(user: str, now: float) -> None:
    """滚动清理:超上限删最旧,超 TTL 删过期。失败不影响主流程。"""
    items = mstore.list_items(ns(user), limit=100000)
    cap = max(1, int(getattr(cfg, "MEMORY_TRACE_MAX_ITEMS", 100) or 100))
    ttl = float(getattr(cfg, "MEMORY_TRACE_TTL_DAYS", 14) or 0) * 86400.0
    rows = []
    for it in items:
        ts = float((it.value or {}).get("ts") or 0.0)
        rows.append((ts, it.key))
    rows.sort(key=lambda x: -x[0])          # 新→旧
    victims = set()
    for i, (ts, key) in enumerate(rows):
        if i >= cap:
            victims.add(key)
        elif ttl > 0 and ts > 0 and (now - ts) > ttl:
            victims.add(key)
    for key in victims:
        mstore.delete_item(ns(user), key)


def list_recent(user: str, limit: int = 50) -> list:
    """最近 N 条留痕(新→旧);无则空列表。"""
    if not user:
        return []
    rows = [dict(it.value or {}) for it in mstore.list_items(ns(user), limit=100000)]
    rows.sort(key=lambda r: -float(r.get("ts") or 0.0))
    return rows[: max(1, int(limit))]
