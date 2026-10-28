# -*- coding: utf-8 -*-
"""时序层(ticks 日桶)、近况摘要(profile)、可主动提及白名单 的离线测试。

依赖 conftest 的 autouse 夹具:每用例重置内存 Store,互不污染。
"""
import time

from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import ticks
from gzhu_cup2.agent.long_memory import trend as mtrend
from gzhu_cup2.agent.long_memory import profile as mprofile
from gzhu_cup2.agent.long_memory import retrieve
from gzhu_cup2.agent.long_memory import forget

USER = "u-ticks"


def _turn(risk="low", emotion="sad", neg=0.6, intensity=0.7, phq=6, gad=4):
    mem_store.capture_turn(USER, "t", "测试内容", "测试回复", meta={
        "risk_level": risk, "emotion": emotion, "negative_score": neg,
        "intensity": intensity, "phq9_total": phq, "gad7_total": gad,
    })


# ============================== 日桶聚合 ==============================
class TestTicks:
    def test_capture_bumps_single_day_bucket(self):
        _turn()
        _turn()
        _turn()
        buckets = ticks.list_day_buckets(USER)
        assert len(buckets) == 1
        _, b = buckets[0]
        assert b["count"] == 3
        assert b["neg_n"] == 3
        assert abs(b["neg_sum"] - 1.8) < 1e-6
        assert b["emotions"].get("sad") == 3
        assert b.get("risk_max_rank", 0) == 0  # low 不升保护
        assert not b.get("protected", False)

    def test_missing_fields_not_counted(self):
        # 只带 risk_level 的裸 meta(历史调用方风格)也应安全聚合
        mem_store.capture_turn(USER, "t", "内容", "回复", meta={"risk_level": "low"})
        _, b = ticks.list_day_buckets(USER)[0]
        assert b["count"] == 1
        assert "neg_n" not in b and "inten_n" not in b

    def test_crisis_turn_excluded_from_emotion_aggregation(self):
        # 危机与情绪解耦(MEMORY_REDESIGN §2/§10):危机轮的 neg/lex 不进聚合,
        # 但计数、保护标记、intensity 等其余字段照常。
        mem_store.capture_turn(USER, "t", "我想结束生命", "请拨打 12356", meta={
            "risk_level": "crisis", "emotion": "fearful", "negative_score": 0.95,
            "intensity": 0.9, "lex_neg": 0.84,
        })
        _, b = ticks.list_day_buckets(USER)[0]
        assert b["count"] == 1
        assert b["risk_max_rank"] == 3
        assert b.get("protected") is True
        assert b["inten_n"] == 1                                  # intensity 照常
        assert "neg_n" not in b and "neg_sum" not in b            # 情绪分不聚合
        assert "lex_neg_n" not in b and "lex_neg_sum" not in b    # 词表不聚合

    def test_normal_turn_still_aggregates_neg_and_lex(self):
        # 对照组:非危机轮(含 high)不受解耦影响,neg/lex 照常聚合
        mem_store.capture_turn(USER, "t", "今天有点难过", "我在听", meta={
            "risk_level": "high", "emotion": "sad", "negative_score": 0.6,
            "intensity": 0.5, "lex_neg": 0.45,
        })
        _, b = ticks.list_day_buckets(USER)[0]
        assert b["count"] == 1
        assert b["neg_n"] == 1 and abs(b["neg_sum"] - 0.6) < 1e-6
        assert b["lex_neg_n"] == 1 and abs(b["lex_neg_sum"] - 0.45) < 1e-6
        assert b["risk_max_rank"] == 2 and b.get("protected") is True

    def test_crisis_and_normal_turn_same_day(self):
        # 同日混合:只有普通轮的 neg 进桶,危机轮只贡献计数
        _turn(risk="low", neg=0.4)
        _turn(risk="crisis", neg=0.95)
        _, b = ticks.list_day_buckets(USER)[0]
        assert b["count"] == 2
        assert b["neg_n"] == 1
        assert abs(b["neg_sum"] - 0.4) < 1e-6

    def test_high_risk_day_protected(self):
        _turn(risk="high", emotion="sad", neg=0.9)
        _, b = ticks.list_day_buckets(USER)[0]
        assert b["risk_max_rank"] >= 2
        assert b.get("protected", False) is True

    def test_prune_removes_old_keeps_protected(self):
        now = time.time()
        mem_store.put_item(ticks.ns_ticks(USER), "2020-01-01",
                           {"day": "2020-01-01", "count": 1, "last_ts": 1.0})
        mem_store.put_item(ticks.ns_ticks(USER), "2020-01-02",
                           {"day": "2020-01-02", "count": 1, "last_ts": 1.0, "protected": True})
        stats = ticks.prune_expired(USER, now=now)
        assert stats["checked"] == 2
        assert stats["pruned"] == 1
        keys = [k for k, _ in ticks.list_day_buckets(USER, days=None)]
        assert keys == ["2020-01-02"]  # 保护桶保留


# ============================== 近况摘要 ==============================
class TestProfile:
    def test_empty_user_returns_none(self):
        assert mprofile.recent_mood_summary(USER) is None
        assert mtrend.window_stats(USER) is None

    def test_summary_after_turns(self):
        _turn(risk="low", emotion="sad", neg=0.6)
        _turn(risk="low", emotion="anxious", neg=0.7)
        summary = mprofile.recent_mood_summary(USER)
        assert summary is not None
        assert "聊" in summary
        stats = mtrend.window_stats(USER)
        assert stats["turn_count"] == 2
        assert stats["days"] == 1
        assert stats["neg_avg"] is not None

    def test_risky_day_phrase_in_summary(self):
        _turn(risk="crisis", emotion="fearful", neg=0.95)
        summary = mprofile.recent_mood_summary(USER)
        assert summary is not None
        assert "危险" in summary

    def test_summary_included_in_memory_context(self):
        _turn(risk="low", emotion="sad", neg=0.5)
        ctx = retrieve.build_memory_context(USER)
        assert "【近期状态】" in ctx


# ============================== 可主动提及白名单 ==============================
class TestInitiable:
    def _fact(self, **over):
        base = {"type": "fact", "category": "preference", "text": "用户喜欢打篮球",
                "importance": 0.7, "mentions": 2,
                "first_seen": time.time(), "last_seen": time.time()}
        base.update(over)
        return base

    def test_fresh_low_stakes_allowed(self):
        assert retrieve.can_initiate(self._fact()) is True

    def test_risk_protected_never(self):
        assert retrieve.can_initiate(
            self._fact(category="risk", protected=True)) is False

    def test_sensitive_category_not_allowed(self):
        assert retrieve.can_initiate(self._fact(category="relationship")) is False
        assert retrieve.can_initiate(self._fact(category="plan")) is False

    def test_too_old_not_allowed(self):
        old = time.time() - 30 * 86400
        assert retrieve.can_initiate(self._fact(last_seen=old, first_seen=old)) is False

    def test_low_importance_not_allowed(self):
        assert retrieve.can_initiate(self._fact(importance=0.2)) is False

    def test_archived_not_allowed(self):
        assert retrieve.can_initiate(self._fact(archived=True)) is False

    def test_list_initiable_filters(self):
        mem_store.put_item(mem_store.ns_facts(USER), "k1", self._fact())          # 可提
        mem_store.put_item(mem_store.ns_facts(USER), "k2",
                           self._fact(category="health"))                          # 不可提
        mem_store.put_item(mem_store.ns_facts(USER), "k3",
                           self._fact(text="用户喜欢画画", importance=0.9))          # 可提,优先
        out = retrieve.list_initiable(USER)
        assert out == ["用户喜欢画画", "用户喜欢打篮球"]
