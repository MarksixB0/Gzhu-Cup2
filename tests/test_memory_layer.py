# -*- coding: utf-8 -*-
"""长期记忆「标签卡 / 维度账本 / 趋势层 / 画像层」离线测试(不触网)。

依赖 conftest 的 autouse 夹具(内存 Store 每用例重置)。
"""
import time
from datetime import datetime

from gzhu_cup2.agent.core.schemas import MemoryExtraction
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import tags
from gzhu_cup2.agent.long_memory import ledger
from gzhu_cup2.agent.long_memory import trend
from gzhu_cup2.agent.long_memory import profile as mprofile

USER = "u-tags"


# ============================ tags:状态弧 ============================
class TestStateArc:
    def test_falling_arc_from_turns(self):
        now = time.time()
        turns = [
            {"seq": 0, "meta": {"emotion": "happy", "negative_score": 0.1, "intensity": 0.5}},
            {"seq": 1, "meta": {"emotion": "sad", "negative_score": 0.8, "intensity": 0.9}},
        ]
        arc = tags.state_arc(turns)
        assert arc["valence_arc"] == "falling"
        assert arc["dominant_emotion"] in ("happy", "sad")
        assert arc["emotion_shift"] == {"from": "happy", "to": "sad"}
        assert arc["arousal"] == "high"  # 平均强度 (0.5+0.9)/2=0.7 ≥ 0.66
        assert arc["sample_n"] == 2

    def test_stable_when_same_polarity(self):
        now = time.time()
        turns = [{"seq": i, "meta": {"emotion": "sad", "negative_score": 0.7}}
                 for i in range(4)]
        assert tags.state_arc(turns)["valence_arc"] == "stable"

    def test_missing_meta_degrades_to_neutral(self):
        arc = tags.state_arc([{"seq": 0}, {"seq": 1}])
        assert arc["dominant_emotion"] == "neutral"
        assert arc["valence_arc"] == "stable"


# ============================ tags:清洗收敛 ============================
class TestClampers:
    def test_topics_only_known_and_limited(self):
        got = tags.clamp_topics(
            ["school", "hobby_gaming", "school", "work", "relationship", "sleep"])
        assert got == ["school", "work", "relationship"]  # 未知剔除、去重、≤3

    def test_short_list_trimmed(self):
        got = tags.clamp_short_list(["准备期末考试并且很紧张", "   ", "a" * 100], limit=2, max_len=5)
        assert got[0].startswith("准备期")  # 超长被截断
        assert len(got) == 2 and "" not in got

    def test_evidence_unknown_dim_dropped(self):
        got = tags.clean_evidence({
            "not_a_dim": {"level": 3, "quote": "x"},
            "phq_sleep": {"level": 2, "quote": "最近睡不好"},
            "phq_mood": {"level": 0, "quote": "提了一句但无困扰"},
            "gad_worry": {"level": 9, "quote": "超档被钳制"},
        })
        assert set(got) == {"phq_sleep", "gad_worry"}
        assert got["phq_sleep"]["level"] == 2
        assert got["gad_worry"]["level"] == 3  # 9 → 钳到 3


# ============================ store:标签卡落库 ============================
class TestStoreTagCard:
    def _commit(self):
        now = time.time()
        pending = {
            "turns": [
                {"seq": 0, "ts": now, "meta": {"emotion": "happy", "negative_score": 0.1}},
                {"seq": 1, "ts": now + 5, "meta": {"emotion": "sad", "negative_score": 0.8}},
            ],
            "next_seq": 2, "started_at": now, "last_ts": now + 5, "threads": ["t"],
        }
        extraction = MemoryExtraction(
            digest="聊了考试压力",
            importance=0.6,
            topics=["school", "school", "bogus_topic"],
            dimension_evidence={
                "phq_sleep": {"level": 2, "quote": "最近睡不好"},
                "bogus_dim": {"level": 3, "quote": "x"},
            },
            triggers=["考试周"],
            coping=["打游戏", ""],
        )
        mem_store.commit_segment(USER, pending, extraction)

    def test_episode_holds_tag_card(self):
        self._commit()
        eps = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))]
        assert len(eps) == 1
        ep = eps[0]
        assert ep["topics"] == ["school"]
        assert set(ep["dimension_evidence"]) == {"phq_sleep"}
        assert ep["dimension_evidence"]["phq_sleep"]["level"] == 2
        assert ep["triggers"] == ["考试周"]
        assert ep["coping"] == ["打游戏"]
        assert ep["valence_arc"] == "falling"
        assert ep["emotion_shift"] == "happy→sad"
        assert ep["dominant_emotion"] in ("happy", "sad")


# ============================ ledger:维度账本 ============================
class TestLedger:
    def test_scan_and_uncovered(self):
        TestStoreTagCard()._commit()
        ev = ledger.scan_evidence(USER)
        assert set(ev) == {"phq_sleep"}
        assert ev["phq_sleep"]["count"] == 1
        assert ev["phq_sleep"]["level"] == 2
        uncovered = ledger.uncovered_dims(USER)
        assert "phq_sleep" not in uncovered
        assert len(uncovered) == len(ledger.DIM_KEYS) - 1

    def test_empty_user(self):
        assert ledger.scan_evidence("nobody") == {}
        assert len(ledger.uncovered_dims("nobody")) == len(ledger.DIM_KEYS)


# ============================ trend:窗口聚合 ============================
class TestTrendWindow:
    def _two_sad(self):
        mem_store.capture_turn(USER, "t", "内容", "回复", meta={
            "risk_level": "low", "emotion": "sad", "negative_score": 0.6, "intensity": 0.7,
        })
        mem_store.capture_turn(USER, "t", "内容2", "回复2", meta={
            "risk_level": "low", "emotion": "sad", "negative_score": 0.8, "intensity": 0.9,
        })

    def test_window_stats_after_turns(self):
        self._two_sad()
        st = trend.window_stats(USER)
        assert st is not None
        assert st["turn_count"] == 2
        assert st["days"] == 1
        assert st["neg_avg"] is not None and 0.69 < st["neg_avg"] < 0.71
        assert st["dominant_emotion"] == "sad"
        assert abs(st["negative_share"] - 1.0) < 1e-6
        assert st["mood_index"] is not None and st["mood_index"] < 0.4

    def test_empty_user_none(self):
        assert trend.window_stats("nobody") is None


# ============================ profile:近况 / 特质 / 触发 ============================
class TestProfileLayer:
    def _capture_and_commit(self):
        """先捕获两轮(喂日桶),再固化(喂 episode 标签卡),两层都有数据。"""
        mem_store.capture_turn(USER, "t", "今天很开心", "那就好", meta={
            "risk_level": "low", "emotion": "happy", "negative_score": 0.1,
        })
        mem_store.capture_turn(USER, "t", "但考试压力好大", "辛苦了", meta={
            "risk_level": "low", "emotion": "sad", "negative_score": 0.8,
        })
        pending = mem_store.get_pending(USER)
        extraction = MemoryExtraction(
            digest="考前提到了考试压力",
            topics=["school"],
            dimension_evidence={"phq_sleep": {"level": 2, "quote": "最近睡不好"}},
        )
        mem_store.commit_segment(USER, pending, extraction)

    def test_recent_mood_summary_text(self):
        mem_store.capture_turn(USER, "t", "内容", "回复", meta={
            "risk_level": "crisis", "emotion": "fearful", "negative_score": 0.95,
        })
        summary = mprofile.recent_mood_summary(USER)
        assert summary is not None and "聊" in summary and "危险" in summary

    def test_trait_card_with_tags(self):
        self._capture_and_commit()
        card = mprofile.trait_card(USER)
        assert card is not None
        assert set(card["traits"]) == {
            "baseline_mood", "stability", "negative_tendency", "social_orientation"}
        assert card["evidence"]["topic_episodes"] >= 1
        assert card["evidence"]["turn_count"] == 2

    def test_trigger_mining_topic_negative(self):
        self._capture_and_commit()
        rows = mprofile.trigger_mining(USER)
        assert isinstance(rows, list)
        assert any(r["topic"] == "school" and r["n_total"] == 1 and r["n_neg"] == 1
                   for r in rows)


# ============================ tags:摘要清洗(防自我强化闭环) ============================
class TestCleanDigest:
    """digest 会被下一轮读回 prompt:助手推测绝不能进去,否则形成自我强化闭环。"""

    def test_drops_assistant_subject_sentences(self):
        raw = ("用户询问助手名称。"
               "助手根据历史记忆指出用户此前因答辩压力出现恍惚状态，并建议抚摸宠物安抚。")
        out = tags.clean_digest(raw)
        assert out.startswith("用户询问助手名称")
        assert "根据历史记忆指出" not in out
        assert "建议抚摸宠物" not in out

    def test_keeps_user_sentences_drops_assistant_ones(self):
        raw = "用户今天心情不错。助手回复了几句鼓励的话。用户提到下周要答辩。"
        out = tags.clean_digest(raw)
        assert "用户今天心情不错" in out
        assert "用户提到下周要答辩" in out
        assert "鼓励" not in out

    def test_falls_back_to_raw_when_everything_dropped(self):
        # 异常输入不返回空 —— 宁可留着也不能把整段记忆丢掉
        raw = "助手做了一些事。"
        assert tags.clean_digest(raw) == raw

    def test_collapses_whitespace_and_truncates(self):
        assert tags.clean_digest("  用户  说了  两句话  ") == "用户 说了 两句话"
        assert len(tags.clean_digest("用户" * 400, max_len=50)) == 50

    def test_empty_inputs(self):
        assert tags.clean_digest("") == ""
        assert tags.clean_digest(None) == ""

    def test_commit_segment_cleans_digest_before_store(self):
        now = time.time()
        pending = {
            "turns": [{"seq": 0, "user": "最近睡不好", "ai": "我在听", "meta": {}}],
            "next_seq": 1, "started_at": now, "last_ts": now, "threads": ["t"],
        }
        mem_store.commit_segment(USER, pending, MemoryExtraction(
            digest="用户说最近睡不好。助手推测用户是因为答辩压力导致失眠。",
            importance=0.6,
        ))
        eps = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))]
        assert len(eps) == 1
        assert "用户说最近睡不好" in eps[0]["digest"]
        assert "助手推测" not in eps[0]["digest"]

    def test_commit_segment_absolutizes_digest(self):
        """摘要里的相对时间词也要被绝对化 —— 事实与摘要必须同一口径。

        回归:此前只对事实跑 ``dates.absolutize``,digest 原样落库、又被原样读回
        提示词;day1「明天要考试」到 day2 仍被模型复述为「明天」(实测)。
        """
        anchor = datetime(2026, 9, 9, 20, 0)
        pending = {
            "turns": [{"seq": 0, "user": "明天我要考试", "ai": "嗯,我记着",
                       "meta": {}, "ts": anchor.timestamp()}],
            "next_seq": 1, "started_at": anchor.timestamp(),
            "last_ts": anchor.timestamp(), "threads": ["t"],
        }
        mem_store.commit_segment(USER, pending, MemoryExtraction(
            digest="用户说明天要考试,有些紧张。", importance=0.6))
        eps = [it.value for it in mem_store.list_items(mem_store.ns_episodes(USER))]
        assert len(eps) == 1
        assert "明天" not in eps[0]["digest"], "摘要原样保留相对时间词 → 会被原样读回提示词"
        assert "2026-09-10" in eps[0]["digest"]
