# -*- coding: utf-8 -*-
"""R1 回归:危机提示词不得把内部判定泄露给用户。

实测现象(见 ``MANUAL_CHAT_SCRIPT.md`` §K R1)
--------------------------------------------
用户说"我想死",AI 回:「我知道你现在**很平静**,但那种"想死"的感觉……」
用户说"我觉得活着没意思,不想活了",AI 回:「我知道这种"**平静**"背后藏着多大的痛苦」

根因(两处叠加)
--------------
1. ``nodes/triage.py`` 的危机短语表与情绪短语表**不重叠** → 危机句命不中任何情绪词 →
   走 ``label="neutral"`` 分支 → ``description = _ZH["neutral"] + "情绪"`` = **"平静情绪"**;
2. ``build_handle_crisis_prompt``（``prompts/prompts.py``）把这个内部描述**裸着**注进
   human prompt：``"用户此时的心情为：neutral，其描述为：平静情绪"``，
   还有一句 ``"用户的心情危险指数为crisis(在low, medium, high, crisis当中)"``。

非危机路径的同类内部状态是**带禁令**注入的
(``"（内部提示…严禁向用户提及或复述…）"``);危机路径此前**没有**这层禁令 ——
所以这里钉死的是更强的口径:**干脆不注入**。

为什么它不只是"泄密"
--------------------
对一个刚说"我想死"的人说"你很平静",是在**否定他的痛苦**,危机干预里是明确的错误方向
("表面平静"绝不能被当作安全信号)。而且它是**系统性**的:任何不含情绪词的危机句
都会被描述成"平静",而危机句恰恰不含情绪词。

本文件不需要真模型:只构造与检查提示词文本 + 判定层的纯函数。
"""
import types

from langchain_core.messages import AIMessage, HumanMessage

from conftest import TEST_CARD_NAME

from gzhu_cup2.agent.algo.components.triage import classify
from gzhu_cup2.agent.prompts import prompts

# 英文档位名:内部枚举的形态之一
_TIER_WORDS = ("low", "medium", "high", "crisis")


def _crisis_state():
    """最小可用的危机轮 state。

    刻意**不含** guard_tier / user_id / llm_params —— 与
    test_character_integration.py 的夹具一致,顺带钉住"提示词构造必须容忍缺键"。
    """
    return {
        "messages": [
            HumanMessage(content="最近有点累"), AIMessage(content="嗯"),
            HumanMessage(content="还行吧"), AIMessage(content="在的"),
            HumanMessage(content="我想死"),
        ],
        "process_input": "我想死",
        "emotional_analysis": types.SimpleNamespace(
            emotion="neutral", description="平静情绪"),
        "risk_level": "crisis",
        "memory_context": None,
        "crisis_handled": False,
    }


def _text() -> str:
    sys_msg, human_msg = prompts.build_handle_crisis_prompt(_crisis_state())
    return sys_msg.content + "\n" + human_msg.content


# ============================ 1. 提示词:内部枚举绝不注入 ============================
class TestCrisisPromptNoInternalEnum:
    def test_human_prompt_has_no_internal_enum(self):
        """human prompt 是此前注入三个内部枚举的地方(情绪标签 / 描述 / 档位名)。

        ⚠ 必须取 ``.content``:``build_handle_crisis_prompt`` 返回的是
        ``[SystemMessage, HumanMessage]`` **对象**。直接对消息对象做 ``in`` 判断会
        静默失效 —— pydantic ``__iter__`` 产出的是 ``(字段名, 值)`` 元组,
        字符串永远不在其中,于是断言**恒真**、永远绿着。本文件初版就踩了这个坑。
        """
        _, msg = prompts.build_handle_crisis_prompt(_crisis_state())
        human = msg.content
        for word in ("平静", "neutral") + _TIER_WORDS:
            assert word not in human, f"危机 human prompt 泄露内部标记 {word!r}"

    def test_specific_leaked_phrases_are_gone(self):
        """逐字钉住实测泄露的那两句的措辞。"""
        _, msg = prompts.build_handle_crisis_prompt(_crisis_state())
        human = msg.content
        assert "用户此时的心情" not in human
        assert "危险指数" not in human
        assert "在low, medium, high, crisis当中" not in human

    def test_system_prompt_has_no_calm_or_emotion_label(self):
        """system 段同样不得出现"平静"/情绪标签(它经 HARD_RULES 与人格段进入模型)。

        ⚠ 刻意**不**在这里断言 "危险指数" —— ``HARD_RULES`` 第 2 条是**禁止**模型说
        "危险指数",它本身**包含**这个词作为反例,断言它会误伤规则本身。
        """
        sys_msg, _ = prompts.build_handle_crisis_prompt(_crisis_state())
        system = sys_msg.content
        assert "平静" not in system
        assert "neutral" not in system
        for word in _TIER_WORDS:
            assert word not in system

    def test_full_prompt_has_no_tier_word(self):
        """端到端:system + human 全文不得出现任何英文档位名。"""
        text = _text()
        for word in _TIER_WORDS:
            assert word not in text, f"危机提示词泄露档位名 {word!r}"


# ============================ 2. 安全红线:去枚举不得误伤 ============================
class TestCrisisSafetyIntact:
    def test_hotline_still_present(self):
        """⛔3:危机话术必须给热线 —— 去掉枚举时最容易误伤的就是它。"""
        text = _text()
        assert "12356" in text or "400-161-9995" in text

    def test_hard_rules_still_injected(self):
        """安全块必须仍在 system 段(角色卡无法覆盖)。"""
        sys_msg, _ = prompts.build_handle_crisis_prompt(_crisis_state())
        assert prompts.HARD_RULES in sys_msg.content

    def test_persona_still_present(self, test_card):
        """人格链路不受影响(危机时仍是同一个人)。

        卡由 conftest 的 test_card 夹具现造 —— 仓库里不再带任何预设人格。
        """
        sys_msg, _ = prompts.build_handle_crisis_prompt(_crisis_state())
        assert TEST_CARD_NAME in sys_msg.content


# ============================ 3. 判定层:危机不再产出"平静" ============================
class TestTriageCrisisDescription:
    def test_crisis_description_is_empty(self):
        c = classify("我想死")
        assert c["is_user_dangerous"] is True
        assert c["description"] != "平静情绪"
        assert c["description"] == ""

    def test_crisis_emotion_label_deliberately_unchanged(self):
        """刻意**不动** emotion 标签:``_ZH`` 是共用的,改它会波及其他中性句的展示。

        缩爆炸半径:只在危机这一支收口 description。
        """
        assert classify("我想死")["emotion"] == "neutral"

    def test_non_crisis_neutral_still_says_calm(self):
        """反向确认:非危机的中性句**仍然**是"平静情绪" —— 证明我们没改 _ZH。"""
        c = classify("今天天气不错")
        assert c["is_user_dangerous"] is False
        assert c["description"] == "平静情绪"

    def test_non_crisis_emotion_descriptions_unchanged(self):
        """常见负面句的描述不受影响(防过度修改)。"""
        assert classify("我很难过")["description"] == "悲伤情绪"
        assert classify("我很生气")["description"] == "愤怒情绪"

    def test_negation_guard_cases_still_not_crisis(self):
        """不变量(⛔ 否定守卫):表述求生的话不得被当成危机,故其描述也不该是空。"""
        for text in ("我不想死，我还想好好活着", "我不会自杀的，你放心"):
            c = classify(text)
            assert c["is_user_dangerous"] is False, text
            assert c["description"] != "", text
