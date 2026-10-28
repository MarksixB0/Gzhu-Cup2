# -*- coding: utf-8 -*-
"""P1 新增逻辑离线测试(不触网):

- care/mode:对话基调纯函数 + 镜像确认短回应判别;
- ticks:镜像确认计数入日桶;
- capture 节点:镜像确认标记只在"上轮负面 + 本轮短回应"时计;
- retrieve 读侧分层:reply/care 的配额与未整理兜底差异。
"""
import time

from gzhu_cup2.agent.care import mode as cmode
from gzhu_cup2.agent.care.mode import dialogue_mode, tone_hint, classify_affect_reply
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import ticks
from gzhu_cup2.agent.long_memory import retrieve

USER = "u-p1"


# ============================ 对话基调 ============================
class TestDialogueMode:
    def test_mapping(self):
        assert dialogue_mode("sad", "high") == "gentle"
        assert dialogue_mode("happy", "low") == "playful"
        assert dialogue_mode("neutral", "low") == "normal"
        assert dialogue_mode("sad", "crisis") == "serious"
        assert dialogue_mode("sad", "medium") == "gentle"
        assert dialogue_mode("happy", "medium") == "normal"
        assert dialogue_mode(None, None) == "normal"

    def test_hint_no_internal_marker(self):
        for m in ("serious", "gentle", "playful", "normal"):
            h = tone_hint(m)
            assert "量表" not in h and "风险" not in h

    def test_negative_emotion_is_gentle_regardless_of_risk(self):
        """负向情绪一律 gentle —— 与 risk 档无关(含 risk 缺失/未登记)。

        回归:此前的分支是 `elif risk in ("low","medium") and neg`,其中 "low" 永远
        不可达(已被 playful 分支拦下),而 risk 为空/未登记时负向情绪会掉进 else
        返回 normal —— 与函数 docstring「负向情绪 → gentle」自相矛盾。
        真实触发面:任何绕过主图 triage、直接给 state 组装 llm_params 的调用方。
        """
        for neg in ("sad", "anxious", "angry", "frustrated", "fearful"):
            for risk in ("low", "medium", "high", "crisis", "", None, "unknown"):
                if risk in ("crisis", "high"):
                    assert dialogue_mode(neg, risk) in ("serious", "gentle")
                else:
                    assert dialogue_mode(neg, risk) == "gentle", \
                        f"{neg}/{risk} 应 gentle"

    def test_playful_only_for_positive_low(self):
        """playful 只在"低风险 + 正向情绪"出现 —— 不得外溢到 medium/负向。"""
        assert dialogue_mode("happy", "low") == "playful"
        assert dialogue_mode("happy", "medium") == "normal"
        assert dialogue_mode("happy", "high") == "gentle"
        assert dialogue_mode("sad", "low") == "gentle"


# ============================ 镜像确认判别 ============================
class TestClassifyAffectReply:
    def test_short_affirm_and_deny(self):
        assert classify_affect_reply("嗯") == "confirm"
        assert classify_affect_reply("对，确实") == "confirm"
        assert classify_affect_reply("不是，其实还好") == "deny"
        assert classify_affect_reply("没有啦") == "deny"

    def test_long_explanation_returns_none(self):
        assert classify_affect_reply("不是你想的那样，其实是因为最近加班太多了，领导又催得紧") is None
        assert classify_affect_reply("我今天去了一趟医院，医生说我没什么大碍") is None

    def test_empty_returns_none(self):
        assert classify_affect_reply("") is None
        assert classify_affect_reply(None) is None


# ============================ ticks:镜像确认计数 ============================
class TestTicksAffectReply:
    def test_bucket_counts_confirm_deny(self):
        day = ticks._day_str(time.time())
        ticks.bump_day_bucket(USER, {"emotion": "sad", "negative_score": 0.8,
                                     "risk_level": "low", "affect_reply": "deny"})
        ticks.bump_day_bucket(USER, {"emotion": "sad", "negative_score": 0.8,
                                     "risk_level": "low", "affect_reply": "deny"})
        ticks.bump_day_bucket(USER, {"emotion": "neutral", "affect_reply": "confirm"})
        b = mem_store.get_item(ticks.ns_ticks(USER), day)
        assert b is not None
        assert int(b.get("deny_count", 0)) == 2
        assert int(b.get("confirm_count", 0)) == 1

    def test_unknown_reply_not_counted(self):
        day = ticks._day_str(time.time())
        ticks.bump_day_bucket(USER, {"affect_reply": "bogus"})
        b = mem_store.get_item(ticks.ns_ticks(USER), day)
        assert int(b.get("deny_count", 0)) == 0
        assert int(b.get("confirm_count", 0)) == 0


# ============================ capture:镜像标记 ============================
class TestCaptureMirrorMarker:
    def test_marker_only_after_negative_turn(self):
        from gzhu_cup2.agent.nodes.capture_memory import _mirror_marker
        mem_store.capture_turn(USER, "t1", "我今天好难过，感觉撑不住", "我听着呢",
                               meta={"negative_score": 0.85, "emotion": "sad",
                                     "risk_level": "low"})
        assert _mirror_marker(USER, "不是，其实没那么糟") == "deny"
        assert _mirror_marker(USER, "对，最近确实很难") == "confirm"
        assert _mirror_marker(USER, "我今天去逛了街，买了点东西，心情好多了") is None

    def test_no_marker_without_previous_negative(self):
        from gzhu_cup2.agent.nodes.capture_memory import _mirror_marker
        mem_store.capture_turn(USER, "t1", "今天天气不错", "是呀",
                               meta={"negative_score": 0.1, "emotion": "neutral",
                                     "risk_level": "low"})
        assert _mirror_marker(USER, "嗯") is None


# ============================ retrieve:读侧分层 ============================
class TestRetrievePurpose:
    def _seed(self):
        now = time.time()
        mem_store.put_item(mem_store.ns_facts(USER), "k1", {
            "type": "fact", "text": "用户养了一只猫", "category": "life",
            "importance": 0.8, "mentions": 2, "first_seen": now,
            "last_seen": now, "archived": False, "protected": False,
        })
        mem_store.put_item(mem_store.ns_episodes(USER), "s1", {
            "type": "episode", "digest": "聊了猫生病的事", "category": "episode",
            "protected": False, "archived": False, "importance": 0.6,
            "mentions": 1, "first_seen": now, "last_seen": now, "ts_end": now,
        })
        # 另一会话待固化段 → reply 兜底可见,care 不携带
        mem_store.capture_turn(USER, "t-other", "最近找工作很焦虑", "辛苦了",
                               meta={"negative_score": 0.6, "risk_level": "low"})

    def test_reply_includes_pending_fallback(self):
        self._seed()
        ctx = retrieve.build_memory_context(USER, purpose="reply")
        assert "最近对话(尚未整理)" in ctx
        assert "猫" in ctx

    def test_care_omits_pending_and_is_shorter(self):
        self._seed()
        care_ctx = retrieve.build_memory_context(USER, purpose="care")
        assert "最近对话(尚未整理)" not in care_ctx
        assert "猫" in care_ctx

    def test_debug_keeps_pending(self):
        self._seed()
        dbg = retrieve.build_memory_context(USER, purpose="debug")
        assert "最近对话(尚未整理)" in dbg
