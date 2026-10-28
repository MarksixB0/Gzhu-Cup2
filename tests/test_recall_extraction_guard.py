# -*- coding: utf-8 -*-
"""回忆型提问不得被当作新事实抽取(回归:相对日期被重复锚定)。

背景
----
Day1 用户在周日说「下周三有模拟考」→ 锚定成 2026-10-21;Day2 是周一,用户问
「我下周三什么来着?」——同一句话在周一被重算成 2026-10-28,于是库里同时存在
两条矛盾的考试日期。这类"确认已记过的事"不提供新信息,抽取前必须中和掉。

同时守住反面:带日期/更正词的句子**不能**被误判成回忆 —— 否则用户真的改期时,
新信息会被丢掉。
"""
from gzhu_cup2.agent.long_memory import consolidate


class TestRecallQuestionDetector:
    def test_recall_phrases_detected(self):
        for t in (
            "师大，没错。我下周三什么来着？",
            "你还记得我考研考什么吗？",
            "我上次说我目标院校是哪儿来着",
        ):
            assert consolidate._is_recall_question(t) is True, t

    def test_correction_or_new_date_is_not_recall(self):
        # 带数字/更正词 = 可能是更正,不能当纯回忆丢掉
        assert consolidate._is_recall_question("不是21号，改到28号了") is False
        assert consolidate._is_recall_question("我下周三什么来着？是10月28号吗") is False

    def test_normal_assertion_is_not_recall(self):
        assert consolidate._is_recall_question("我下周三有个模拟考，挺紧张的。") is False


class TestFormatPendingTextNeutralizesRecall:
    def _pending(self, user: str, ai: str) -> dict:
        return {"turns": [{
            "seq": 0, "ts": 0.0, "thread": "t",
            "user": user, "ai": ai, "meta": {},
        }]}

    def test_recall_turn_is_neutralized(self):
        """两侧都要中和:助手复述的「28号」也不能再进摘要。"""
        text = consolidate.format_pending_text(
            self._pending("我下周三什么来着？", "你下周三（28号）有模拟考呀。"))

        assert "下周三" not in text
        assert "28号" not in text
        assert "没有提供新信息" in text
        assert "复述了此前记录过的内容" in text

    def test_normal_turn_is_kept(self):
        text = consolidate.format_pending_text(
            self._pending("我下周三有个模拟考，挺紧张的。", "紧张很正常，我们一起捋一捋。"))

        assert "下周三" in text and "模拟考" in text
        assert "紧张很正常" in text
