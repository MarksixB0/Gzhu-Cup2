# -*- coding: utf-8 -*-
"""记忆「道」(lanes)纯函数测试:档案 / 状态 / 事件的确定性归类 + 风险内容豁免。

钉住三件事(都是 42 天实测踩出来的):
  1. 分道**按时效性**,不按 category —— 实测库里「有失眠问题」的类别是 health、
     「活着没意思」的类别是 emotion,按类别分道等于没分;
  2. 带实体词的句子(「聊到外婆时忍不住想哭」)不进状态道 —— 它讲的是关于某人的事;
  3. 风险内容可被内容识别(用于读侧豁免),且它**不是** protected/risk 类别。
"""
from gzhu_cup2.agent.long_memory import lanes


class TestClassify:
    def test_event_by_date(self):
        assert lanes.classify({"text": "用户要参加教招笔试", "event_date": "2026-09-10"}) == lanes.LANE_EVENT

    def test_event_by_hint_word(self):
        assert lanes.classify("用户的外婆要做手术") == lanes.LANE_EVENT
        assert lanes.classify("用户下周有公开课") == lanes.LANE_EVENT

    def test_state_by_symptom_word(self):
        assert lanes.classify("用户有失眠问题") == lanes.LANE_STATE
        assert lanes.classify("用户觉得很累，什么都不想动") == lanes.LANE_STATE
        assert lanes.classify("用户被医生诊断为焦虑状态") == lanes.LANE_STATE

    def test_entity_sentence_is_not_state(self):
        """带实体词的句子讲的是"关于某人的事",不该被状态道限流误伤。"""
        assert lanes.classify("用户聊到外婆时忍不住想哭") == lanes.LANE_ARCHIVE
        assert lanes.classify("用户想画一张外婆和芝麻的画") == lanes.LANE_ARCHIVE

    def test_archive_is_default(self):
        assert lanes.classify("用户在广东技术师范大学读学前教育专业") == lanes.LANE_ARCHIVE
        assert lanes.classify("用户有一个弟弟叫沈嘉树") == lanes.LANE_ARCHIVE

    def test_empty_is_archive(self):
        assert lanes.classify("") == lanes.LANE_ARCHIVE
        assert lanes.classify(None) == lanes.LANE_ARCHIVE

    def test_deterministic(self):
        v = "用户最近总是觉得活着没意思"
        assert lanes.classify(v) == lanes.classify(v)


class TestRiskContent:
    def test_risk_markers_detected(self):
        assert lanes.is_risk_content("用户最近总是觉得活着没意思") is True
        assert lanes.is_risk_content("用户明确表示自己不想死") is True
        assert lanes.is_risk_content({"text": "用户说过不想活了"}) is True

    def test_plain_state_is_not_risk(self):
        assert lanes.is_risk_content("用户有失眠问题") is False
        assert lanes.is_risk_content("用户觉得很累") is False


class TestCounts:
    def test_counts_by_lane(self):
        values = ["用户有失眠问题", "用户的外婆要做手术", "用户在读大学"]
        got = lanes.counts(values)
        assert got[lanes.LANE_STATE] == 1
        assert got[lanes.LANE_EVENT] == 1
        assert got[lanes.LANE_ARCHIVE] == 1

    def test_describe(self):
        assert lanes.describe("用户有失眠问题") == "状态"
        assert lanes.describe("用户在读大学") == "档案"
