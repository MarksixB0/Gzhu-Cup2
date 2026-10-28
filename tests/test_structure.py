# -*- coding: utf-8 -*-
"""结构特征模块(否定辖域/主体归属/转述/假设)的规格测试。

标注集 tests/fixtures/structure_corpus.jsonl 是规格本体:每条钉住
一个目标词的四特征判定 + 词表层"是否计入证据"。四类刁难句原型
(否定/他人主体/转述/假设)与直陈正例按同一条流水线过,
正例组的存在就是防过拟合 —— 过滤器不许把直陈的真话也拦掉。
"""
import json
import os

import pytest

from gzhu_cup2.agent.algo.components import structure
from gzhu_cup2.agent.algo.components.lexicon import (
    ANHEDONIA_WORDS, CONFLICT_WORDS, NEG_WORDS, SOMATIC_WORDS,
    WITHDRAW_WORDS, behavior_flags, score_neg,
)
from gzhu_cup2.agent.algo.components.triage import classify, crisis_level

_FIXTURE = os.path.join(os.path.dirname(__file__),
                        "fixtures", "structure_corpus.jsonl")

_FAM_OF_WORD = [(WITHDRAW_WORDS, "withdraw"), (CONFLICT_WORDS, "conflict"),
                (ANHEDONIA_WORDS, "anhedonia"), (SOMATIC_WORDS, "somatic")]


def _corpus():
    with open(_FIXTURE, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_corpus_size_and_shape():
    """标注集规模下限(防悄悄缩水)与必备字段。"""
    rows = _corpus()
    assert len(rows) >= 200
    for r in rows:
        assert r["w"] in r["t"], r
        assert r["g"] in ("neg", "pos", "other", "rep", "hypo", "mix"), r


def test_structural_spec():
    """逐条:目标词的四特征判定 + 词表层是否计入,必须与标注一致。

    失败信息带句子与分组,便于直接定位到标注集行。
    """
    bad = []
    for row in _corpus():
        t, w = row["t"], row["w"]
        tag = "%s[%s]" % (t, row["g"])
        if t.count(w) == 1:
            i = t.index(w)
            f = structure.flags_for(t, i, i + len(w))
            if f["negated"] != bool(row.get("n")):
                bad.append("%s negated=%s" % (tag, f["negated"]))
            if f["other_subject"] != bool(row.get("o")):
                bad.append("%s other=%s" % (tag, f["other_subject"]))
            if f["reported"] != bool(row.get("r")):
                bad.append("%s reported=%s" % (tag, f["reported"]))
            if f["hypothetical"] != bool(row.get("h")):
                bad.append("%s hypo=%s" % (tag, f["hypothetical"]))
            if structure.is_evidence(t, i, i + len(w)) != bool(row["k"]):
                bad.append("%s evidence=%s" % (tag, not row["k"]))
        # 词表层:负面词看 score_neg 的保留命中;行为词看 behavior_flags 的族
        try:
            if w in NEG_WORDS:
                _, hits, _ = score_neg(t)
                if (w in hits) != bool(row["k"]):
                    bad.append("%s lex hits=%s" % (tag, hits))
            for words, fam in _FAM_OF_WORD:
                if w in words:
                    flags = behavior_flags(t)
                    if (fam in flags) != bool(row["k"]):
                        bad.append("%s behavior=%s" % (tag, sorted(flags)))
                    break
        except AssertionError as e:
            bad.append("%s %s" % (tag, e))
    assert not bad, "标注集不一致 %d 条:\n" % len(bad) + "\n".join(bad)


class TestTriageIntegration:
    """危机召回与情绪分类消费同一套结构判定。"""

    def test_negated_crisis_withdrawn(self):
        assert crisis_level("我不想死") is None
        assert crisis_level("我不会自杀的，放心") is None
        assert crisis_level("我没有想过自杀") is None

    def test_crisis_ignores_subject_report_hypothesis(self):
        """主体/转述/假设不拦危机:宁误触 soft 协议,不冒漏报的险。"""
        assert crisis_level("我室友说想死") == "hard"
        assert crisis_level("他说他不想活了") == "hard"
        assert crisis_level("万一我想死了怎么办") == "hard"

    def test_other_subject_not_classified_negative(self):
        assert classify("我室友很难受")["emotion"] == "neutral"
        assert classify("我妈说我看起来很累")["emotion"] == "neutral"

    def test_negation_not_classified_negative(self):
        c = classify("我不难过")
        assert c["emotion"] == "neutral"
        assert c["negative_score"] == pytest.approx(0.30)


class TestPositiveChannel:
    """正面词同样过结构过滤:否定的正面不给抵消(「我一点都不开心」仍是坏信号)。"""

    def test_negated_positive_not_counted(self):
        _, _, pos = score_neg("我一点都不开心")
        assert pos == []

    def test_plain_positive_counted(self):
        _, _, pos = score_neg("我今天很开心")
        assert "开心" in pos


class TestScanMeta:
    """整句级 scan:供 meta 落库与排障面板。"""

    def test_plain_sentence_all_false(self):
        s = structure.scan("今天天气不错，中午吃了面")
        assert not any(s.values())

    def test_help_question_is_not_hypothetical(self):
        """「我该怎么办」是求助,不是假设。"""
        s = structure.scan("我最近很难受，不知道该怎么办")
        assert s["hypothetical"] is False

    def test_reported_sentence_detected(self):
        assert structure.scan("我妈说我看起来很累")["reported"] is True
