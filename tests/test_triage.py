# -*- coding: utf-8 -*-
"""前台 triage(快闸分流 + 粗情绪)离线测试:确定性、不触网。"""
import pytest

from gzhu_cup2.agent.algo.components import triage
from gzhu_cup2.agent.algo.components.triage import classify, risk_from
from gzhu_cup2.agent.nodes.triage import fast_triage


class TestClassify:
    def test_neutral_plain(self):
        c = classify("今天天气不错")
        assert c["emotion"] == "neutral"
        assert c["is_user_dangerous"] is False

    def test_weak_single_cue_low_risk(self):
        # 单个弱负面词(累 / 睡不着)→ 低风险,不轻易升档
        assert risk_from(classify("我最近比较累")) == ("low", 0.15)
        assert risk_from(classify("睡不着"))[0] == "low"

    def test_accumulated_sadness_medium(self):
        c = classify("我很难过，想哭，觉得撑不住")
        assert c["emotion"] == "sad"
        risk, _ = risk_from(c)
        assert risk in ("medium", "high")

    def test_crisis_explicit_phrase(self):
        c = classify("我真的不想活了")
        assert c["is_user_dangerous"] is True
        assert risk_from(c)[0] == "crisis"

    def test_casual_death_phrases_not_crisis(self):
        # "累死了 / 笑死了" 不应误触危机快闸
        assert classify("今天累死了")["is_user_dangerous"] is False
        assert classify("这个笑话笑死了")["is_user_dangerous"] is False

    def test_negated_death_phrases_not_crisis(self):
        """明确**否定**死意的话不得被当成危机。

        回归:危机短语是子串匹配,而 "想死" 是 "不想死" 的子串、"自杀" 是
        "不会自杀" 的子串 —— 修前这些句子全部被判 crisis,直接走 handle_crisis
        固定协议甩热线,把一句表达**求生意愿**的话当成危机处理。
        """
        for text in ("我不想死", "我不想死，我还想好好活着", "我不会自杀的",
                     "我没想过自杀", "我不想做傻事", "我不敢死"):
            c = classify(text)
            assert c["is_user_dangerous"] is False, f"误报: {text}"
            assert risk_from(c)[0] != "crisis", f"误报: {text}"

    def test_real_crisis_still_detected(self):
        """否定守卫不得削弱真危机检出(含"不想活"这类不含否定线索的真短语)。"""
        for text in ("我想死", "我真的不想活了", "活着没意思", "我想自杀",
                     "我要结束生命", "我活不下去"):
            c = classify(text)
            assert c["is_user_dangerous"] is True, f"漏报: {text}"
            assert risk_from(c)[0] == "crisis", f"漏报: {text}"

    def test_hedged_ideation_still_crisis(self):
        """带转折/条件的高危表达仍按危机处理 —— 安全优先,不因守卫而漏。"""
        for text in ("我想死，但是不敢", "我想死，可是放不下我妈",
                     "有时候真想去死"):
            assert classify(text)["is_user_dangerous"] is True, f"漏报: {text}"

    def test_third_person_crisis_still_escalates(self):
        """第三人称求助保持危机处理(刻意保守:宁可多给一次热线)。

        真实场景里"我同学说他想死"是**替他人求助**,更该给资源;
        本守卫因此只作用于第一人称语境,不因"不是我"而降级。
        """
        assert classify("我同学说他想死")["is_user_dangerous"] is True

    def test_happy_text_kept_low(self):
        c = classify("好开心，今天考完了")
        assert c["emotion"] in ("happy", "neutral")
        assert risk_from(c)[0] == "low"


class TestAnhedoniaVocabulary:
    """快感缺失/乏力换词表达必须命中真实负向分,而不是漏成 neutral 0.30。

    回归:"隐晦持续低落"型人格不说"难过/焦虑",而说「提不起劲」「被抽空了」
    「做什么都没意思」—— 此前这些词不在情绪短语表里,negative_score 恒为
    0.30,情绪通道整体失灵(实测漏报)。recall-first:此类词收录进 frustrated 族。
    """

    @pytest.mark.parametrize("text", [
        "提不起劲", "今天没精神", "没力气", "做什么都没意思", "不想动",
        "不想说话", "不想见人", "像被抽空了", "心里空空的", "坐着发呆",
        "浑身乏力", "有点嗜睡",
    ])
    def test_anhedonia_words_score_genuine_negative(self, text):
        c = classify(text)
        assert c["emotion"] == "frustrated", (text, c)
        assert c["negative_score"] >= 0.45, (text, c["negative_score"])

    def test_anhedonia_words_reach_lexicon_too(self):
        """词表旁路通道同样认识这些词(两通道互证才有意义)。"""
        from gzhu_cup2.agent.algo.components.lexicon import score_neg
        for word in ("没精神", "没力气", "不想动", "被抽空", "心里空空的",
                     "发呆", "乏力", "嗜睡"):
            score, hits, _ = score_neg(word)
            assert word in hits, word
            assert score >= 0.39, (word, score)

    def test_plain_neutral_still_neutral(self):
        assert classify("今天天气不错")["negative_score"] == 0.30


class TestNodeRouting:
    def test_crisis_routes_to_handle_crisis(self):
        from langgraph.types import Command
        out = fast_triage({"process_input": "我想去死，感觉没有出路"})
        assert isinstance(out, Command)
        assert out.goto == "handle_crisis"
        assert (out.update or {}).get("risk_level") == "crisis"

    def test_normal_routes_to_retrieve_memory(self):
        from langgraph.types import Command
        out = fast_triage({"process_input": "今天和朋友去公园走了走"})
        assert isinstance(out, Command)
        assert out.goto == "retrieve_memory"
        assert (out.update or {}).get("risk_level") == "low"


class TestSemanticFrames:
    """语义框架(一条规则覆盖一族表达)的正反例。

    反例同时是"词表不补丁"的边界:框架接不住的变体(情绪词衬字、转述插副词)
    属于内容层职责,这里锁定框架自身的拦截面。
    """

    def test_means_frame_word_order_is_hard(self):
        """手段宾语前置与常规语序同判级。"""
        for text in ("刀都准备好了", "绳子我已经买好了", "遗书我都写好了",
                     "炭都备好了"):
            assert triage.crisis_level(text) == "hard", text

    def test_means_frame_negative_cases(self):
        """无手段名词 / 无准备动词 / 无关名词不触发。"""
        for text in ("我准备好了", "感冒药都买好了", "作业都写好了"):
            assert triage.crisis_level(text) is None, text

    def test_despair_frame_insertion(self):
        """「看不到…希望」的插字变体按绝望计。"""
        c = triage.classify("看不到任何希望")
        assert c["despair"] is True
        assert triage.risk_from(c)[0] == "high"
        assert triage.classify("看不到希望")["despair"] is True

    def test_despair_frame_negation_blocked(self):
        assert triage.classify("不是看不到希望，是我不想认输")["despair"] is False

    def test_hypothetical_death_is_soft(self):
        """假设性死亡意念走 soft 警铃(开确认窗,不直接硬协议)。"""
        for text in ("假如我死了，你会难过吗", "要是哪天我不在了，你要记得想我",
                     "万一哪天真没了呢"):
            c = triage.classify(text)
            assert c["crisis_level"] == "soft", text
            assert triage.risk_from(c)[0] == "crisis"

    def test_soft_frame_negative_cases(self):
        """无假设前缀的「死了/没了」不成框(「我死了这条心」是放弃义)。"""
        for text in ("我死了这条心", "作业没了", "他要是来了就好了"):
            c = triage.classify(text)
            assert c["crisis_level"] is None, text

    def test_squeeze_dual_path_keeps_intensifier_phrases(self):
        """短语自带程度副词(活着太累)在剥词后仍由原文兜住。"""
        c = triage.classify("活着太累了")
        assert c["despair"] is True
        assert triage.risk_from(c)[0] == "high"
