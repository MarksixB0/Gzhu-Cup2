# -*- coding: utf-8 -*-
"""问题意图(intent)与相关性(relevance)纯函数测试。

为什么这两件事要单测钉死:
  · 读侧"按问题取用"的召回提升(实测 9 题覆盖 5/9 → 9/9)完全建立在这两个函数上,
    它们一旦失真,退化方式是**静默的**——排序照跑,只是答非所问;
  · 主题必须落在 tags.TOPICS_ALLOWED 的闭合空间内(不新造词表,避免两套真相)。
"""
from gzhu_cup2.agent.long_memory import intent as I
from gzhu_cup2.agent.long_memory import relevance as R
from gzhu_cup2.agent.long_memory.tags import TOPICS_ALLOWED


class TestIntent:
    def test_topics_are_closed_vocabulary(self):
        for t in I.TOPIC_TERMS:
            assert t in TOPICS_ALLOWED, "主题 %s 不在闭合词表里" % t

    def test_detect_school_question(self):
        got = I.detect("我是哪个学校的，学什么专业？")
        assert "school" in got["topics"]
        assert "学校" in got["terms"] and "专业" in got["terms"]

    def test_detect_family_question(self):
        got = I.detect("我弟弟今年有什么要紧的事？")
        assert "family" in got["topics"]
        assert "弟弟" in got["terms"]

    def test_detect_health_question(self):
        got = I.detect("我外婆后来身体怎么样了？")
        assert "health" in got["topics"] and "family" in got["topics"]

    def test_date_question_flag(self):
        assert I.detect("我外婆手术是哪天来着？")["date_question"] is True
        assert I.detect("我外婆身体怎么样")["date_question"] is False

    def test_empty_query(self):
        got = I.detect(None)
        assert got == {"topics": [], "terms": [], "date_question": False}
        assert I.detect("   ")["topics"] == []


class TestRelevance:
    def test_empty_query_is_zero(self):
        assert R.score(None, "用户有失眠问题") == 0.0
        assert R.score("  ", "用户有失眠问题") == 0.0

    def test_topic_lexicon_bridges_synonym(self):
        """问"睡眠"要能捞到"失眠" —— 这正是加主题词表的原因(字面零重叠)。"""
        q = "我最近的睡眠情况怎么样"
        it = I.detect(q)
        assert "sleep" in it["topics"]
        assert R.score(q, "用户有失眠问题", intent=it) > R.score(q, "用户养了一只叫芝麻的宠物", intent=it)

    def test_low_base_fact_can_outrank_off_topic(self):
        q = "我是哪个学校的，学什么专业？"
        it = I.detect(q)
        rel_school = R.score(q, "用户在广东技术师范大学读学前教育专业", intent=it)
        rel_pet = R.score(q, "用户养了一只叫芝麻的宠物", intent=it)
        assert rel_school > rel_pet

    def test_irrelevant_fact_scores_low(self):
        q = "我是哪个学校的？"
        assert R.score(q, "用户养了一只叫芝麻的宠物") < 0.25

    def test_date_bonus(self):
        q = "我外婆手术是哪天？"
        with_date = R.score(q, "用户的外婆要做手术", value={"event_date": "2026-09-10"})
        without = R.score(q, "用户的外婆要做手术", value={})
        assert with_date > without

    def test_explain_components(self):
        q = "我弟弟今年有什么要紧的事？"
        e = R.explain(q, "用户的弟弟沈嘉树明年就要高考了")
        assert set(e) >= {"topic", "term", "bigram", "date_bonus", "score"}
        assert 0.0 <= e["score"] <= 1.0
