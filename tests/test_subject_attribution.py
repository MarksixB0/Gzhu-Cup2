# -*- coding: utf-8 -*-
"""指代主体:代码不猜字符串,由模型在它读到的这句话上判断。

实测(见 ``MANUAL_CHAT_SCRIPT.md`` §K R2/R5)
--------------------------------------------
- "我同学说他不想活了，我该怎么帮他" → 回复把**用户本人**当作有自杀意念的人安抚;
- "**我的**同学最近心情不稳定，似乎想自杀了" → 走**第一人称**危机协议,
  在教用户自己打热线;
- "不是我，是我的同学" → 被当成"虚惊一场"("原来只是同学啊，那就好"),
  还编造出"你刚才说想自杀";
- "你太没用了" → 被当成用户在自我否定,反过来安慰用户。

走过一次弯路(保留记录,别再走)
------------------------------
第一版在 ``nodes/triage.py`` 里**用词表猜主体**(我同学/朋友/我的室友/别人/有人…),
实测立刻被"**我的**同学"打回(中间那个"的"让判别落回 self),补完词表还会有下一个写法。
中文里指代第三方的说法是**无穷**的(我一个发小/同宿舍那哥们儿/实习认识的人…),
穷举必然漏;而且词表用例只能证明"覆盖了我自己想到的写法",证明不了实际场景 ——
"这句话说的是谁"是**语义**判断,该由模型做。

现在的分工
----------
- ``nodes/triage.py`` 只做**安全判定**(``is_user_dangerous``):毫秒级、确定性、
  零 LLM —— 这部分绝不能交给模型,也**不碰**主体;
- 危机分支只有**一份通用指令**,要求模型先判断"处于风险中的是谁",再按两种情况回复;
- 普通路径的指代要求写进 ``HARD_RULES``(第 6/7 条),同样不含任何称谓词表。

⚠ 本文件能证明的是「**代码里不再有按措辞分派的逻辑**」+「通用指令确实在提示词里」。
   它**不能**证明模型实际会照做 —— 那只能靠真实对话验证,别把绿灯当成行为保证。
"""
import types

import pytest
from langchain_core.messages import HumanMessage

from gzhu_cup2.agent.algo.components.triage import classify
from gzhu_cup2.agent.prompts import prompts

# 含危机词的第三方表述:必须仍然进危机协议(安全优先)
_THIRD_PERSON_CRISIS = (
    "我同学说他不想活了，我该怎么帮他",
    "我的同学最近心情不稳定，似乎想自杀了",
    "实习认识的那个人跟我说他不想活了",
    "朋友想自杀",
    "有人想死",
)
# 不含危机词的第三方表述 / 澄清轮:只验证"提示词不做措辞分派"
_THIRD_PERSON_PLAIN = (
    "我一个发小最近状态很差，我很担心",
    "同宿舍那哥们儿说他撑不下去了",
    "别人都说我不好",
    "不是我，是我的同学",
)


_ALL_THIRD_PERSON = _THIRD_PERSON_CRISIS + _THIRD_PERSON_PLAIN


def _state(text="在吗", risk_level="low"):
    return {
        "messages": [HumanMessage(content=text)],
        "process_input": text,
        "emotional_analysis": types.SimpleNamespace(emotion="neutral", description=""),
        "risk_level": risk_level,
        "memory_context": None,
        "crisis_handled": False,
    }


def _crisis_prompt(text="我想死"):
    return "\n".join(
        str(m.content) for m in
        prompts.build_handle_crisis_prompt(_state(text, risk_level="crisis")))


def _gen_prompt(text="在吗"):
    return "\n".join(
        str(m.content)
        for m in prompts.build_generate_response_prompt(_state(text)))


def _aside_from_input(prompt: str, text: str) -> str:
    """把"用户原话"挖掉后再比较。

    提示词里**当然要**带上用户这一句(那是必须的),所以要比的是**除原话以外**的部分:
    "系统打算怎么处理这句话"有没有随措辞变化。
    """
    return prompt.replace(text, "<INPUT>")


# ============================ 1. 机器可验证的"写活":处理方式不随措辞变化 ============================
class TestPromptDoesNotBranchOnWording:
    """任何第三方写法,系统**除原话以外**给出的提示词完全相同。

    这是本文件最有价值的一条:如果哪天有人再把主体判断塞回代码(按措辞分派提示词),
    不同措辞必然产出不同的指令段落 —— 这条会立刻红。换句话说,它锁住的是
    **"不存在一个需要我猜对的词表"** 这个性质,而不是某个词表的内容。
    """

    @pytest.mark.parametrize("text", _ALL_THIRD_PERSON)
    def test_crisis_handling_identical_for_any_wording(self, text):
        assert (_aside_from_input(_crisis_prompt(text), text)
                == _aside_from_input(_crisis_prompt("我想死"), "我想死")), text

    @pytest.mark.parametrize("text", _ALL_THIRD_PERSON)
    def test_generate_handling_identical_for_any_wording(self, text):
        assert (_aside_from_input(_gen_prompt(text), text)
                == _aside_from_input(_gen_prompt("我今天很累"), "我今天很累")), text


# ============================ 2. 通用指令真的在提示词里 ============================
class TestGeneralReferentInstruction:
    def test_crisis_requires_referent_judgement(self):
        assert "处于风险中的是谁" in _crisis_prompt()

    def test_crisis_covers_both_cases(self):
        text = _crisis_prompt()
        assert "他自己" in text
        assert "别人" in text

    def test_crisis_forbids_minimising_third_party_risk(self):
        """实测:模型把"不是我"当成好消息,说"原来只是同学啊，那就好"。"""
        text = _crisis_prompt()
        assert "虚惊一场" in text or "好消息" in text

    def test_crisis_still_gives_hotline(self):
        text = _crisis_prompt()
        assert "12356" in text or "400-161-9995" in text

    def test_crisis_instruction_is_single_source(self):
        """只有**一份**危机指令,且求助资源来自 config(不在这里另写一份)。"""
        from gzhu_cup2.agent.config import config as cfg
        from gzhu_cup2.agent.prompts import prompts as p
        assert not hasattr(p, "_CRISIS_OTHER_INSTRUCTION")
        assert not hasattr(p, "_CRISIS_SUPPORT")          # 硬编码那份已删除
        text = p.crisis_instruction()
        assert isinstance(text, str) and text
        # 逐字来自 config:改了 config 的热线,提示词必须跟着变
        assert str(cfg.CRISIS_HOTLINES).strip() in text

    def test_hard_rules_tell_model_to_judge_referent(self):
        assert "回应前先判断这句话说的是谁" in prompts.HARD_RULES

    def test_hard_rules_forbid_third_party_relief(self):
        assert "不要因为「不是他自己」就放松或庆贺" in prompts.HARD_RULES

    def test_hard_rules_forbid_inventing_user_self_harm(self):
        assert "不要凭空说用户本人有自杀念头" in prompts.HARD_RULES


# ============================ 3. 反向锁:词表不得回归 ============================
class TestNoVocabularyRegression:
    """退化成词表匹配就红 —— 那是被实测打回来的方向。"""

    def test_triage_has_no_referent_vocabulary(self):
        from gzhu_cup2.agent.algo.components import triage as t
        for banned in ("resolve_subject", "_other_hit", "_ai_hit", "_OTHER_RE",
                       "_OTHER_BARE", "_OTHER_POSSESSIVE", "_OTHER_STRONG",
                       "_OTHER_WEAK", "_AI_ADDRESS", "_AI_DEROGATORY",
                       "SUBJECT_OTHER", "SUBJECT_AI"):
            assert not hasattr(t, banned), f"主体词表回归: {banned}"

    def test_prompts_has_no_subject_branching_helper(self):
        for banned in ("build_subject_hint", "_SUBJECT_OTHER", "_SUBJECT_AI"):
            assert not hasattr(prompts, banned), banned

    def test_state_has_no_subject_field(self):
        import inspect
        from gzhu_cup2.agent.core import state as state_mod
        assert "input_subject" not in inspect.getsource(state_mod)


# ============================ 4. 安全判定不受影响(绝不能动的部分) ============================
class TestJudgementLayerUnchanged:
    @pytest.mark.parametrize("text", _THIRD_PERSON_CRISIS)
    def test_third_person_still_reaches_crisis_protocol(self, text):
        """宁可多给一次热线:第三人称**仍然**进危机协议。主体判断只影响文案。"""
        assert classify(text)["is_user_dangerous"] is True, text

    def test_negation_guard_unaffected(self):
        for text in ("我不想死", "我不会自杀的"):
            assert classify(text)["is_user_dangerous"] is False, text

    def test_first_person_crisis_unchanged(self):
        assert classify("我想死")["is_user_dangerous"] is True

    def test_plain_third_person_is_not_crisis(self):
        """没有危机词的第三方表述不该被当成危机(与上面相反方向的锁)。"""
        for text in _THIRD_PERSON_PLAIN:
            assert classify(text)["is_user_dangerous"] is False, text
