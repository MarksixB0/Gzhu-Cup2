# -*- coding: utf-8 -*-
"""读侧相关性:给「用户这句话」与「一条记忆」打一个 0-1 的相关分(纯函数)。

三个分量(全部确定性、可逐项解释,见 `explain`):

  1. `topic`(权重最高)—— 问题命中某个主题(见 `intent.TOPIC_TERMS`)后,
     该主题的整套词表都拿来与事实比对。
  2. `term` —— 问题里实际出现的那个词是否出现在事实里。
  3. `bigram` —— 问题内容 bigram 被事实覆盖的比例,只统计两端都是实字的 bigram。

问时间/日期时(intent.date_question)给带 event_date 的事实额外权重。

无 query、或三个分量全 0 → 返回 0.0,调用方据此退回静态排序。
"""
from typing import Optional

from gzhu_cup2.agent.long_memory import intent as intent_mod

# 只保留"两端都是实字"的 bigram:虚词字参与的 bigram 几乎不含信息
_FILLER_CHARS = frozenset(
    "我你他她它的了是在有和与就都也很还又而但却并把被给对从向往之其这那些个只"
    "吗呢吧呀啊哦嘛来着上下前后中会要想能可没不无"
)
_PUNCT = frozenset("，。！？、；：“”‘’（）《》〈〉【】…—～·,.!?;:\"'()[]{}<>-_/\\|@#$%^&*+=~` ")

# 分量权重(和为 1.0);改这里必须同时改 tests/test_relevance.py 的期望区间
W_TOPIC = 0.5
W_TERM = 0.3
W_BIGRAM = 0.2
DATE_BONUS = 0.15


def _clean(text) -> str:
    return "".join(ch for ch in str(text or "") if ch not in _PUNCT)


def content_bigrams(text) -> set:
    """内容 bigram 集合(两端都必须是实字)。"""
    s = _clean(text)
    return {s[i:i + 2] for i in range(len(s) - 1)
            if s[i] not in _FILLER_CHARS and s[i + 1] not in _FILLER_CHARS}


def _topic_component(text: str, intent: Optional[dict]) -> float:
    """问题命中的主题里,有几个能在事实文本上找到词表命中(比例)。"""
    topics = list((intent or {}).get("topics") or [])
    if not topics or not text:
        return 0.0
    hit = 0
    for t in topics:
        words = intent_mod.TOPIC_TERMS.get(t) or ()
        if any(w in text for w in words):
            hit += 1
    return hit / len(topics)


def _term_component(text: str, intent: Optional[dict]) -> float:
    """问题里实际出现的词,是否出现在事实文本里(命中即 1)。"""
    terms = list((intent or {}).get("terms") or [])
    if not terms or not text:
        return 0.0
    return 1.0 if any(t in text for t in terms) else 0.0


def _bigram_component(query: str, text: str) -> float:
    """问题内容 bigram 被事实覆盖的比例(事实更长不吃亏,故用覆盖率而非 Jaccard)。"""
    q = content_bigrams(query)
    if not q or not text:
        return 0.0
    t = content_bigrams(text)
    return len(q & t) / len(q)


def explain(query: Optional[str], text, intent: Optional[dict] = None,
            value: Optional[dict] = None) -> dict:
    """返回各分量明细(洞察面板 / 排障 / 单测用)。"""
    q = str(query or "")
    t = str(text or "")
    it = intent if intent is not None else (intent_mod.detect(q) if q else {})
    topic = _topic_component(t, it)
    term = _term_component(t, it)
    big = _bigram_component(q, t)
    base = W_TOPIC * topic + W_TERM * term + W_BIGRAM * big
    bonus = 0.0
    if it.get("date_question") and isinstance(value, dict) and value.get("event_date"):
        bonus = DATE_BONUS
    return {
        "topic": round(topic, 4), "term": round(term, 4), "bigram": round(big, 4),
        "date_bonus": bonus, "score": round(min(1.0, base + bonus), 4),
    }


def score(query: Optional[str], text, intent: Optional[dict] = None,
          value: Optional[dict] = None) -> float:
    """问题 × 事实 的相关性(0-1)。无 query 时恒为 0.0。"""
    if not str(query or "").strip():
        return 0.0
    return explain(query, text, intent=intent, value=value)["score"]
