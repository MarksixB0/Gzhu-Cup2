# -*- coding: utf-8 -*-
"""P0 新增能力离线测试(不触网):

1. 危机四要素(risk_events):清洗纯函数 / 独立落库 / 受保护(不参与遗忘) / 窗口计数;
2. 个人基线 + 恶化检测(baseline_and_drift):持续变差识别、平稳不误报、数据不足返回 None。
"""
import time
from datetime import date, timedelta

from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import forget
from gzhu_cup2.agent.long_memory import tags
from gzhu_cup2.agent.long_memory import trend
from gzhu_cup2.agent.core.schemas import MemoryExtraction, MemoryFact, CrisisFacets

USER = "u-risk"


# ============================ 危机分面清洗(纯函数) ============================
class TestCleanCrisisFacets:
    def test_keeps_nonempty_trimmed(self):
        out = tags.clean_crisis_facets(CrisisFacets(
            ideation="  我想结束生命  ", plan="把药都攒起来了", behavior="",
        ))
        assert out == {"ideation": "我想结束生命", "plan": "把药都攒起来了"}

    def test_none_or_empty_returns_empty(self):
        assert tags.clean_crisis_facets(None) == {}
        assert tags.clean_crisis_facets(CrisisFacets()) == {}


# ============================ risk_events 落库 / 保护 ============================
def _pending_snapshot(texts=("我最近睡不好", "感觉什么都没意思")):
    now = time.time()
    turns = [
        {"seq": i, "ts": now - (len(texts) - i), "thread": "t1",
         "user": t, "ai": "我在听", "meta": {"risk_level": "low"}}
        for i, t in enumerate(texts)
    ]
    return {
        "turns": turns, "next_seq": len(turns),
        "started_at": now - 10, "last_ts": now, "threads": ["t1"],
    }


class TestRiskEvents:
    def test_add_and_count(self):
        mem_store.add_risk_event(USER, "ideation", "我想一了百了", ts=time.time())
        mem_store.add_risk_event(USER, "plan", "已经想好时间了", ts=time.time())
        items = mem_store.list_risk_events(USER)
        assert len(items) == 2
        cnt = mem_store.risk_facet_counts(USER)
        assert cnt == {"ideation": 1, "plan": 1}

    def test_add_ignores_empty(self):
        mem_store.add_risk_event(USER, "ideation", "")
        mem_store.add_risk_event(USER, "", "x")
        assert mem_store.list_risk_events(USER) == []

    def test_commit_segment_writes_risk_events_and_protects_episode(self):
        pending = _pending_snapshot()
        extraction = MemoryExtraction(
            digest="用户表达了自杀意念与具体计划",
            facts=[MemoryFact(text="用户最近压力很大", category="emotion", importance=0.7)],
            importance=0.9,
            risk_note="用户提到想结束生命",
            crisis=CrisisFacets(ideation="我想结束生命", plan="把药都攒起来了"),
        )
        mem_store.commit_segment(USER, pending, extraction)

        events = mem_store.list_risk_events(USER)
        assert len(events) == 2
        facets = {e.value["facet"] for e in events}
        assert facets == {"ideation", "plan"}
        assert all(e.value["protected"] is True for e in events)

        episodes = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))]
        assert episodes and episodes[0]["protected"] is True
        assert episodes[0]["category"] == "risk"

    def test_risk_events_survive_forgetting(self):
        mem_store.commit_segment(USER, _pending_snapshot(), MemoryExtraction(
            digest="x", crisis=CrisisFacets(ideation="我想死"),
        ))
        # 再放一条陈旧低价值事实确保遗忘确实跑过
        old = time.time() - 100 * 86400
        mem_store.put_item(mem_store.ns_facts(USER), "k-old", {
            "type": "fact", "text": "旧低价值事实", "category": "other",
            "importance": 0.1, "mentions": 1,
            "first_seen": old, "last_seen": old, "archived": False, "protected": False,
        })
        forget.apply_user_forgetting(USER)
        assert len(mem_store.list_risk_events(USER)) == 1  # 受保护,仍在
        assert mem_store.risk_facet_counts(USER) == {"ideation": 1}

    def test_risk_events_commit_idempotent(self):
        """同一 pending 段重复固化(重试/重复执行)不产生重复风险事件。"""
        pending = _pending_snapshot()
        extraction = MemoryExtraction(
            digest="x", crisis=CrisisFacets(ideation="我想结束生命", plan="想好时间了"),
        )
        mem_store.commit_segment(USER, pending, extraction)
        mem_store.commit_segment(USER, pending, extraction)  # 再次固化同一快照
        events = mem_store.list_risk_events(USER)
        assert len(events) == 2  # 仍是 ideation + plan 各一条
        assert mem_store.risk_facet_counts(USER) == {"ideation": 1, "plan": 1}

    def test_two_write_paths_share_same_deterministic_key(self):
        """add_risk_event 与 commit_segment 必须产生同一个键。

        commit_segment 已改为复用 add_risk_event;此用例锁住"两条路径键一致",
        否则混用(CLI 手动补录 + 后台固化)会重复计数、污染分面统计。
        """
        pending = _pending_snapshot()
        mem_store.add_risk_event(
            USER, "ideation", "我想结束生命",
            ts=float(pending["last_ts"]), thread=(pending["threads"] or [None])[0],
        )
        mem_store.commit_segment(USER, pending, MemoryExtraction(
            digest="x", crisis=CrisisFacets(ideation="我想结束生命"),
        ))
        assert len(mem_store.list_risk_events(USER)) == 1
        assert mem_store.risk_facet_counts(USER) == {"ideation": 1}


# ============================ 个人基线 / 恶化检测 ============================
def _put_bucket(day_key: str, neg: float):
    mem_store.put_item(("memory", USER, "ticks"), day_key, {
        "day": day_key, "count": 1, "neg_sum": float(neg), "neg_n": 1,
    })


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


class TestBaselineAndDrift:
    def test_sustained_worsening_detected(self):
        # 基线:较早 3 天稳定偏低;最近 3 天连续走高 → 持续恶化
        for d in (-9, -8, -7):
            _put_bucket(_day(d), 0.2)
        for d, v in ((-2, 0.6), (-1, 0.75), (0, 0.85)):
            _put_bucket(_day(d), v)
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["sustained_worsening"] is True
        assert rep["consecutive_above"] >= 3

    def test_flat_recent_is_not_worsening(self):
        for d in (-9, -8, -7, -2, -1, 0):
            _put_bucket(_day(d), 0.4)
        rep = trend.baseline_and_drift(USER, now=time.time())
        assert rep is not None
        assert rep["sustained_worsening"] is False

    def test_insufficient_data_returns_none(self):
        _put_bucket(_day(0), 0.5)
        assert trend.baseline_and_drift(USER, now=time.time()) is None
