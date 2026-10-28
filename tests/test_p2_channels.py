# -*- coding: utf-8 -*-
"""P2 新增能力离线测试(不触网):

- 危机级即时固化判定 store.pending_is_urgent;
- 词表旁路通道 lexicon.score_neg 与日桶聚合;
- 固化双采样投票 merge_extractions 与 extract_memory_voted。
"""
import time
from datetime import date, timedelta

from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import ticks
from gzhu_cup2.agent.algo.components import lexicon
from gzhu_cup2.agent.long_memory import consolidate as cons
from gzhu_cup2.agent.core.schemas import MemoryExtraction, MemoryFact, CrisisFacets

USER = "u-p2"


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


# ============================ 危机级即时固化 ============================
class TestPendingUrgent:
    def test_high_or_crisis_urgent(self):
        mem_store.capture_turn(USER, "t1", "我很难受", "我在",
                               meta={"risk_level": "low", "negative_score": 0.3})
        pending = mem_store.get_pending(USER)
        assert mem_store.pending_is_urgent(pending) is False
        mem_store.capture_turn(USER, "t1", "我想结束生命", "请拨打热线",
                               meta={"risk_level": "crisis"})
        pending = mem_store.get_pending(USER)
        assert mem_store.pending_is_urgent(pending) is True
        assert mem_store.pending_is_urgent(None) is False


# ============================ 词表旁路通道 ============================
class TestLexicon:
    def test_negative_hits_scored(self):
        s, neg, pos = lexicon.score_neg("我最近好难过，压力好大，睡不着")
        assert s >= 0.5
        assert "难过" in neg and "压力" in neg

    def test_positive_reduces(self):
        s1, _, _ = lexicon.score_neg("我今天好难过")
        s2, _, _ = lexicon.score_neg("我今天好难过，但是和朋友出去玩了，很开心")
        assert s2 < s1

    def test_neutral_low(self):
        s, _, _ = lexicon.score_neg("今天天气不错，中午吃了面")
        assert s < 0.3


class TestLexiconInBucket:
    def test_capture_meta_aggregates_lex_fields(self):
        mem_store.capture_turn(USER, "t1", "最近压力好大很难受", "我在听", meta={
            "risk_level": "low", "negative_score": 0.7,
            "lex_neg": 0.6,
        })
        day = ticks._day_str(time.time())
        b = mem_store.get_item(ticks.ns_ticks(USER), day)
        assert b is not None
        assert int(b.get("lex_neg_n", 0)) == 1
        assert abs(float(b.get("lex_neg_sum", 0.0)) - 0.6) < 1e-6


# ============================ 双采样投票 ============================
class TestConsolidateVote:
    def test_merge_keeps_only_consistent_items(self):
        a = MemoryExtraction(
            digest="A",
            facts=[MemoryFact(text="用户养了一只猫", category="life", importance=0.7),
                   MemoryFact(text="用户最近在准备答辩", category="work", importance=0.6)],
            topics=["family", "school"],
            crisis=CrisisFacets(ideation="我想结束生命", plan="我买了药"),
        )
        b = MemoryExtraction(
            digest="B",
            facts=[MemoryFact(text="用户养了一只猫", category="life", importance=0.8),
                   MemoryFact(text="用户周末去了趟公园", category="life", importance=0.4)],
            topics=["family", "sleep"],
            crisis=CrisisFacets(ideation="我想结束生命", plan="准备从高处跳下去"),
        )
        m = cons.merge_extractions([a, b])
        # ⚠ 2026-09-13 口径变更(任务 V):**全部字段改为并集保留 + 支持度标注**。
        #   旧口径要求条目在两份样本中逐字一致才留,实测丢弃率 85.1%、段级一致率 1.7%,
        #   且被丢弃的候选里 78.4% 是原文确有依据的真信息(如「用户对咖啡因比较敏感」
        #   的两种说法被一起扔掉,导致该事实从未落库)。
        #   并集把两条都留下;支持度(2/2 或 1/2)供检索侧区分强弱证据(见 memory_score)。
        assert [f.text for f in m.facts] == [
            "用户养了一只猫", "用户最近在准备答辩", "用户周末去了趟公园",
        ], "事实应取并集(首次出现序)"
        assert m.topics == ["family", "school", "sleep"], "短列表应取并集"
        assert m.crisis is not None
        assert getattr(m.crisis, "ideation", "") == "我想结束生命"
        assert getattr(m.crisis, "plan", "") == "准备从高处跳下去", "plan 应保留(取最长)"

    def test_merge_reports_support(self):
        """并集同时给出支持度:2/2 = 强证据;a、b 各自独有的 = 弱证据。"""
        a = MemoryExtraction(digest="A", facts=[
            MemoryFact(text="用户养了一只猫"), MemoryFact(text="用户最近在准备答辩")])
        b = MemoryExtraction(digest="B", facts=[
            MemoryFact(text="用户养了一只猫"), MemoryFact(text="用户周末去了趟公园")])
        sup: dict = {}
        cons.merge_extractions([a, b], support_out=sup)
        assert sup["vote_samples"] == 2
        assert sup["facts"] == {
            "用户养了一只猫": 2, "用户最近在准备答辩": 1, "用户周末去了趟公园": 1,
        }

    def test_merge_single_returns_as_is(self):
        a = MemoryExtraction(digest="x")
        assert cons.merge_extractions([a]) is a

    def test_voted_two_samples(self):
        calls = []

        class Stub:
            def with_structured_output(self, schema, **kwargs):
                def build(_m):
                    calls.append(1)
                    return MemoryExtraction(
                        digest="d",
                        facts=[MemoryFact(text="用户喜欢雨天", category="preference",
                                          importance=0.6)],
                    )
                return type("H", (), {"invoke": lambda self, m, **k: build(m)})()

        pending = {"turns": [{"user": "今天下雨了，我喜欢听雨声", "ai": "嗯",
                              "meta": {}}], "next_seq": 1,
                   "started_at": time.time(), "last_ts": time.time(),
                   "threads": ["t"]}
        r = cons.extract_memory_voted(pending, llm=Stub(), samples=2)
        assert len(calls) == 2
        assert r.digest == "d" and len(r.facts) == 1

    def test_samples_one_equals_single(self, monkeypatch):
        # samples=1 → 走单次抽取路径
        monkeypatch.setattr(cons, "extract_memory",
                            lambda pending, llm=None, user=None:
                            MemoryExtraction(digest="solo"))
        r = cons.extract_memory_voted({}, samples=1)
        assert r.digest == "solo"

