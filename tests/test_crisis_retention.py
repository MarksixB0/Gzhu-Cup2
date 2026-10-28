# -*- coding: utf-8 -*-
"""危机留痕的**可靠性契约**测试(离线,不触网、不落盘)。

背景(2026-09-13 / 42 天真实链路实测):
  语料里唯一一次明确的危机意念「最近总是觉得活着没意思」,前台协议正确触发,
  但**后台留下 0 条 risk_events** —— 原因是两条链路上的口径问题:
    ① `merge_extractions` 对 crisis 分面要求"两次采样引文逐字一致",
       而两次分别给出「活着没意思」与「最近总是觉得活着没意思」→ 双双被丢弃;
    ② `commit_segment` 只在分面非空时才写 risk_events,LLM 只写了自由文本 risk_note → 什么都不落。
  危机留痕是整套设计里唯一声称"受保护、永不遗忘"的部分,它不能依赖 LLM 的配合与采样运气。

本文件把修复后的契约钉死:
  · 分面:**并集**保留(任一采样命中即留),多条取最长;
  · 兜底:段内出现 high/crisis 轮次 → **必写**一条 `signal`(用户原话,不经 LLM);
  · 无风险段**不得**误写(避免"独立事件数"被污染);
  · 幂等重提交不产生重复事件、也不误报警告。
"""
import logging
import time

import pytest

from gzhu_cup2.agent.long_memory import consolidate as cons
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.core.schemas import MemoryExtraction, MemoryFact, CrisisFacets

USER = "u-crisis"


# ============================ 构造工具 ============================
def _pending(texts, risk_levels=None):
    """构造待固化段快照;risk_levels 缺省全 low。"""
    now = time.time()
    levels = list(risk_levels or ["low"] * len(texts))
    turns = [
        {"seq": i, "ts": now - (len(texts) - i), "thread": "t1", "user": t,
         "ai": "我在听", "meta": {"risk_level": levels[i]}}
        for i, t in enumerate(texts)
    ]
    return {"turns": turns, "next_seq": len(turns), "started_at": now - 10,
            "last_ts": now, "threads": ["t1"]}


def _ex(crisis=None, risk_note="", digest="聊了聊近况"):
    return MemoryExtraction(digest=digest, facts=[], importance=0.6,
                            risk_note=risk_note, crisis=crisis)


# ============================ B-1:分面并集保留 ============================
class TestCrisisFacetsUnion:
    def test_different_quotes_are_both_kept_take_longest(self):
        """**实测翻车案例的回归**:两次采样引文不同 ⇒ 旧口径全丢,新口径取最长保留。

        「活着没意思」 vs 「最近总是觉得活着没意思」 —— 修复前 risk_events 为 0。
        """
        a = _ex(crisis=CrisisFacets(ideation="活着没意思"))
        b = _ex(crisis=CrisisFacets(ideation="最近总是觉得活着没意思"))
        merged = cons.merge_extractions([a, b])
        assert merged.crisis is not None, "引文不同也必须有分面(旧口径在这里丢弃)"
        assert merged.crisis.ideation == "最近总是觉得活着没意思", "应取最长(信息更完整)"

    def test_single_sample_hit_is_enough(self):
        """只有一个样本命中也要保留 —— 危机证据不接受"另一个样本没抽到"作为丢弃理由。"""
        a = _ex(crisis=CrisisFacets(plan="已经想好时间了"))
        b = _ex(crisis=None)
        merged = cons.merge_extractions([a, b])
        assert merged.crisis is not None and merged.crisis.plan == "已经想好时间了"

    def test_multiple_facets_union(self):
        a = _ex(crisis=CrisisFacets(ideation="不想活了"))
        b = _ex(crisis=CrisisFacets(behavior="上周划了自己一下"))
        merged = cons.merge_extractions([a, b])
        assert merged.crisis is not None
        assert merged.crisis.ideation == "不想活了"
        assert merged.crisis.behavior == "上周划了自己一下"

    def test_no_crisis_at_all_stays_none(self):
        """非危机段不得凭空产生分面(防误报)。"""
        merged = cons.merge_extractions([_ex(), _ex(risk_note="有点累")])
        assert merged.crisis is None

    def test_single_char_quote_filtered(self):
        """1 字引文视为噪声丢弃(唯一的门槛;但仍不要求跨样本逐字一致)。

        注:用两个样本走**合并**路径 —— `merge_extractions([a])` 单样本是恒等返回
        (不合并、也就是不过滤),那是既有契约(见 test_p2_channels)。
        """
        merged = cons.merge_extractions([
            _ex(crisis=CrisisFacets(ideation="死")), _ex(crisis=None)])
        assert merged.crisis is None

    def test_normal_fields_are_union_too(self):
        """⚠ 危机不再是唯一例外:任务 V 之后**所有字段都是并集**(只被部分采样抽到的不丢)。

        本用例的前身断言"事实仍按全部样本一致投票";该口径已被实测否决
        (丢弃率 85.1%,且 78.4% 的丢弃项是原文确有依据的真信息),故改为并集契约。
        弱证据如何被约束:落库时标 `weak`,检索排序乘 MEMORY_VOTE_WEAK_SCORE_FACTOR。
        """
        a = _ex(crisis=None)
        a.facts = [MemoryFact(text="用户养了一只猫")]
        b = _ex(crisis=None)
        b.facts = [MemoryFact(text="用户养了一只狗")]
        merged = cons.merge_extractions([a, b])
        assert [f.text for f in merged.facts] == ["用户养了一只猫", "用户养了一只狗"]


# ============================ B-2:兜底 signal 留痕 ============================
class TestSignalFallback:
    def test_crisis_turn_always_leaves_a_trace(self):
        """运行时判过 crisis 的轮次 ⇒ 即使 LLM 什么都没给,也必须留痕(引文=用户原话)。"""
        mem_store.reset_store()
        pending = _pending(["最近总是觉得活着没意思"], risk_levels=["crisis"])
        mem_store.commit_segment(USER, pending, _ex(crisis=None, risk_note=""))
        events = [it.value for it in mem_store.list_risk_events(USER)]
        assert len(events) == 1, "危机轮必须留下一条留痕"
        assert events[0]["facet"] == "signal"
        assert events[0]["quote"] == "最近总是觉得活着没意思", "引文必须是用户原话"
        assert events[0]["protected"] is True

    def test_high_risk_turn_also_leaves_a_trace(self):
        mem_store.reset_store()
        pending = _pending(["我撑不住了"], risk_levels=["high"])
        mem_store.commit_segment(USER, pending, _ex(crisis=None))
        events = [it.value for it in mem_store.list_risk_events(USER)]
        assert len(events) == 1 and events[0]["facet"] == "signal"

    def test_risk_note_without_facets_leaves_a_trace(self):
        """分面为空但 LLM 留了 risk_note(实测最常见的形态)⇒ 仍要落痕。"""
        mem_store.reset_store()
        pending = _pending(["我很难受"], risk_levels=["low"])
        mem_store.commit_segment(USER, pending,
                                _ex(crisis=None, risk_note="用户表达活着没意思、极度疲惫绝望感"))
        events = [it.value for it in mem_store.list_risk_events(USER)]
        assert len(events) == 1 and events[0]["facet"] == "signal"
        assert "活着没意思" in events[0]["quote"]

    def test_no_signal_when_facets_are_complete(self):
        """分面齐备时**不**额外写 signal —— 否则一次危机会被算成两个"独立事件"。

        实测曾出现:同一危机轮既落 `ideation` 又落 `signal`,引文完全相同,
        `risk_facets_total` 因此虚增。分面非空即已满足"必有留痕"。
        """
        mem_store.reset_store()
        pending = _pending(["我想结束生命"], risk_levels=["crisis"])
        mem_store.commit_segment(USER, pending, _ex(
            crisis=CrisisFacets(ideation="我想结束生命"), risk_note="提到想结束生命"))
        facets = sorted(it.value["facet"] for it in mem_store.list_risk_events(USER))
        assert facets == ["ideation"], "已有分面时不应重复留 signal(即使运行时也判了 crisis)"

    def test_normal_segment_writes_nothing(self):
        """非风险段不得误写(保护"独立事件数"的口径)。"""
        mem_store.reset_store()
        mem_store.commit_segment(USER, _pending(["今天天气不错", "晚上吃了面"]),
                                 _ex(crisis=None, risk_note=""))
        assert mem_store.list_risk_events(USER) == []

    def test_signal_is_idempotent(self):
        """同一快照重复固化(崩溃重试)不产生重复事件。"""
        mem_store.reset_store()
        pending = _pending(["不想活了"], risk_levels=["crisis"])
        mem_store.commit_segment(USER, pending, _ex(crisis=None))
        mem_store.commit_segment(USER, pending, _ex(crisis=None))
        assert len(mem_store.list_risk_events(USER)) == 1

    def test_signal_is_protected_from_forgetting(self):
        """signal 与四要素一样受保护:永不归档/删除。"""
        mem_store.reset_store()
        pending = _pending(["不想活了"], risk_levels=["crisis"])
        mem_store.commit_segment(USER, pending, _ex(crisis=None))
        from gzhu_cup2.agent.long_memory import forget
        forget.apply_user_forgetting(USER, now=time.time() + 3650 * 86400)
        assert len(mem_store.list_risk_events(USER)) == 1


# ============================ B-4:静默失败变可见 ============================
class TestSilentFailureSelfCheck:
    def test_warns_when_risk_segment_leaves_no_trace(self, caplog):
        """风险段却什么都没留下 ⇒ 必须告警(此前完全静默,只能人工翻库发现)。"""
        mem_store.reset_store()
        # 风险来自 risk_note,但只有 1 个字 → 达不到 signal 的最小长度,且无分面、无风险轮
        pending = _pending(["嗯"], risk_levels=["low"])
        with caplog.at_level(logging.WARNING):
            mem_store.commit_segment(USER, pending, _ex(crisis=None, risk_note="累"))
        assert mem_store.list_risk_events(USER) == []
        assert any("固化自检" in r.getMessage() for r in caplog.records), "应发出固化自检告警"

    def test_no_warning_on_idempotent_recommit(self, caplog):
        """幂等重提交不产生新键,但也不该误报(这正是自检要区分"首次提交"的原因)。"""
        mem_store.reset_store()
        pending = _pending(["不想活了"], risk_levels=["crisis"])
        mem_store.commit_segment(USER, pending, _ex(crisis=None))
        with caplog.at_level(logging.WARNING):
            mem_store.commit_segment(USER, pending, _ex(crisis=None))
        assert not any("固化自检" in r.getMessage() for r in caplog.records)
