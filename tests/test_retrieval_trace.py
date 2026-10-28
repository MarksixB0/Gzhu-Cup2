# -*- coding: utf-8 -*-
"""检索留痕:读侧组装按次落审计记录;独立命名空间、滚动上限、wipe 覆盖。

红线回归:留痕绝不写进 facts/episodes(自我污染),debug 用途不产生留痕。
"""
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import retrieve as mretrieve
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory import trace as mtrace

_USER = "trace_u"


def _seed_fact(user: str, text: str) -> None:
    mstore.merge_fact(user, text, "life", 0.6)


def test_reply_retrieval_writes_trace():
    _seed_fact(_USER, "用户喜欢在图书馆自习")
    text = mretrieve.build_memory_context(_USER, query="我喜欢在哪里自习来着")
    assert "图书馆" in text
    rows = mtrace.list_recent(_USER)
    assert len(rows) == 1
    r = rows[0]
    assert r["purpose"] == "reply"
    assert "自习" in r["query"]
    # FTS 观测字段恒在(内存后端无索引 → engaged=False);选中明细与预算在记录里
    assert r["fts_engaged"] is False
    assert r["fts_applied"] is False
    assert r["fts_hits"] == 0
    assert r["fts_narrowed"] is False
    assert r["fts_fallback"] is False
    # 即使 FTS 未介入,也要留下"初筛前/后各有多少候选"的基线
    assert r["candidates"]["facts"]["raw"] == 1
    assert r["candidates"]["facts"]["eligible"] == 1
    assert r["candidates"]["facts"]["kept"] == 1
    assert r["intent_topics"] == []
    assert r["n_selected"] >= 1
    assert r["selected"] and "图书馆" in r["selected"][0]["text"]
    assert r["budget"] == cfg.MEMORY_RETRIEVE_MAX_CHARS


def test_care_purpose_recorded_debug_not():
    _seed_fact(_USER, "用户最近在准备答辩")
    mretrieve.build_memory_context(_USER, purpose="care")
    mretrieve.build_memory_context(_USER, purpose="debug")
    rows = mtrace.list_recent(_USER)
    assert [r["purpose"] for r in rows] == ["care"]


def test_trace_disabled_switch(monkeypatch):
    monkeypatch.setattr(cfg, "MEMORY_TRACE_ENABLED", False)
    _seed_fact(_USER, "用户养了一只猫")
    mretrieve.build_memory_context(_USER)
    assert mtrace.list_recent(_USER) == []


def test_paused_recording_writes_no_trace(privacy_state_file):
    """暂停记录 = 连留痕也不写:它含 query 原文与记忆摘录,否则等于绕过开关。"""
    from gzhu_cup2.agent.long_memory import privacy as mprivacy

    mprivacy.set_recording(_USER, False)
    _seed_fact(_USER, "用户喜欢在图书馆自习")
    mretrieve.build_memory_context(_USER, query="我喜欢在哪里自习来着")
    assert mtrace.list_recent(_USER) == []

    # 恢复记录后照常落痕(暂停只是"不写新的",不是永久关闭)
    mprivacy.set_recording(_USER, True)
    mretrieve.build_memory_context(_USER, query="我喜欢在哪里自习来着")
    assert len(mtrace.list_recent(_USER)) == 1


def test_cap_rolling_and_ttl(monkeypatch):
    monkeypatch.setattr(cfg, "MEMORY_TRACE_MAX_ITEMS", 2)
    monkeypatch.setattr(cfg, "MEMORY_TRACE_TTL_DAYS", 1)
    old = 1_000_000_000.0                      # 远早于 now 的时刻(TTL 之外)
    mtrace.record(_USER, "reply", {"query": "一"}, now=old)
    mtrace.record(_USER, "reply", {"query": "二"}, now=old + 86400.0 * 30)
    mtrace.record(_USER, "care", {"query": "三"})
    rows = mtrace.list_recent(_USER)
    # TTL 清掉两条旧记录;上限内只剩最新一条
    assert len(rows) == 1
    assert rows[0]["query"] == "三"


def test_wipe_covers_trace():
    from gzhu_cup2.agent.web import routes_admin
    from gzhu_cup2.agent.web import sessions
    mtrace.record(sessions.default_user(), "reply", {"query": "会被清掉"})
    assert len(mtrace.list_recent(sessions.default_user())) == 1
    out = routes_admin.memory_wipe(confirm=1)
    assert out["ok"] is True
    assert out["removed"].get("retrieval_trace") == 1
    assert mtrace.list_recent(sessions.default_user()) == []


def test_record_failure_never_raises(monkeypatch):
    # 留痕是增强:store 坏掉只告警,不向调用方抛
    def _boom(*a, **k):
        raise RuntimeError("store down")
    monkeypatch.setattr(mstore, "put_item", _boom)
    mtrace.record(_USER, "reply", {"query": "x"})     # 不应抛
