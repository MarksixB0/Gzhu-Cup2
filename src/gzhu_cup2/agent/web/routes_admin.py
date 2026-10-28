# -*- coding: utf-8 -*-
"""运维与配置路由:/api/health、/api/connection*、/api/memory/debug。

「API 连接」参考 OpenWebUI 的 Connection 配置(单条主连接):
  GET  /api/connection        读取当前连接(Key 只回传打码,绝不明文)
  PUT  /api/connection        保存连接(base_url + key + 默认模型,持久化 JSON)
  POST /api/connection/test   用表单中的 url/key 探测 OpenAI 兼容 /models
生效链路:LLM 工厂缺省解析走 runtime_connection(resolve_*),热更新无需重启。
"""
import logging
import os
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.config import runtime_connection
from gzhu_cup2.agent.core import user_profile as uprofile
from gzhu_cup2.agent.long_memory import ledger as mledger
from gzhu_cup2.agent.long_memory import lanes as mlanes
from gzhu_cup2.agent.algo.components import lexicon as mlexicon
from gzhu_cup2.agent.long_memory import privacy as mprivacy
from gzhu_cup2.agent.long_memory import profile as mprofile
from gzhu_cup2.agent.long_memory import retrieve as mretrieve
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory import ticks as mticks
from gzhu_cup2.agent.long_memory import trend as mtrend
from gzhu_cup2.agent.long_memory import risk_state as mrisk
from gzhu_cup2.agent.long_memory import tags as mtags
from gzhu_cup2.agent.long_memory import forget as mforget
from gzhu_cup2.agent.long_memory import trace as mtrace
from gzhu_cup2.agent.algo import skeleton as guard_mod
from gzhu_cup2.agent.pet import narrative as mnarrative
from gzhu_cup2.agent.web import sessions

logger = logging.getLogger(__name__)

router = APIRouter()

_RISK_RANK_NAME = {0: "low", 1: "medium", 2: "high", 3: "crisis"}

# 可被用户删除/导出的记忆命名空间(**白名单**:只认这些 kind,不接受任意 namespace)
_MEMORY_KINDS = {
    "pending": mstore.ns_pending,
    "facts": mstore.ns_facts,
    "episodes": mstore.ns_episodes,
    "risk_events": mstore.ns_risk,
}


@router.get("/api/health")
def health():
    return {"status": "ok", "model_ready": runtime_connection.is_ready()}


# ==================== 记忆隐私控制面(见 PLAN_NEXT.md §6) ====================


def _namespace_of(user: str, kind: str):
    """把前端传来的 kind 映射成命名空间;未知 kind 返回 None(白名单)。"""
    factory = _MEMORY_KINDS.get(str(kind))
    return factory(user) if factory else None


class RecordingRequest(BaseModel):
    recording: bool = Field(
        description="是否记录记忆;False = 暂停记录(只影响写入,对话不受影响)")


@router.get("/api/memory/privacy")
def memory_privacy():
    """隐私状态 + 数据落盘位置。

    **不受** ``MEMORY_INSIGHT_ENABLED`` 约束:那个开关管的是「能不能读到记忆**原文**」,
    这里是「数据在不在、我有没有在记」,任何配置下都要能回答。
    """
    user = sessions.default_user()
    return {
        **mprivacy.status(user),
        "memory_dir": cfg.MEMORY_DIR,
        "files": sessions.memory_files(),
    }


@router.patch("/api/memory/privacy")
def memory_set_privacy(req: RecordingRequest):
    """暂停 / 恢复记录(设置面板里的隐私开关)。"""
    user = sessions.default_user()
    mprivacy.set_recording(user, req.recording)
    return {
        **mprivacy.status(user),
        "memory_dir": cfg.MEMORY_DIR,
        "files": sessions.memory_files(),
    }


@router.get("/api/memory/export")
def memory_export():
    """导出当前用户的全部记忆为 JSON(供用户自行备份 / 带走)。

    ⚠ 返回**原文**,受 ``MEMORY_INSIGHT_ENABLED`` 约束(与 debug/insight 同一开关)。
    只导出当前用户 —— 导出他人记忆没有任何正当用途。
    """
    _require_insight_enabled()
    user = sessions.default_user()

    def _items(kind: str) -> list:
        ns = _namespace_of(user, kind)
        return [{"key": it.key, "value": it.value}
                for it in mstore.list_items(ns, limit=100000)]

    return {
        "user": user,
        "exported_at": time.time(),
        "facts": _items("facts"),
        "episodes": _items("episodes"),
        "risk_events": _items("risk_events"),
        "pending": _items("pending"),
        "day_buckets": [{"key": d, "value": b}
                        for d, b in mticks.list_day_buckets(user, days=3650)],
    }


@router.delete("/api/memory/all")
def memory_wipe(confirm: int = 0):
    """清空当前用户的**全部记忆**(不可逆)。

    除六类记忆命名空间(facts/episodes/risk_events/pending/pending_ready/retrieval_trace)外,
    还清空**派生内部数据**:日桶(ticks)、风险标记(risk_state)、桌宠每日叙事(pet.daily)
    与用户自己写的日程(agenda) —— 它们同样是用户数据。

    两层保护:① 必须显式带 ``confirm=1``;
    ② 返回**实际删了什么**(命名空间 → 条数)。
    """
    _require_insight_enabled()
    if not confirm:
        raise HTTPException(status_code=400, detail="该操作不可逆,请带 confirm=1 显式确认")
    user = sessions.default_user()
    namespaces = [
        ("pending", mstore.ns_pending(user)),
        ("pending_ready", mstore.ns_pending_ready(user)),
        ("facts", mstore.ns_facts(user)),
        ("episodes", mstore.ns_episodes(user)),
        ("risk_events", mstore.ns_risk(user)),
        ("retrieval_trace", mstore.ns_retrieval_trace(user)),
        ("ticks", mticks.ns_ticks(user)),
        ("risk_state", mrisk.ns_risk_state(user)),
        ("pet_daily", mnarrative.ns_daily(user)),
        # 日程是用户写的约定,同样是用户数据:清空记忆不能留着它
        ("agenda", mstore.ns_agenda(user)),
    ]
    removed = {}
    for kind, ns in namespaces:
        items = mstore.list_items(ns, limit=100000)
        for it in items:
            mstore.delete_item(ns, it.key)
        if items:
            removed[kind] = len(items)
    # 用户自述资料(称呼 / 喜欢 / 讨厌)同样是用户数据:它不在上面的命名空间里,
    # 但清空记忆却不删它,就是"假删除"(知情权原则,同 §6)。
    if uprofile.clear():
        removed["user_profile"] = 1
    return {"ok": True, "removed": removed}


# ============================ 长期记忆(排障) ============================
@router.get("/api/memory/debug")
def memory_debug():
    """长期记忆调试:查看 store 后端、各用户记忆状态与画像信号(只读)。

    除基础计数外,还暴露趋势/画像层结论供排障与答辩演示:
      risk_facets(窗口内)/ risk_facets_total(全时段)、baseline、
      trait_card(粗分档+证据计数)、trigger_mining(话题→情绪下行占比)。

    ⚠ 本接口返回用户记忆原文摘录,属敏感数据面;受 cfg.MEMORY_INSIGHT_ENABLED
      开关约束(默认 True 便于开发,对外部署应置 False 或收紧 CORS)。
    """
    _require_insight_enabled()
    users = mstore.list_memory_users()
    if not users:
        users = [sessions.resolve_user()]
    users_out = []
    for u in users:
        pending = mstore.get_pending(u)
        facts = [it.value for it in mstore.list_items(mstore.ns_facts(u))
                 if not it.value.get("archived")]
        episodes = [it.value for it in mstore.list_items(mstore.ns_episodes(u))
                    if not it.value.get("archived")]
        tick_days = mticks.list_day_buckets(u, days=7)
        users_out.append({
            "user": u,
            "pending_turns": len((pending or {}).get("turns") or []),
            "fact_count": len(facts),
            "facts": [str(f.get("text"))[:120] for f in facts][:10],
            "episode_count": len(episodes),
            "episodes": [str(e.get("digest"))[:120] for e in episodes][:10],
            "tick_days_7": len(tick_days),
            "tick_turns_7": sum(int((b or {}).get("count", 0)) for _, b in tick_days),
            "recent_summary": mprofile.recent_mood_summary(u),
            # 危机分面留痕(受保护):recent 遵循 MEMORY_RISK_EVENTS_RECENT_DAYS 口径;
            # total 保留全时段供排障
            "risk_facets": mstore.risk_facet_counts(
                u, days=cfg.MEMORY_RISK_EVENTS_RECENT_DAYS),
            "risk_facets_total": mstore.risk_facet_counts(u, days=None),
            "baseline": mtrend.baseline_and_drift(u),   # 个人基线恶化检测(可 None)
            # 观测面:画像粗分档 + 触发模式(带证据计数)。仅展示,不接进 care 决策。
            "trait_card": mprofile.trait_card(u),
            "trigger_mining": mprofile.trigger_mining(u),
        })
    return {"backend": mstore.store_backend_name(),
            "users": users_out}


@router.get("/api/memory/trace")
def memory_trace(limit: int = 50):
    """检索留痕:最近 N 次读侧组装的决策记录(新→旧;只读,见 long_memory/trace.py)。

    回答"那一次回复/关怀,模型实际看到了哪些记忆、为什么是这些":
    FTS 是否介入、候选多少、选中条目与落选原因、分道分布、预算用量。
    与 debug 同一敏感数据面(返回 query 原话与记忆摘录),受 MEMORY_INSIGHT_ENABLED 约束。
    """
    _require_insight_enabled()
    return {"records": mtrace.list_recent(sessions.resolve_user(), limit=limit)}


# ==================== 长期记忆洞察(开发期可视化用) ====================
def _require_insight_enabled() -> None:
    """两个记忆数据面接口的统一开关(见 cfg.MEMORY_INSIGHT_ENABLED 注释)。"""
    if not getattr(cfg, "MEMORY_INSIGHT_ENABLED", True):
        raise HTTPException(
            status_code=403,
            detail="长期记忆洞察接口已关闭(MEMORY_INSIGHT_ENABLED=False)",
        )


def _human_ts(ts) -> Optional[str]:
    try:
        f = float(ts)
        if f <= 0:
            return None
        return datetime.fromtimestamp(f).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return None


def _facts_view(user: str, now: float) -> list:
    """全部事实(不截断)+ 读侧配额是否入选 + 主动提及白名单资格。

    两个派生字段各管一端:
      · memory_score —— "读侧取前 N 条"时谁入选(即哪条没被模型看到);
      · can_initiate —— 系统能不能主动提起它(recall 是否触发)。
    """
    rows = []
    for it in mstore.list_items(mstore.ns_facts(user)):
        v = dict(it.value or {})
        score = mforget.memory_score(v, now)
        rows.append({
            "key": it.key,
            "text": str(v.get("text") or ""),
            "category": str(v.get("category") or "other"),
            "importance": v.get("importance"),
            "mentions": v.get("mentions"),
            "memory_score": round(float(score), 4),
            "archived": bool(v.get("archived")),
            "protected": bool(mforget.is_protected(v)),
            # 弱证据标注(多采样固化的并集产物):support/vote_samples/weak 三件套。
            # 旧数据没有这些字段 ⇒ support 为 None、weak 为 False(不降权),与改动前一致。
            "support": v.get("support"),
            "vote_samples": v.get("vote_samples"),
            "weak": bool(v.get("weak")),
            "first_seen": _human_ts(v.get("first_seen")),
            "last_seen": _human_ts(v.get("last_seen")),
            "initiable": bool(mretrieve.can_initiate(v, now)),
        })
    # 读侧入选:由 retrieve 的预算分配器给出(不在此另算)。
    # 不传 query,故为静态口径;真实对话的排序会按用户那句话重排。
    sel = mretrieve.select_context(user, purpose="reply", now=now)
    top_texts = set(sel["fact_texts"])
    by_text = {mstore.norm_text(e["text"]): e for e in (sel.get("explain") or [])}
    for r in rows:
        r["in_reply_quota"] = mstore.norm_text(r["text"]) in top_texts
        # 分道 / 相关分 / 落选原因
        info = by_text.get(mstore.norm_text(r["text"])) or {}
        r["lane"] = info.get("lane") or mlanes.classify(r)
        r["lane_zh"] = info.get("lane_zh") or mlanes.describe(r)
        r["relevance"] = info.get("rel")
        r["on_topic"] = info.get("on_topic")
        r["drop_reason"] = info.get("reason")
        r["risk_content"] = info.get("risk", mlanes.is_risk_content(r))
    rows.sort(key=lambda r: r["memory_score"], reverse=True)
    return rows


def _episodes_view(user: str, now: float) -> list:
    """全部会话摘要 + 标签卡(话题/情绪弧/维度证据)+ 读侧配额是否入选。"""
    rows = []
    for it in mstore.list_items(mstore.ns_episodes(user)):
        v = dict(it.value or {})
        topics = list(v.get("topics") or [])
        dims = v.get("dimension_evidence") or {}
        rows.append({
            "key": it.key,
            "digest": str(v.get("digest") or ""),
            "importance": v.get("importance"),
            "mentions": v.get("mentions"),
            "memory_score": round(float(mforget.memory_score(v, now)), 4),
            "archived": bool(v.get("archived")),
            "protected": bool(v.get("protected")),
            "category": str(v.get("category") or ""),
            "risk_note": str(v.get("risk_note") or ""),
            "topics": topics,
            "topics_zh": [mtags.TOPIC_ZH.get(t, t) for t in topics],
            "dominant_emotion": v.get("dominant_emotion"),
            "valence_arc": v.get("valence_arc"),
            "emotion_shift": v.get("emotion_shift"),
            "arousal": v.get("arousal"),
            "dimension_evidence": {
                k: {
                    "name": mtags.DIM_NAMES.get(k, k),
                    "level": (vv or {}).get("level") if isinstance(vv, dict) else None,
                    "quote": (vv or {}).get("quote") if isinstance(vv, dict) else None,
                }
                for k, vv in (dims or {}).items()
            },
            "triggers": list(v.get("triggers") or []),
            "coping": list(v.get("coping") or []),
            "turn_count": v.get("turn_count"),
            "ts_start": _human_ts(v.get("ts_start")),
            "ts_end": _human_ts(v.get("ts_end")),
            "first_seen": _human_ts(v.get("first_seen")),
            "last_seen": _human_ts(v.get("last_seen")),
        })
    # 与 _facts_view 同理:入选与否由 retrieve 的预算分配器决定,不在这里另算一遍
    sel = mretrieve.select_context(user, purpose="reply", now=now)
    top_digests = {str(v.get("digest") or "") for v in sel["episodes"]}
    for r in rows:
        r["in_reply_quota"] = r["digest"] in top_digests
    rows.sort(key=lambda r: r["memory_score"], reverse=True)
    return rows


def _pending_view(user: str) -> dict:
    """待固化段 —— 这是系统里**唯一还留着用户原话**的地方(固化后即删除)。"""
    p = mstore.get_pending(user) or {}
    turns = []
    for t in (p.get("turns") or []):
        turns.append({
            "user": str(t.get("user") or ""),
            "ai": str(t.get("ai") or ""),
            "thread": t.get("thread"),
            "ts": _human_ts(t.get("ts")),
            "risk_level": (t.get("meta") or {}).get("risk_level"),
            "emotion": (t.get("meta") or {}).get("emotion"),
            "negative_score": (t.get("meta") or {}).get("negative_score"),
            "lex_neg": (t.get("meta") or {}).get("lex_neg"),
        })
    return {
        "exists": bool(p),
        "turn_count": len(turns),
        "seq": p.get("seq"),
        "started_at": _human_ts(p.get("started_at")),
        "threads": list(p.get("threads") or []),
        "is_due": bool(mstore.pending_is_due(p)) if p else False,
        "is_urgent": bool(mstore.pending_is_urgent(p)) if p else False,
        "min_turns_threshold": int(cfg.MEMORY_CONSOLIDATE_MIN_TURNS),
        "idle_seconds_threshold": int(cfg.MEMORY_IDLE_SECONDS),
        "turns": turns,
    }


def _buckets_view(user: str) -> list:
    """逐日日桶:全部原始字段 + 两种口径的 lex 均值(务必分清)。"""
    out = []
    for day, b in mticks.list_day_buckets(user, days=None):
        b = dict(b or {})
        n = int(b.get("neg_n", 0) or 0)
        lex_n = int(b.get("lex_neg_n", 0) or 0)
        lex_s = float(b.get("lex_neg_sum", 0.0) or 0.0)
        inten_n = int(b.get("inten_n", 0) or 0)
        out.append({
            "day": day,
            "count": int(b.get("count", 0) or 0),
            "neg_avg": round(float(b.get("neg_sum", 0.0) or 0.0) / n, 4) if n else None,
            "neg_n": n,
            "neg_sum": round(float(b.get("neg_sum", 0.0) or 0.0), 4),
            "inten_avg": (round(float(b.get("inten_sum", 0.0) or 0.0) / inten_n, 4)
                          if inten_n else None),
            "lex_avg": round(lex_s / lex_n, 4) if lex_n else None,
            "lex_n": lex_n,
            "lex_sum": round(lex_s, 4),
            "night_count": int(b.get("night_count", 0) or 0),
            "confirm_count": int(b.get("confirm_count", 0) or 0),
            "deny_count": int(b.get("deny_count", 0) or 0),
            "risk_max_rank": int(b.get("risk_max_rank", 0) or 0),
            "risk_max_name": _RISK_RANK_NAME.get(int(b.get("risk_max_rank", 0) or 0), "low"),
            "emotions": dict(b.get("emotions") or {}),
            "protected": bool(b.get("protected")),
            "last_ts": _human_ts(b.get("last_ts")),
        })
    return out




def _pick(src, keys) -> dict:
    """从窗口统计里取指定字段(源为 None 时全部给 None,不伪造 0)。"""
    return {k: (src or {}).get(k) for k in keys}


# 三个窗口共用的字段。快尺度(3 天)只有前三个(样本太少算不出方差/相位)。
_WIN_KEYS = ("n_days", "neg_mean", "neg_median", "neg_var", "phase_h", "turn_rate")
_WIN_KEYS_SHORT = _WIN_KEYS[:3]


def _score_block(a: dict | None) -> dict | None:
    """完整分数分解(排障用)。没有数据时返回 None。

    入参是 ``skeleton.analyze()`` 的结果,不在本函数内重复分析。
    """
    a = a or {}
    if a.get("score") is None:
        return None
    f, sc = a.get("features") or {}, a.get("score") or {}
    return {
        "mode": a.get("mode"),
        "reason": a.get("reason"),
        "scores": {k: sc.get(k) for k in
                   ("s_total", "s_change", "s_severity", "s_floor",
                    "s_chronic", "s_behavior", "s_fast", "s_dimension",
                    "s_precursor", "quality")},
        "paths": (sc.get("detail") or {}).get("paths"),
        "detail": sc.get("detail"),
        "windows": {
            "recent": _pick(f.get("recent"), _WIN_KEYS),
            "base": _pick(f.get("base"), _WIN_KEYS),
            "fast": _pick(f.get("fast"), _WIN_KEYS_SHORT),
        },
        "families": {k: f.get("%s_rate" % k) for k in
                     ("withdraw", "conflict", "anhedonia", "somatic")},
        # 症状族密度的真实分母(max(5, 近窗有记录天数)),前端换算天数时须乘它
        "family_span": f.get("density_span"),
    }


def _data_basis(a: dict | None) -> dict:
    """面板上"资料够不够"那句话的依据。"""
    from gzhu_cup2.agent.algo import skeleton
    f = (a or {}).get("features") or {}
    recent = f.get("recent") or {}
    n_recent = int(recent.get("n_days") or 0)
    return {
        "basis": "recent",
        "recent_n_days": n_recent,
        "min_recent_days": int(skeleton.MIN_RECENT_DAYS),
        "coverage": f.get("coverage"),
        "idle_days": f.get("idle_days"),
        # 全部日桶的有记录天数(与 chronic 的 dim_hist_days 同口径,不做两窗相加)
        "days_total": int(f.get("dim_hist_days") or 0),
    }


def _guard_view(user: str) -> dict:
    """守护层视图:分数分解 + 逐日回放(每天"当时会怎么判")。

    ``tier`` 取**落盘标记**(含停留期/沉默保持),与面板上另一处读 risk_state
    的徽章保持同一个数;``tier_instant`` 是纯分数的瞬时档,给
    "档位为什么没立刻变"提供对照。
    """
    now = time.time()
    a = guard_mod.analyze(user, now=now) or {}
    tier_instant = str(a.get("tier_instant") or a.get("tier") or "stable")

    tier = tier_instant
    try:
        from gzhu_cup2.agent.long_memory import risk_state as rstate
        tier = str((rstate.read(user) or {}).get("tier") or tier_instant)
    except Exception:  # noqa: BLE001 - 读标记失败只影响展示,不影响瞬时判定
        tier = tier_instant

    if not a.get("features"):
        return {
            "tier": tier,
            "tier_instant": tier_instant,
            "mode": a.get("mode"),
            "tier_reason": a.get("reason"),
            "score": _score_block(a),
            "available": False,
            "reason": "无日桶数据,守护层不判定",
            "data_sufficient": False,
            "data_basis": _data_basis(a),
            "replay": [],
        }

    # 逐日回放:完整数据集 + 判定时刻回拨到当天 23:59(与真实运行一致的口径)
    replay = []
    for day, _b in mticks.list_day_buckets(user, days=None):
        try:
            anchor = datetime.strptime(day, "%Y-%m-%d").replace(
                hour=23, minute=59)
            ts = anchor.timestamp()
            r = guard_mod.analyze(user, now=ts) or {}
            replay.append({
                "day": day,
                "tier": ("数据不足" if r.get("mode") == "insufficient"
                         else str(r.get("tier") or "stable")),
            })
        except (ValueError, OSError):
            continue

    return {
        "tier": tier,
        "tier_instant": tier_instant,
        # 档位保持原因(状态机 mode + reason)
        "mode": a.get("mode"),
        "tier_reason": a.get("reason"),
        "score": _score_block(a),
        "available": True,
        "data_sufficient": a.get("mode") != "insufficient",
        "data_basis": _data_basis(a),
        # 逐段 trace:排障时回看"哪一段把分算坏了"
        "trace": a.get("trace"),
        "replay": replay,
    }


def _insight_user(user: str, now: float) -> dict:
    """单个用户的完整洞察视图。"""
    buckets = _buckets_view(user)
    facts = _facts_view(user, now)
    episodes = _episodes_view(user, now)

    ctx_reply = mretrieve.build_memory_context(user, purpose="reply")
    ctx_care = mretrieve.build_memory_context(user, purpose="care")
    ctx_debug = mretrieve.build_memory_context(user, purpose="debug")

    risk_events = []
    for e in mstore.list_risk_events(user):
        v = dict(e.value or {}) if hasattr(e, "value") else dict(e or {})
        risk_events.append({
            "facet": v.get("facet"),
            "quote": str(v.get("quote") or ""),
            "ts": _human_ts(v.get("ts")),
            "key": getattr(e, "key", None),
        })

    return {
        "user": user,
        "totals": {
            "backend": mstore.store_backend_name(),
            "day_buckets": len(buckets),
            "turns": sum(d["count"] for d in buckets),
            "fact_count": len(facts),
            "fact_active": sum(1 for f in facts if not f["archived"]),
            "episode_count": len(episodes),
            "episode_active": sum(1 for e in episodes if not e["archived"]),
            "risk_event_count": len(risk_events),
            "pending_turns": len((mstore.get_pending(user) or {}).get("turns") or []),
            "first_day": buckets[0]["day"] if buckets else None,
            "last_day": buckets[-1]["day"] if buckets else None,
        },
        # 回话时一次带得动多少记忆(条数安全阀)
        "retrieve_budget": {
            "facts": cfg.MEMORY_RETRIEVE_MAX_FACTS,
            "episodes": cfg.MEMORY_RETRIEVE_MAX_EPISODES,
        },
        "lexicon_ladder": {
            # 从 lexicon 取,不手抄常数
            **mlexicon.ladder(),
            "note": "词表通道是离散步进:base_hit + step_per_word×命中负词数"
                    "(负词数受 neg_word_cap 封顶)。故阈值 0.50 实际等价于"
                    "『每句≥3 个负词』,0.48 差 0.02 即不达标。",
        },
        "buckets": buckets,
        "trend": {
            "window_3d": mtrend.window_stats(user, days=cfg.MEMORY_TREND_RECENT_DAYS, now=now),
            "window_trait": mtrend.window_stats(user, days=cfg.MEMORY_TRAIT_WINDOW_DAYS, now=now),
            "baseline": mtrend.baseline_and_drift(user, now=now),
            "recent_summary": mprofile.recent_mood_summary(user, now=now),
        },
        "guard": _guard_view(user),
        # 心理风险标记(内部留痕):当前档位 + 跃迁历史。
        # ⚠ 只在本机内部使用;不通知任何外部方、不向用户展示内部标签。
        "risk_state": mrisk.read(user),
        "risk_history": mrisk.read_history(user)[-50:],
        "facts": facts,
        "episodes": episodes,
        "pending": _pending_view(user),
        "risk_events": risk_events,
        "dimension_ledger": {
            "dim_names": dict(mtags.DIM_NAMES),
            "covered": mledger.scan_evidence(
                user, days=cfg.MEMORY_TRAIT_WINDOW_DAYS, now=now),
            "uncovered": mledger.uncovered_dims(
                user, days=cfg.MEMORY_TRAIT_WINDOW_DAYS, now=now),
        },
        "profile": {
            "trait_card": mprofile.trait_card(user, now=now),
            "trigger_mining": mprofile.trigger_mining(user, limit=10, now=now),
        },
        "context_preview": {
            "reply": ctx_reply,
            "care": ctx_care,
            "debug": ctx_debug,
            "reply_chars": len(ctx_reply),
            "care_chars": len(ctx_care),
            "debug_chars": len(ctx_debug),
        },
    }


@router.get("/api/memory/insight")
def memory_insight(user: Optional[str] = None):
    """长期记忆完整洞察:时序 + 各通道标记 + 守护判定链 + 读侧上下文预览。

    与 /api/memory/debug 的分工:
      · debug    —— 面向"主动关怀为什么不触发"的轻量计数视图(保持向后兼容);
      · insight  —— 面向**全量分析**(本接口):逐日日桶全字段、facts/episodes
                    全字段(含 memory_score 与读侧配额入选)、待固化原话、
                    守护层 A/B/C 通道真值与逐日回放、维度账本、上下文预览。

    只读;不写任何记忆。user 省略时返回全部已知用户。
    """
    _require_insight_enabled()
    now = time.time()
    users = [user] if user else (mstore.list_memory_users() or [sessions.resolve_user()])

    # 读取即刷新一次风险标记(幂等、纯算术、零 LLM),使界面上的标记与当前数据一致;
    # 后台调度线程也会周期性刷新(见 care/scheduler.py)。
    for u in users:
        try:
            mrisk.update(u, now=now, reason="insight_read")
        except Exception as e:  # noqa: BLE001 - 标记失败不影响洞察视图
            logger.warning(
                "risk_state update failed user=%s: %s", u, e)

    return {
        "generated_at": datetime.fromtimestamp(now).strftime(
            "%Y-%m-%d %H:%M:%S"),
        "backend": mstore.store_backend_name(),
        "memory_dir": cfg.MEMORY_DIR,
        # 边界声明:随接口一起返回,使前端与任何调用方都无法误解本标记的用途
        "risk_policy": {
            "scope": "internal_only",
            "external_notification": False,
            "user_visible": False,
            "means": "识别到早期心理风险后,只在本机内部标记该用户,并据此调整桌宠的关怀方式"
                     "(更轻、更贴、更主动)。",
            "does_not": "不通知辅导员/家长/任何第三方,不推送webhook,不生成对外报告,"
                        "不自动弹出热线,不切换危机协议。",
            "crisis_path": "对话内危机仍由前台 triage → handle_crisis 独立处理,与本标记无关。",
            "see": "long_memory/risk_state.py",
        },
        "users": [_insight_user(u, now) for u in users],
    }


# ============================ API 连接 ============================
def _connection_view() -> dict:
    """GET/PUT 共用的对外视图:Key 永不回传明文。"""
    state = runtime_connection.load_state()
    key = state["api_key"]
    active = runtime_connection.get_active() is not None
    return {
        "enabled": state["enabled"],
        "base_url": state["base_url"] or cfg.MODEL_BASE_URL,
        "api_key_set": bool(key),
        "api_key_masked": runtime_connection.mask_key(key) if key else "",
        # 单点模式下默认模型恒为 config.MODEL_NAME(改 config 一处即可换模型)
        "default_model": (cfg.MODEL_NAME if cfg.MODEL_SINGLE_SOURCE
                          else (state["default_model"] or cfg.MODEL_NAME)),
        "models": state["models"],
        # active=True 表示连接已接管模型调用;否则走环境变量/默认
        "active": active,
        "source": "connection" if active else "env_or_default",
    }


def _validate_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="API Base URL 需以 http:// 或 https:// 开头")
    return url


class ConnectionConfigRequest(BaseModel):
    """保存单条主连接。api_key 传空串 = 沿用已保存的 Key(避免回显明文)。"""

    enabled: bool = True
    base_url: str = Field(min_length=1, max_length=500, description="OpenAI 兼容 API 地址")
    api_key: str = Field(default="", max_length=500, description="API Key,空串表示保持已存值")
    default_model: str = Field(default="", max_length=200, description="默认模型 id;空则用 config 出厂默认")
    models: list[str] = Field(default_factory=list, description="可选模型 id 列表(供前端建议)")


class ConnectionProbeRequest(BaseModel):
    base_url: Optional[str] = Field(default=None, description="为空则用当前已存/默认地址")
    api_key: Optional[str] = Field(default=None, description="为空则尝试用当前已存 Key")


def probe_models(base_url: str, api_key: str) -> list:
    """调用 OpenAI 兼容的 GET {base_url}/models,返回排序后的模型 id 列表。

    openai 只在探测时需要,故保持函数内导入(避免为可选能力付导入成本)。
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key or "unused", base_url=base_url, timeout=20, max_retries=1)
    resp = client.models.list()
    ids = sorted({getattr(m, "id", "") for m in resp.data})
    return [i for i in ids if i]


@router.get("/api/connection")
def get_connection():
    return _connection_view()


@router.put("/api/connection")
def save_connection(req: ConnectionConfigRequest):
    previous = runtime_connection.load_state()
    new_key = req.api_key.strip()
    runtime_connection.save_state({
        "enabled": req.enabled,
        "base_url": _validate_base_url(req.base_url),
        "api_key": new_key or previous["api_key"],
        "default_model": req.default_model.strip(),
        "models": req.models,
    })
    # 连接参数已变化,清掉旧参数的 LLM 实例缓存,下次调用按新配置重建。
    # 走 LLM_base 的**公开**接口:直接 clear 私有 _llm_cache 会绕开它的双检锁。
    from gzhu_cup2.agent.core.models.LLM_base import clear_llm_cache
    clear_llm_cache()
    return _connection_view()


@router.post("/api/connection/test")
def test_connection(req: ConnectionProbeRequest):
    """用表单中的 base_url/api_key 探测可用模型(不落库)。"""
    url = _validate_base_url(req.base_url or runtime_connection.resolve_base_url())
    key = (req.api_key or "").strip() or runtime_connection.resolve_api_key() or ""
    try:
        models = probe_models(url, key)
    except Exception as e:  # noqa: BLE001 - 探测失败统一转成 502 提示
        raise HTTPException(
            status_code=502,
            detail=f"连接验证失败:请检查 API 地址与 Key({type(e).__name__}: {e})",
        )
    if not models:
        raise HTTPException(status_code=502, detail="连接成功,但该地址未返回任何模型")
    return {"ok": True, "models": models}
