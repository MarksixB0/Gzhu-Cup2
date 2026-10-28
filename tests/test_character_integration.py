# -*- coding: utf-8 -*-
"""角色卡 ↔ 前台提示词的集成测试(pet.character + prompts.prompts)。

钉死 S2 的核心契约(用户裁定 2026-09):
  1. **有角色卡**:网页端每轮对话的 system 段就是该角色的人格,说话风格以卡为准,
     本模块内置的 REPLY_LENGTH_RULE 不再注入;
  2. **无角色卡**:完整回落到内置纯助手设定(最纯净的 AI),长度规则原样恢复;
  3. **安全块永远在**:HARD_RULES 与危机热线不受角色卡影响,卡的 system_prompt
     只能被追加在安全块之后;
  4. **不注入 mes_example**:不额外引入"对话示例"式的硬编码文本。

不触网、不依赖 LLM:只构造与检查提示词文本。

⚠ 仓库里**没有预设角色卡**。需要"真的装着一张卡"的用例统一用 conftest 的
``test_card`` 夹具 —— 它会把测试卡落到隔离目录并启用,于是这里测的仍是**真实的
读盘链路**(而非 mock 掉 resolve_active),只是卡的内容由用例自己决定。
"""
import types

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from conftest import TEST_CARD_NAME

from gzhu_cup2.agent.pet.character import loader as ploader
from gzhu_cup2.agent.pet.character import schema as pschema
from gzhu_cup2.agent.prompts import prompts

HOTLINE = "12356"

# 测试卡的 mes_example 里的一句(见 conftest.TEST_CARD)。
# 用来验证"对话示例不进提示词"—— 换成别的卡也不会误判。
CARD_MES_EXAMPLE = "在的,随时可以开始。"


def _state(memory_context="【用户档案】\n- 用户叫小林", risk_level="low"):
    return {
        "messages": [
            HumanMessage(content="第一句"), AIMessage(content="嗯"),
            HumanMessage(content="第二句"), AIMessage(content="在的"),
            HumanMessage(content="第三句"),
        ],
        "process_input": "我今天有点累",
        "emotional_analysis": types.SimpleNamespace(emotion="frustrated", description="疲惫"),
        "risk_level": risk_level,
        "memory_context": memory_context,
        "crisis_handled": False,
    }


def _use_card(monkeypatch, data: dict):
    """把"当前启用卡"替换成一张内存卡(不落盘)。

    注意打补丁的位置:提示词集经 ``pet.character.loader.resolve_active()`` 解析角色卡,
    所以要 patch **loader 模块上**的函数,而不是包里的转发名。
    """
    card = pschema.parse_card({
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {"name": "测试角色", "description": "你是测试角色。", **data},
    })
    monkeypatch.setattr(ploader, "resolve_active",
                        lambda **kw: ploader.ResolvedCard(card, "card", "inline", ""))


def _no_card(monkeypatch):
    """模拟"未搭载角色卡"。"""
    monkeypatch.setattr(
        ploader, "resolve_active",
        lambda **kw: ploader.ResolvedCard(None, "fallback", "", "测试:未配置角色卡"))


def _gen(state=None):
    sys_msg, human_msg = prompts.build_generate_response_prompt(state or _state())
    return sys_msg.content, human_msg.content


def _crisis(state=None):
    sys_msg, human_msg = prompts.build_handle_crisis_prompt(
        state or _state(risk_level="crisis"))
    return sys_msg.content, human_msg.content


# ============================ 装着测试卡时真实生效 ============================
@pytest.mark.usefixtures("test_card")
class TestActiveCardReal:
    """不 mock 任何东西,直接验证"装上一张卡后前台提示词长什么样"。

    卡来自 conftest 的 test_card 夹具(落盘 + 启用),走的是真实读盘链路。
    """

    def test_persona_actually_lands_in_system(self):
        system, _ = _gen()
        assert TEST_CARD_NAME in system
        assert "【核心人格】" in system
        assert "【场景】" in system

    def test_default_role_is_displaced(self):
        """有卡时不得再出现内置的纯助手身份设定。"""
        system, _ = _gen()
        assert prompts.DEFAULT_ROLE not in system

    def test_card_displaces_config_length_rule(self):
        """角色卡优先于配置文件:S2 起长度规则由卡接管。"""
        _, human = _gen()
        assert prompts.REPLY_LENGTH_RULE not in human
        assert "【交流风格】" in human

    def test_style_constraints_sit_at_the_tail(self):
        """说话风格约束在用户输入之后(尾部锚定,影响最强)。"""
        _, human = _gen()
        assert human.index("【用户本轮输入") < human.index("【交流风格】")

    def test_persona_and_runtime_hint_are_separated(self):
        """人格段与动态语气指令之间必须有换行分隔。

        回归:S2 首版把 safety_instruction 直接 += 到人格段后面,导致
        scenario 结尾与"用户情绪比较稳定…"黏成同一句(实机渲染发现)。
        """
        system, _ = _gen()
        assert "\n用户情绪比较稳定，请正常对话。" in system

    def test_memory_layout_preserved_under_card(self):
        """接入角色卡不得破坏原有的记忆占比收敛布局。"""
        _, human = _gen()
        assert human.index("【最近对话背景】") < human.index("【用户背景记忆")
        assert human.index("【用户背景记忆") < human.index("【用户本轮输入")

    def test_mes_example_is_not_injected(self):
        """用户裁定:不注入对话示例(不增加此类硬编码文本)。"""
        system, human = _gen()
        blob = system + human
        assert CARD_MES_EXAMPLE not in blob   # 只在测试卡的 mes_example 里
        assert "<START>" not in blob

    def test_current_persona_reports_the_card(self):
        r = prompts.current_persona()
        assert r.active is True and r.source == "card"
        assert r.char_name == TEST_CARD_NAME


# ============================ 无角色卡:回落"最纯净的 AI" ============================
class TestNoCardFallback:
    def test_restores_pure_assistant_role(self, monkeypatch):
        _no_card(monkeypatch)
        system, _ = _gen()
        assert prompts.DEFAULT_ROLE in system
        assert TEST_CARD_NAME not in system
        assert "【核心人格】" not in system

    def test_restores_config_length_rule(self, monkeypatch):
        _no_card(monkeypatch)
        _, human = _gen()
        assert prompts.REPLY_LENGTH_RULE in human
        assert "【交流风格】" not in human

    def test_safety_rules_unaffected(self, monkeypatch):
        _no_card(monkeypatch)
        system, _ = _gen()
        assert prompts.HARD_RULES in system
        assert "你必须遵守的硬规则" in system


# ============================ 出厂状态:没有卡就走回落 ============================
class TestShipsWithoutCard:
    """仓库不带预设卡,所以"什么都不配"时前台必须是纯助手 —— 这条要能自动守住。"""

    def test_missing_card_yields_pure_assistant(self):
        system, human = _gen()
        assert prompts.DEFAULT_ROLE in system
        assert prompts.REPLY_LENGTH_RULE in human
        assert "【核心人格】" not in system
        assert "【交流风格】" not in human


# ============================ 安全块不可被卡覆盖 ============================
class TestSafetyNotOverridable:
    @pytest.mark.usefixtures("test_card")
    def test_hard_rules_always_present_with_card(self):
        system, _ = _gen()
        assert "你必须遵守的硬规则" in system
        assert prompts.HARD_RULES in system

    def test_hard_rules_always_present_without_card(self, monkeypatch):
        _no_card(monkeypatch)
        system, _ = _gen()
        assert prompts.HARD_RULES in system

    def test_card_system_prompt_appended_after_hard_rules(self, monkeypatch):
        """卡自带 system_prompt 只能追加在安全块之后,不能顶掉它。"""
        _use_card(monkeypatch, {"system_prompt": "SENTINEL_CARD_SYS_5d1e"})
        system, _ = _gen()
        i_rules = system.index("你必须遵守的硬规则")
        i_card = system.index("SENTINEL_CARD_SYS_5d1e")
        assert i_card > i_rules
        assert prompts.HARD_RULES in system


# ============================ 危机分支 ============================
class TestCrisisPrompt:
    @pytest.mark.usefixtures("test_card")
    def test_hotline_always_delivered(self):
        system, _ = _crisis()
        assert HOTLINE in system

    def test_hotline_delivered_without_card_too(self, monkeypatch):
        _no_card(monkeypatch)
        system, _ = _crisis()
        assert HOTLINE in system
        assert prompts.DEFAULT_ROLE in system

    @pytest.mark.usefixtures("test_card")
    def test_uses_card_persona_for_voice(self):
        """危机回复也要是同一个人在说话。"""
        system, _ = _crisis()
        assert TEST_CARD_NAME in system

    @pytest.mark.usefixtures("test_card")
    def test_does_not_inject_style_constraints(self):
        """刻意不注入卡的说话风格约束:与"完整给出热线"冲突,以信息传达为先。"""
        system, _ = _crisis()
        assert "【交流风格】" not in system
        assert "20 字以内" not in system

    @pytest.mark.usefixtures("test_card")
    def test_hard_rules_still_present(self):
        system, _ = _crisis()
        assert prompts.HARD_RULES in system


# ============================ 坏卡不拖垮请求 ============================
class TestBrokenCardIsSurvivable:
    def test_broken_card_still_produces_usable_prompt(self, monkeypatch):
        """卡坏掉时前台必须还能回话(回落纯助手),而不是整个请求 500。"""
        def _boom(**kw):
            return ploader.ResolvedCard(
                None, "fallback", "broken",
                "角色卡 'broken' 角色卡 spec 必须为 'chara_card_v2',实际为 'wrong'")
        monkeypatch.setattr(ploader, "resolve_active", _boom)

        system, human = _gen()
        assert prompts.DEFAULT_ROLE in system
        assert prompts.HARD_RULES in system
        assert prompts.REPLY_LENGTH_RULE in human
        assert human.strip()
