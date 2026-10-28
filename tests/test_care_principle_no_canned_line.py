# -*- coding: utf-8 -*-
"""R4 回归:提示词里不得内置"可照抄的话术",且必须要求每轮换说法。

实测现象(见 ``MANUAL_CHAT_SCRIPT.md`` §K R4)
--------------------------------------------
心情不好的场景里,AI 回复**很大概率带"我在"**,读起来千篇一律。

根因不是"模型没创意",而是**提示词把答案写好了**:
``pet/character/prompts.py`` 的 ``care_principle`` 里内置了一句示范话术 ——
``"可以说：我在呀。不着急。想说的时候再说。"`` ——
而 ``care_principle`` 由 ``build_generate_response_prompt`` **每轮注入**,
且**角色卡不可覆盖**。实测渲染后系统提示词里确实出现 "我在呀"。

第二个放大器:``prompts.py`` 把最近历史 ``messages[-4:-1]`` 注入,其中**包含上一轮的
AI 回复** → 模型倾向模仿自己上一轮 → 自我强化循环(这同时是 R2 逐字复读的机制)。

本文件钉两件事:
  1. **不再有可逐字照抄的范例**(把"给成品"改成"给要求");
  2. **HARD_RULES 里有一条明确的反复读约束**。

⚠ 改 ``care_principle`` 时有两处硬约束(现有测试钉着),本文件一并保护:
  · 必须保留子串 **"观察结论"**  —— ``test_care_intensity.py:214,221,249``;
  · 首句必须保留 **"只能感觉到眼前这一句话的语气"** —— ``test_care_intensity.py:240``;
  · 全文不得含 ``watch/elevated/stable/tier/档位`` 与 ``监测/检测`` —— ``test_care_intensity.py:31,148-152``。
"""
from langchain_core.messages import AIMessage, HumanMessage

from gzhu_cup2.agent.pet.character import prompts as pprompts
from gzhu_cup2.agent.prompts import prompts as fprompts

# 与 test_care_intensity.py 同一份口径:档位名永不进任何给模型的文本
_FORBIDDEN = ("watch", "elevated", "stable", "tier", "档位")

# 被实测抓到的"成品范例":它出现在提示词里,模型就会照说
_CANNED_LINE = "我在呀"


def _rendered_prompt(tier: str = "stable") -> str:
    """渲染一轮真实的生成提示词(system + human 全文)。

    ⚠ 必须取 ``.content``:消息对象上做 ``in`` 判断会静默失效(pydantic ``__iter__``
    产出 ``(字段名, 值)`` 元组,字符串永远不在其中,断言恒真)。
    """
    state = {
        "messages": [HumanMessage(content="有点累"), AIMessage(content="嗯")],
        "process_input": "今天有点累",
        "emotional_analysis": None,
        "risk_level": "low",
        "guard_tier": tier,
        "memory_context": None,
    }
    return "\n".join(str(m.content) for m in fprompts.build_generate_response_prompt(state))


# ============================ 1. 不再有可照抄的成品范例 ============================
class TestNoCannedLine:
    def test_principle_text_has_no_canned_line(self):
        assert _CANNED_LINE not in pprompts.resolve(None)["care_principle"]

    def test_rendered_prompt_has_no_canned_line(self):
        """端到端:实测泄露点是**渲染后的系统提示词**,所以在这里钉死。"""
        text = _rendered_prompt()
        assert _CANNED_LINE not in text, "提示词里仍有可逐字照抄的范例"

    def test_rendered_prompt_has_no_canned_line_in_any_tier(self):
        for tier in ("stable", "watch", "elevated"):
            assert _CANNED_LINE not in _rendered_prompt(tier), tier

    def test_no_period_style_imperative_left(self):
        """被删掉的是一整句"可以说:…"的成品;顺带确认它没以别的形式留下。"""
        assert "不着急。想说的时候再说" not in _rendered_prompt()


# ============================ 2. 反复读:必须有明确约束 ============================
class TestAntiRepetitionRule:
    def test_hard_rules_requires_rephrasing(self):
        """反复读是**行为要求**,必须作为硬规则存在(而不是靠模型自觉)。"""
        assert "不要重复" in fprompts.HARD_RULES

    def test_hard_rules_has_numbered_anti_repetition_item(self):
        assert "5. 不要重复" in fprompts.HARD_RULES

    def test_anti_repetition_rule_reaches_the_prompt(self):
        assert "不要重复" in _rendered_prompt()

    def test_hard_rules_has_no_forbidden_words(self):
        text = fprompts.HARD_RULES
        for word in _FORBIDDEN + ("监测", "检测"):
            assert word not in text, f"HARD_RULES 含内部词 {word!r}"


# ============================ 3. 保护既有契约(改文案时最容易手滑撞到) ============================
class TestPrincipleContractsPreserved:
    def test_keeps_observation_boundary_word(self):
        """``test_care_intensity.py`` 断言这个子串必须在 —— 它是"观察结论"边界的载体。"""
        assert "观察结论" in pprompts.resolve(None)["care_principle"]

    def test_keeps_required_first_sentence(self):
        """``test_care_intensity.py:240`` 逐字断言这一句(渲染后)必须还在。"""
        assert "只能感觉到眼前这一句话的语气" in pprompts.resolve(None)["care_principle"]

    def test_no_internal_words(self):
        text = pprompts.resolve(None)["care_principle"]
        for word in _FORBIDDEN + ("监测", "检测"):
            assert word not in text, f"care_principle 含内部词 {word!r}"

    def test_macros_are_renderable(self):
        """渲染后不得残留宏(否则模型看到占位符就出戏)。"""
        text = _rendered_prompt()
        assert "{{char}}" not in text and "{{user}}" not in text

    def test_principle_still_in_every_tier(self):
        for tier in ("stable", "watch", "elevated"):
            assert "观察结论" in _rendered_prompt(tier), tier
