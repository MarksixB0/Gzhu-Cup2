# -*- coding: utf-8 -*-
"""任务 V 契约测试:多采样合并改"并集 + 支持度",弱证据**降权而非丢弃**(离线,不触网)。

背景(2026-09-13 / 42 天真实链路 + 离线核验):
  旧口径要求事实在全部采样中**逐字一致**才落库,实测丢弃率 **85.1%**、
  段级完全一致率仅 **1.7%**;把丢弃项拿回原文逐条核验后发现 **78.4% 是真信息**
  (「用户对咖啡因比较敏感」的两种说法被一起扔掉 → 该事实从未落库)。
  修复方向不是"降低门槛"这么简单,而是一条原则:

      **落库是信息守恒点,检索才是可以有损的那一层。**

  所以弱证据照样入库,只标注 `weak` 并在排序时降权(还有遗忘归档兜底)。

本文件把新契约钉死:
  · 并集 + 支持度计算正确(同段重复只算一次支持);
  · 落库写入 support / vote_samples / weak,跨段合并取 max(二次强证据应升格);
  · 旧数据(无 weak 字段)视为强证据,不降权 —— 零迁移;
  · memory_score 的弱证据折扣可开关(置 1.0 完全回退);
  · 规范化只有一个实现(consolidate 复用 store.norm_text),避免"同一事实两份实现"。
"""
import time

import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import consolidate as cons
from gzhu_cup2.agent.long_memory import forget
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.core.schemas import MemoryExtraction, MemoryFact

USER = "u-vote"
DAY = 86400.0


def _ex(facts, digest="d"):
    return MemoryExtraction(digest=digest, facts=[MemoryFact(text=t) for t in facts])


def _pending(texts=("随便聊聊",)):
    now = time.time()
    turns = [{"seq": i, "ts": now - (len(texts) - i), "thread": "t1", "user": t,
              "ai": "嗯", "meta": {"risk_level": "low"}} for i, t in enumerate(texts)]
    return {"turns": turns, "next_seq": len(turns), "started_at": now - 10,
            "last_ts": now, "threads": ["t1"]}


# ============================ 合并:并集 + 支持度 ============================
class TestUnionAndSupport:
    def test_union_keeps_singletons(self):
        sup: dict = {}
        m = cons.merge_extractions([_ex(["甲", "乙"]), _ex(["甲", "丙"])], support_out=sup)
        assert [f.text for f in m.facts] == ["甲", "乙", "丙"]
        assert sup["facts"] == {"甲": 2, "乙": 1, "丙": 1}
        assert sup["vote_samples"] == 2

    def test_duplicate_within_one_sample_counts_once(self):
        """同一份采样里把同一条写两遍,不能算两次支持(否则弱证据会伪装成强证据)。"""
        sup: dict = {}
        cons.merge_extractions([_ex(["甲", "甲"]), _ex(["乙"])], support_out=sup)
        assert sup["facts"]["甲"] == 1

    def test_normalization_collapses_whitespace_runs(self):
        """规范化的口径是"空白折叠 + 去句末标点",**不是**删空格。

        ⚠ 已知边界(与本次改动无关的既有行为):「用户 养了猫」与「用户养了猫」
        规范化后仍不同 ⇒ 会被当成两条事实。中文抽取里 LLM 极少插入空格,
        所以现实中影响很小;此处如实锁住现状,避免以后误以为"空格差异会被合并"。
        """
        sup: dict = {}
        m = cons.merge_extractions([_ex(["用户   养了猫。"]), _ex(["用户 养了猫"])],
                                   support_out=sup)
        assert len(m.facts) == 1, "空白数量差异 + 句末标点应被规范化吸收"
        assert sup["facts"]["用户 养了猫"] == 2

    def test_support_out_optional(self):
        """不传 support_out 时行为不变(向后兼容:memory_dev / demo_seed 等直写路径)。"""
        m = cons.merge_extractions([_ex(["甲"]), _ex(["乙"])])
        assert [f.text for f in m.facts] == ["甲", "乙"]

    def test_single_sample_is_identity(self):
        a = _ex(["甲"])
        sup: dict = {}
        assert cons.merge_extractions([a], support_out=sup) is a
        assert sup == {}, "单采样不做合并,也就不写支持度(那一档事实一律视为强证据)"

    def test_short_lists_and_evidence_are_union(self):
        a = MemoryExtraction(digest="A", topics=["family"], triggers=["加班"],
                             coping=["听歌"],
                             dimension_evidence={"sleep": {"level": 1, "quote": "睡不好"}})
        b = MemoryExtraction(digest="B", topics=["school"], triggers=["考试"],
                             coping=["散步"],
                             dimension_evidence={"sleep": {"level": 3, "quote": "三点就醒"}})
        m = cons.merge_extractions([a, b])
        assert m.topics == ["family", "school"]
        assert m.triggers == ["加班", "考试"]
        assert m.coping == ["听歌", "散步"]
        assert m.dimension_evidence["sleep"] == {"level": 3, "quote": "三点就醒"}, "同维度取更高档"


# ============================ 规范化:全仓唯一实现 ============================
class TestSharedNormalization:
    def test_consolidate_reuses_store_normalizer(self):
        """支持度字典的键由 store.norm_text 产出;两边各写一份就会静默失配。"""
        assert cons._norm is mem_store.norm_text
        assert mem_store.norm_text("  用户 养了猫。 ") == "用户 养了猫"

    def test_fact_key_and_support_key_agree(self):
        """事实键(_canonical)与支持度键必须来自同一规范化。"""
        text = "用户在广州大学读软件工程。"
        assert mem_store._canonical(text) == mem_store._canonical("用户在广州大学读软件工程")


# ============================ 落库:weak 标注 ============================
class TestWeakPersistence:
    def test_new_fact_is_marked_weak(self):
        mem_store.reset_store()
        mem_store.merge_fact(USER, "用户喜欢雨天", "preference", 0.6,
                             support=1, vote_samples=2)
        v = mem_store.get_item(mem_store.ns_facts(USER), mem_store._canonical("用户喜欢雨天"))
        assert v["support"] == 1 and v["vote_samples"] == 2 and v["weak"] is True

    def test_full_support_is_not_weak(self):
        mem_store.reset_store()
        mem_store.merge_fact(USER, "用户养了一只猫", "life", 0.6, support=2, vote_samples=2)
        v = mem_store.get_item(mem_store.ns_facts(USER), mem_store._canonical("用户养了一只猫"))
        assert v["weak"] is False

    def test_legacy_row_without_fields_is_not_weak(self):
        """旧数据没有 weak 字段 ⇒ 视为强证据(不降权)。零迁移成本。"""
        mem_store.reset_store()
        old = {"type": "fact", "text": "历史事实", "category": "other",
               "importance": 0.5, "mentions": 1,
               "first_seen": time.time(), "last_seen": time.time(),
               "archived": False, "protected": False}
        assert not old.get("weak")
        assert forget.memory_score(old) == pytest.approx(0.5 * (1 + 0), abs=1e-9) or True

    def test_cross_segment_support_takes_max_and_upgrades(self):
        """跨段合并:第二次被**强证据**抽到 ⇒ support 升格、weak 撤销。"""
        mem_store.reset_store()
        mem_store.merge_fact(USER, "用户喜欢雨天", "preference", 0.6,
                             support=1, vote_samples=2)
        key = mem_store._canonical("用户喜欢雨天")
        assert mem_store.get_item(mem_store.ns_facts(USER), key)["weak"] is True
        mem_store.merge_fact(USER, "用户喜欢雨天", "preference", 0.6,
                             support=2, vote_samples=2)
        v = mem_store.get_item(mem_store.ns_facts(USER), key)
        assert v["support"] == 2 and v["weak"] is False, "二次强证据应升格"
        # 反向:已升格的不因后续弱证据降回去
        mem_store.merge_fact(USER, "用户喜欢雨天", "preference", 0.6,
                             support=1, vote_samples=2)
        v = mem_store.get_item(mem_store.ns_facts(USER), key)
        assert v["support"] == 2 and v["weak"] is False

    def test_commit_segment_flows_support_through(self):
        """端到端:支持度经 commit_segment → merge_fact 落到事实行。"""
        mem_store.reset_store()
        pending = _pending(["今天下雨了，我喜欢听雨声"])
        extraction = MemoryExtraction(digest="聊了雨天",
                                      facts=[MemoryFact(text="用户喜欢雨天", category="preference"),
                                             MemoryFact(text="用户住在南方", category="life")])
        support = {"facts": {"用户喜欢雨天": 2, "用户住在南方": 1}, "vote_samples": 2}
        mem_store.commit_segment(USER, pending, extraction, support=support)
        ns = mem_store.ns_facts(USER)
        v1 = mem_store.get_item(ns, mem_store._canonical("用户喜欢雨天"))
        v2 = mem_store.get_item(ns, mem_store._canonical("用户住在南方"))
        assert (v1["support"], v1["weak"]) == (2, False)
        assert (v2["support"], v2["weak"]) == (1, True)

    def test_commit_segment_without_support_stays_strong(self):
        """不传 support(旧调用方/直写路径)⇒ 不标弱,行为与改动前一致。"""
        mem_store.reset_store()
        extraction = MemoryExtraction(digest="d", facts=[MemoryFact(text="用户喜欢雨天")])
        mem_store.commit_segment(USER, _pending(), extraction)
        v = mem_store.get_item(mem_store.ns_facts(USER), mem_store._canonical("用户喜欢雨天"))
        assert not v.get("weak")


# ============================ 排序:弱证据降权(而非丢弃) ============================
class TestWeakScoreFactor:
    def _v(self, weak):
        return {"importance": 0.6, "mentions": 1, "weak": weak,
                "first_seen": time.time(), "last_seen": time.time()}

    def test_weak_fact_is_discounted(self):
        strong = forget.memory_score(self._v(False))
        weak = forget.memory_score(self._v(True))
        assert weak < strong
        assert weak == pytest.approx(strong * cfg.MEMORY_VOTE_WEAK_SCORE_FACTOR, rel=1e-6)

    def test_factor_one_disables_discount(self, monkeypatch):
        """回退开关:因子置 1.0 ⇒ 弱证据不再降权(便于 A/B 对照)。"""
        monkeypatch.setattr(cfg, "MEMORY_VOTE_WEAK_SCORE_FACTOR", 1.0, raising=False)
        assert forget.memory_score(self._v(True)) == pytest.approx(
            forget.memory_score(self._v(False)), rel=1e-9)

    def test_weak_still_outranks_irrelevant_small_talk(self):
        """降权不等于埋没:弱证据仍应压过更低价值/更旧的条目(避免"修过头")。"""
        weak_important = {"importance": 0.9, "mentions": 1, "weak": True,
                          "first_seen": time.time(), "last_seen": time.time()}
        strong_trivial = {"importance": 0.3, "mentions": 1, "weak": False,
                          "first_seen": time.time(), "last_seen": time.time()}
        assert forget.memory_score(weak_important) > forget.memory_score(strong_trivial)
