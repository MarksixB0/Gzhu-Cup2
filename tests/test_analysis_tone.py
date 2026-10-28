# -*- coding: utf-8 -*-
"""对话基调(tone):``extract_analysis`` 暴露给桌宠做表情的那个字段。

只测**契约与边界**,不碰 LLM —— ``extract_analysis`` 是纯函数,输入就是 langgraph 的
state dict。桌宠按 tone 选待机素材(见 desktop/src/pet/expression.ts),所以这里的
重点是:**取值集合固定**、且**负向情绪不会直接变成 tone 值**(那等于"标签即表情")。
"""
from types import SimpleNamespace


def _state(emotion: str | None, risk: str = "low") -> dict:
    """构造最小 state。

    ``emotional_analysis`` 用 SimpleNamespace 顶替 pydantic 模型即可 ——
    extract_analysis 只读它的属性,不调用方法。
    """
    emo = None if emotion is None else SimpleNamespace(
        emotion=emotion,
        description="",
        intensity=0.55,
        negative_score=0.5,
        confidence_score=0.7,
        is_user_dangerous=False,
    )
    return {"emotional_analysis": emo, "risk_level": risk}


def _tone(emotion: str | None, risk: str = "low") -> str:
    from gzhu_cup2.agent.web.engine import extract_analysis
    return extract_analysis(_state(emotion, risk))["tone"]


class TestTone:
    def test_neutral_low_is_normal(self):
        assert _tone(None) == "normal"

    def test_negative_is_gentle(self):
        """负向 → gentle:桌宠"降低活泼度",而不是换一张更惨的脸。"""
        assert _tone("sad") == "gentle"
        assert _tone("anxious") == "gentle"

    def test_happy_low_is_playful(self):
        assert _tone("happy") == "playful"

    def test_high_risk_is_gentle(self):
        assert _tone("sad", "high") == "gentle"
        assert _tone(None, "high") == "gentle"

    def test_crisis_is_serious(self):
        assert _tone("sad", "crisis") == "serious"

    def test_value_set_is_closed(self):
        """前端按值 switch;出现未知值会静默退回原行为 —— 这里守住集合。"""
        valid = {"playful", "normal", "gentle", "serious"}
        for emo in (None, "happy", "hopeful", "sad", "anxious", "angry",
                    "frustrated", "fearful", "neutral"):
            for risk in ("low", "medium", "high", "crisis"):
                assert _tone(emo, risk) in valid

    def test_tone_never_equals_raw_emotion(self):
        """表情只看基调:负向情绪**不得**直接成为 tone 值。

        这条是对 PLAN_NEXT.md §4.2 的机器化守门 —— 一旦有人图省事把
        `tone = emotion` 写进去,用户就会看到"桌宠按系统对我的判定变脸"。
        """
        for emo in ("sad", "anxious", "angry", "frustrated", "fearful"):
            assert _tone(emo, "low") != emo

    def test_tone_is_sole_field(self):
        """analysis 只暴露基调一个键:内部情绪/风险标记不进 API。"""
        from gzhu_cup2.agent.web.engine import extract_analysis
        st = extract_analysis(_state("sad"))
        assert set(st.keys()) == {"tone"}
        assert st["tone"] == "gentle"
