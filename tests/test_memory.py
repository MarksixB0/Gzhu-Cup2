# -*- coding: utf-8 -*-
"""长期记忆离线测试(不触网;LLM 用假引擎)。

覆盖:捕获追加 / 到期判定 / 记忆图固化(事实合并去重、摘要落库、pending 推进)/
读取格式化 / 人脑式遗忘(归档、清理、受保护集)。
"""
import time
from datetime import date, datetime

import pytest

from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import forget
from gzhu_cup2.agent.long_memory.retrieve import build_memory_context
from gzhu_cup2.agent.core.schemas import MemoryExtraction, MemoryFact

USER = "u-test"


# ============================ 假 LLM ============================
class _Handler:
    def __init__(self, builder):
        self._builder = builder

    def invoke(self, messages, **kwargs):
        return self._builder(messages, **kwargs)


class FakeMemoryLLM:
    """只支持 with_structured_output(MemoryExtraction).invoke(...)。"""

    def __init__(self, digest="聊了家里养的猫", facts=None, importance=0.6, risk_note=""):
        self.digest = digest
        self.facts = facts or [MemoryFact(text="用户养了一只叫年糕的猫", category="preference", importance=0.7)]
        self.importance = importance
        self.risk_note = risk_note

    def with_structured_output(self, schema, **kwargs):
        def build(_m):
            return MemoryExtraction(
                digest=self.digest, facts=list(self.facts),
                importance=self.importance, risk_note=self.risk_note,
            )
        return _Handler(build)

    def invoke(self, messages, **kwargs):
        raise AssertionError("记忆阶段不应走普通 invoke")


@pytest.fixture
def fake_memory_llm(monkeypatch):
    llm = FakeMemoryLLM()
    from gzhu_cup2.agent.long_memory import consolidate as cons
    monkeypatch.setattr(cons, "get_llm", lambda *a, **k: llm)
    return llm


# ============================ 捕获 / 到期 ============================
class TestCaptureAndDue:
    def test_capture_appends_turns(self):
        mem_store.capture_turn(USER, "t1", "你好", "你好呀", meta={"risk_level": "low"})
        mem_store.capture_turn(USER, "t1", "我最近睡不好", "辛苦了", meta={"risk_level": "low"})
        pending = mem_store.get_pending(USER)
        assert pending is not None
        assert len(pending["turns"]) == 2
        assert pending["turns"][0]["user"] == "你好"
        assert pending["turns"][1]["meta"]["risk_level"] == "low"
        assert pending["threads"] == ["t1"]

    def test_due_by_turn_count(self):
        # 纯函数判定:轮数达标即到期
        now = time.time()
        pending = {"turns": [{"seq": i} for i in range(5)], "last_ts": now, "threads": ["t"]}
        assert mem_store.pending_is_due(pending, now) is True
        pending2 = {"turns": [{"seq": 0}], "last_ts": now, "threads": ["t"]}
        assert mem_store.pending_is_due(pending2, now) is False

    def test_due_by_idle_and_cross_thread(self):
        now = time.time()
        old = now - 10000  # 远超空闲阈值(300s)
        assert mem_store.pending_is_due(
            {"turns": [{"seq": 0}], "last_ts": old, "threads": ["t"]}, now) is True
        assert mem_store.pending_is_due(
            {"turns": [{"seq": 0}], "last_ts": now, "threads": ["t1", "t2"]}, now) is True


# ============================ 固化(记忆图) ============================
class TestConsolidation:
    def _capture_two(self):
        mem_store.reset_store()
        mem_store.capture_turn(USER, "t1", "我家猫叫年糕", "好可爱", meta={"risk_level": "low"})
        mem_store.capture_turn(USER, "t1", "它最近生病了", "别担心", meta={"risk_level": "low"})

    def _run_memory_graph(self, user):
        from gzhu_cup2.agent.graphs.memory_graph import build_memory_graph
        app = build_memory_graph()
        pending = mem_store.get_pending(user)
        app.invoke({"user_id": user, "pending": pending})

    def test_consolidate_writes_facts_and_episode(self, fake_memory_llm):
        self._capture_two()
        self._run_memory_graph(USER)
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        episodes = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))]
        assert len(facts) == 1 and "年糕" in facts[0]["text"]
        assert facts[0]["mentions"] == 1
        assert len(episodes) == 1 and episodes[0]["digest"]
        assert mem_store.get_pending(USER) is None  # pending 已清空

    def test_same_fact_merges_not_duplicates(self, fake_memory_llm):
        self._capture_two()
        self._run_memory_graph(USER)
        # 第二段对话再次出现同一事实
        mem_store.capture_turn(USER, "t2", "猫今天好多了", "太好了", meta={"risk_level": "low"})
        self._run_memory_graph(USER)
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        episodes = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))]
        assert len(facts) == 1            # 仍是同一条
        assert facts[0]["mentions"] == 2  # 提及次数累加
        assert len(episodes) == 2         # 两段对话各一条摘要

    def test_new_turns_during_consolidation_survive(self, fake_memory_llm):
        self._capture_two()
        snapshot = mem_store.get_pending(USER)  # 固化期间的"快照"
        # 固化进行中又来了新线程的新一轮
        mem_store.capture_turn(USER, "t2", "我下周要答辩", "加油", meta={"risk_level": "low"})
        from gzhu_cup2.agent.long_memory import consolidate as cons
        extraction = cons.extract_memory(snapshot)
        mem_store.commit_segment(USER, snapshot, extraction)
        pending = mem_store.get_pending(USER)
        assert pending is not None
        assert [t["user"] for t in pending["turns"]] == ["我下周要答辩"]

    def test_risk_segment_is_protected_episode(self, fake_memory_llm):
        mem_store.reset_store()
        mem_store.capture_turn(USER, "t1", "我想结束生命", "请拨打12356", meta={"risk_level": "crisis"})
        fake_memory_llm.risk_note = "用户表达自杀意念"
        self._run_memory_graph(USER)
        episodes = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))]
        assert episodes and episodes[0]["protected"] is True
        assert episodes[0]["category"] == "risk"

    def test_consolidation_resolves_event_date_from_turn_ts(self, fake_memory_llm):
        """「用户明天要考试」+ 锚定 ts → 落库 text 已绝对化,且带 event_date。"""
        mem_store.reset_store()
        anchor = datetime(2026, 9, 9, 10, 0)
        snapshot = {
            "turns": [{"seq": 0, "ts": anchor.timestamp(), "thread": "t1",
                       "user": "我明天要考试", "ai": "加油",
                       "meta": {"risk_level": "low"}}],
            "next_seq": 1, "started_at": anchor.timestamp(),
            "last_ts": anchor.timestamp(), "threads": ["t1"],
        }
        fake_memory_llm.facts = [MemoryFact(
            text="用户明天要考试", category="plan", importance=0.8)]
        from gzhu_cup2.agent.long_memory import consolidate as cons
        extraction = cons.extract_memory(snapshot)
        mem_store.commit_segment(USER, snapshot, extraction)
        facts = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))]
        assert any(f.get("text") == "用户在2026-09-10要考试"
                   and f.get("event_date") == "2026-09-10" for f in facts)


# ============================ 读取 ============================
class TestRetrieve:
    def test_memory_context_includes_facts_and_episodes(self, fake_memory_llm):
        mem_store.reset_store()
        mem_store.capture_turn(USER, "t1", "我家猫叫年糕", "好可爱", meta={"risk_level": "low"})
        from gzhu_cup2.agent.graphs.memory_graph import build_memory_graph
        build_memory_graph().invoke({"user_id": USER, "pending": mem_store.get_pending(USER)})
        ctx = build_memory_context(USER)
        assert "年糕" in ctx
        assert "用户档案" in ctx

    def test_empty_user_returns_blank(self):
        assert build_memory_context("no-such-user") == ""

    def _put_fact_with_event_date(self, event_date):
        now = time.time()
        mem_store.put_item(mem_store.ns_facts(USER), "k-ev", {
            "type": "fact", "text": "用户明天要考试", "category": "plan",
            "importance": 0.8, "mentions": 1,
            "first_seen": now, "last_seen": now,
            "archived": False, "protected": False,
            "event_date": event_date,
        })

    def test_memory_context_renders_event_date(self):
        mem_store.reset_store()
        self._put_fact_with_event_date("2026-09-10")
        ctx = build_memory_context(USER)
        assert "事件日期：2026-09-10" in ctx

    def test_memory_context_marks_past_event_date(self):
        mem_store.reset_store()
        self._put_fact_with_event_date("2020-01-01")
        ctx = build_memory_context(USER)
        assert "（已过去）" in ctx

    def test_memory_context_legacy_fact_without_event_date_unchanged(self):
        # 老数据没有 event_date 键 → 渲染必须与之前完全一致(不出现日期后缀)
        mem_store.reset_store()
        now = time.time()
        mem_store.put_item(mem_store.ns_facts(USER), "k-legacy", {
            "type": "fact", "text": "用户养了一只猫", "category": "preference",
            "importance": 0.7, "mentions": 1,
            "first_seen": now, "last_seen": now,
            "archived": False, "protected": False,
        })
        ctx = build_memory_context(USER)
        assert "- 用户养了一只猫" in ctx
        assert "事件日期" not in ctx

    def test_memory_context_absolutizes_legacy_episode_digest(self):
        """存量摘要里的相对时间词必须在**读侧**被绝对化。

        回归:绝对化上线前落库的 digest 原样带「明天」,写侧修复救不了存量,
        故 `_episode_line` 用段首 ts_start 再绝对化一次(与写侧同锚点)。
        """
        mem_store.reset_store()
        anchor = datetime(2026, 9, 9, 20, 0).timestamp()
        mem_store.put_item(mem_store.ns_episodes(USER), "k-ep-legacy", {
            "type": "episode", "digest": "用户说明天要考试。",
            "importance": 0.6, "risk_note": "", "category": "episode",
            "protected": False, "ts_start": anchor, "ts_end": anchor,
            "turn_count": 1, "threads": ["t"],
            "first_seen": anchor, "last_seen": anchor, "archived": False,
        })
        ctx = build_memory_context(USER)
        assert "明天" not in ctx, "摘要里的相对时间词没被换算 → 模型会复述成「明天」"
        assert "2026-09-10" in ctx


# ============================ 跨天「什么时候考试」端到端 ============================
class TestExamDateAcrossDays:
    """day1 说「明天要考试」→ 固化 → day2 读侧必须给出**绝对日期**。

    这是用户报的原始症状:day2 问「我什么时候考试」,模型仍答「明天」。
    覆盖两处:事实(写侧已绝对化)与 **摘要 digest**(此前原样带相对词、原样读回)。
    """

    DAY1 = datetime(2026, 9, 9, 21, 0)     # 说「明天考试」的那天
    DAY2 = datetime(2026, 9, 10, 23, 30)   # 次日深夜来问

    def _consolidate_day1(self, fake_memory_llm):
        snapshot = {
            "turns": [{"seq": 0, "ts": self.DAY1.timestamp(), "thread": "t1",
                       "user": "明天我要考试", "ai": "嗯,我记着",
                       "meta": {"risk_level": "low"}}],
            "next_seq": 1, "started_at": self.DAY1.timestamp(),
            "last_ts": self.DAY1.timestamp(), "threads": ["t1"],
        }
        # LLM 抽出来的事实与摘要都**原样**带着「明天」—— 绝对化必须由 Python 完成
        fake_memory_llm.digest = "用户说明天要考试,有点紧张。"
        fake_memory_llm.facts = [MemoryFact(
            text="用户明天要考试", category="plan", importance=0.8)]
        from gzhu_cup2.agent.long_memory import consolidate as cons
        mem_store.commit_segment(USER, snapshot, cons.extract_memory(snapshot))

    def test_fact_and_digest_carry_absolute_date(self, fake_memory_llm):
        mem_store.reset_store()
        self._consolidate_day1(fake_memory_llm)
        fact = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))][0]
        assert fact["text"] == "用户在2026-09-10要考试"
        assert fact["event_date"] == "2026-09-10"
        ep = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))][0]
        assert "明天" not in ep["digest"], "摘要仍带相对词 → 会被原样读回提示词"
        assert "2026-09-10" in ep["digest"]

    def test_day2_rendering_says_today_not_tomorrow(self, fake_memory_llm):
        """day2 深夜来问时,读侧必须渲染成「今天」,而不是复述「明天」。"""
        from gzhu_cup2.agent.long_memory import retrieve as ret
        mem_store.reset_store()
        self._consolidate_day1(fake_memory_llm)
        today = self.DAY2.date()

        fact = [it.value for it in mem_store.list_items(mem_store.ns_facts(USER))][0]
        line = ret._fact_line(fact, today, False)
        assert "2026-09-10" in line
        assert "（今天）" in line, "day2 看这条事必须显示「今天」"

        ep = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))][0]
        eline = ret._episode_line(ep, today, self.DAY2.timestamp())
        assert "明天" not in eline
        assert "2026-09-10" in eline


# ============================ 旧数据读侧锚点 ============================
class TestLegacyReadAnchor:
    """旧事实(无 event_date)读侧绝对化:锚点必须是 first_seen。

    相对时间词是**首次说出口**那天的视角;last_seen 会被每次复述刷新,
    拿它当锚点会让「下周三」越读越远(11-04 的事被读成 11-25)。
    """

    FIRST = datetime(2026, 10, 28, 16, 0)    # 首次说「下周三」那天(周三)
    LATER = datetime(2026, 11, 20, 20, 0)    # 后来又被提过一次

    def _legacy(self, text="用户下周三有考试"):
        return {"type": "fact", "text": text, "category": "plan",
                "first_seen": self.FIRST.timestamp(), "last_seen": self.LATER.timestamp()}

    def test_anchor_is_first_seen_not_last_seen(self):
        from gzhu_cup2.agent.long_memory import retrieve as ret
        line = ret._fact_line(self._legacy(), self.FIRST.date(), False)
        assert "2026-11-04" in line, line
        assert "11-25" not in line, line

    def test_repeated_mention_does_not_drift(self):
        from gzhu_cup2.agent.long_memory import retrieve as ret
        v = self._legacy()
        before = ret._fact_line(v, self.FIRST.date(), False)
        v["last_seen"] = (self.LATER + (self.LATER - self.FIRST)).timestamp()   # 再提一次
        assert ret._fact_line(v, self.FIRST.date(), False) == before

    def test_text_without_relative_word_is_untouched(self):
        from gzhu_cup2.agent.long_memory import retrieve as ret
        line = ret._fact_line(self._legacy("用户喜欢跑步"), self.FIRST.date(), False)
        assert line == "- 用户喜欢跑步"


class TestRangeEventHint:
    """整周/整月事实的相对提示:区间没过完不能说「已过去」。"""

    def _fact(self, text, event_date):
        return {"type": "fact", "text": text, "category": "plan",
                "event_date": event_date, "first_seen": 1.0, "last_seen": 1.0}

    def test_week_fact_in_progress(self):
        from gzhu_cup2.agent.long_memory import retrieve as ret
        v = self._fact("用户在2026-11-01那一周有考试", "2026-11-01")
        assert "（本周内）" in ret._fact_line(v, date(2026, 11, 6), False)
        assert "（已过去）" in ret._fact_line(v, date(2026, 11, 9), False)

    def test_month_fact_in_progress(self):
        from gzhu_cup2.agent.long_memory import retrieve as ret
        v = self._fact("用户在2026-11月要搬家", "2026-11-01")
        assert "（本月内）" in ret._fact_line(v, date(2026, 11, 20), False)

    def test_day_fact_keeps_relative_hint(self):
        from gzhu_cup2.agent.long_memory import retrieve as ret
        v = self._fact("用户在2026-11-04有考试", "2026-11-04")
        assert "（3 天后）" in ret._fact_line(v, date(2026, 11, 1), False)


# ============================ 遗忘 ============================
class TestForgetting:
    def _put_old_low_fact(self, importance=0.1):
        now = time.time()
        old = now - 100 * 86400  # 100 天前
        mem_store.put_item(mem_store.ns_facts(USER), "k-low", {
            "type": "fact", "text": "一条陈旧低价值事实", "category": "other",
            "importance": importance, "mentions": 1,
            "first_seen": old, "last_seen": old,
            "archived": False, "protected": False,
        })

    def test_archive_when_old_and_low_value(self):
        """陈旧 + 低价值 → 软归档(可恢复);**默认不再物理删除**。

        ⚠ 2026-09-13 口径变更:物理删除默认关闭(cfg.MEMORY_FORGET_PURGE_ENABLED=False),
        因为实测中它会删掉用户的名字(imp=0.3 的事实,第 37 天被清除),而归档可被
        "重新提及即解除"。本用例锁住"只归档、不删除"这条新契约。
        """
        mem_store.reset_store()
        self._put_old_low_fact()
        assert forget.forgetting_decision(
            mem_store.get_item(mem_store.ns_facts(USER), "k-low")) == "archive"
        forget.apply_user_forgetting(USER)
        val = mem_store.get_item(mem_store.ns_facts(USER), "k-low")
        assert val and val["archived"] is True
        # 归档早已超过观察期 —— 但默认不删除:条目必须还在,且判定仍为 keep
        archived = dict(val)
        archived["archived_at"] = time.time() - (14 + 1) * 86400
        mem_store.put_item(mem_store.ns_facts(USER), "k-low", archived)
        assert forget.forgetting_decision(archived) == "keep"
        forget.apply_user_forgetting(USER)
        assert mem_store.get_item(mem_store.ns_facts(USER), "k-low") is not None

    def test_purge_still_available_when_explicitly_enabled(self, monkeypatch):
        """purge 只是退出默认路径,不是被移除:显式开启后仍按观察期清理。"""
        mem_store.reset_store()
        monkeypatch.setattr(forget.cfg, "MEMORY_FORGET_PURGE_ENABLED", True, raising=False)
        now = time.time()
        archived = {"type": "fact", "text": "陈旧低价值", "category": "other",
                    "importance": 0.05, "mentions": 1,
                    "first_seen": now - 200 * 86400, "last_seen": now - 200 * 86400,
                    "archived": True,
                    "archived_at": now - (forget.cfg.MEMORY_ARCHIVE_GRACE_DAYS + 1) * 86400}
        assert forget.forgetting_decision(archived) == "purge"
        mem_store.put_item(mem_store.ns_facts(USER), "k-purge", archived)
        forget.apply_user_forgetting(USER)
        assert mem_store.get_item(mem_store.ns_facts(USER), "k-purge") is None

    def test_pinned_importance_never_archived(self):
        """免衰减:importance ≥ MEMORY_FORGET_PINNED_IMPORTANCE 的条目永不自动归档。

        构造上贴近真实翻车案例 —— 「用户叫林知远」这类事实只被提过一次、分数会随时间衰减。
        """
        mem_store.reset_store()
        now = time.time()
        old = now - 400 * 86400
        mem_store.put_item(mem_store.ns_facts(USER), "k-name", {
            "type": "fact", "text": "用户叫林知远", "category": "other",
            "importance": forget.cfg.MEMORY_FORGET_PINNED_IMPORTANCE, "mentions": 1,
            "first_seen": old, "last_seen": old,
            "archived": False, "protected": False,
        })
        assert forget.is_pinned(mem_store.get_item(mem_store.ns_facts(USER), "k-name"))
        assert forget.forgetting_decision(
            mem_store.get_item(mem_store.ns_facts(USER), "k-name")) == "keep"
        forget.apply_user_forgetting(USER)
        val = mem_store.get_item(mem_store.ns_facts(USER), "k-name")
        assert val is not None and val["archived"] is not True

    def test_pinned_does_not_change_can_initiate(self):
        """免衰减(pinned)与"可否主动提起"(protected)必须解耦。

        is_protected 同时被 forgetting_decision 与 retrieve.can_initiate 使用;
        若把"免遗忘"直接做成 protected,pinned 的条目会被顺带踢出主动关怀白名单。
        """
        from gzhu_cup2.agent.long_memory.retrieve import can_initiate
        now = time.time()
        v = {"type": "fact", "text": "用户喜欢打羽毛球", "category": "preference",
             "importance": 0.8, "mentions": 1, "first_seen": now, "last_seen": now,
             "archived": False, "protected": False}
        assert forget.is_pinned(v) is True          # 免衰减
        assert forget.is_protected(v) is False      # 但不是受保护集
        assert can_initiate(v, now) is True         # 仍可被主动提起

    def test_protected_never_archived(self):
        mem_store.reset_store()
        now = time.time()
        old = now - 300 * 86400
        mem_store.put_item(mem_store.ns_episodes(USER), "k-risk", {
            "type": "episode", "digest": "危机记录", "category": "risk",
            "protected": True, "importance": 0.05, "mentions": 1,
            "first_seen": old, "last_seen": old, "archived": False,
        })
        assert forget.forgetting_decision(
            mem_store.get_item(mem_store.ns_episodes(USER), "k-risk")) == "keep"
        forget.apply_user_forgetting(USER)
        val = mem_store.get_item(mem_store.ns_episodes(USER), "k-risk")
        assert val is not None and val["archived"] is not True

    def test_high_value_old_memory_kept(self):
        mem_store.reset_store()
        now = time.time()
        old = now - 60 * 86400
        mem_store.put_item(mem_store.ns_facts(USER), "k-high", {
            "type": "fact", "text": "重要事实", "category": "plan",
            "importance": 0.9, "mentions": 5,
            "first_seen": old, "last_seen": old,
            "archived": False, "protected": False,
        })
        assert forget.forgetting_decision(
            mem_store.get_item(mem_store.ns_facts(USER), "k-high")) == "keep"


# ============================ API 冒烟(捕获不破坏主流程) ============================
class TestApiSmoke:
    def test_chat_captures_pending(self, api_client):
        r1 = api_client.post("/api/chat", json={"message": "第一句话"})
        assert r1.status_code == 200, r1.text
        r2 = api_client.post("/api/chat", json={
            "message": "第二句话", "thread_id": r1.json()["thread_id"],
        })
        assert r2.status_code == 200, r2.text
        pending = mem_store.get_pending("local_user")
        assert pending is not None and len(pending["turns"]) == 2
