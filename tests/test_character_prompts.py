# -*- coding: utf-8 -*-
"""「活提示词」机制测试:角色卡可覆盖 + 代码默认无角色专名 + 安全项不可覆盖。

对应 PET_POLICY.md 第三节。三条判定标准各有一组测试:
  1. **可被角色卡覆盖** —— 卡里写什么键就覆盖什么键;
  2. **代码默认里不得出现角色专名** —— 用词表扫描钉死;
  3. **代码默认只是脚手架** —— 卡没写全时仍能产出可用提示词。

另加一组"安全项不可覆盖"(方针第三节例外条款)与一组"旧硬编码已彻底移除"的守卫测试。
"""
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from conftest import TEST_CARD_NAME

from gzhu_cup2.agent.pet import narrative as pnarrative
from gzhu_cup2.agent.pet.character import PROMPT_DEFAULTS, loader as ploader, prompts as pprompts
from gzhu_cup2.agent.pet.character import schema as pschema
from gzhu_cup2.agent.prompts import care_prompts

# 必须不出现在「代码默认」里的角色专名(换卡后这些名字不该有任何残留)
CHARACTER_NAMES = ("团团", "Sayori", "莎世理", "Monika", "Yuri", "Natsuki")


def _use_overrides(monkeypatch, overrides: dict, name: str = "测试角色"):
    """把当前启用卡替换成一张带 prompts 覆盖的内存卡。"""
    card = pschema.parse_card({
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {
            "name": name,
            "description": f"你是{name}。",
            "extensions": {"companion": {"prompts": overrides}},
        },
    })
    monkeypatch.setattr(ploader, "resolve_active",
                        lambda **kw: ploader.ResolvedCard(card, "card", "inline", ""))
    return card


# ============================ 1. 卡可覆盖 ============================
class TestCardOverrides:
    def test_intent_can_be_overridden(self, monkeypatch):
        _use_overrides(monkeypatch, {"intents": {"checkin": "SENTINEL_INTENT_1a2b"}})
        msgs = care_prompts.build_care_llm_prompt("checkin")
        assert "SENTINEL_INTENT_1a2b" in msgs[1].content

    def test_unoverridden_keys_keep_defaults(self, monkeypatch):
        """只覆盖一个键,其它键必须仍走默认。"""
        _use_overrides(monkeypatch, {"intents": {"checkin": "SENTINEL_INTENT_1a2b"}})
        msgs = care_prompts.build_care_llm_prompt("soothe")
        assert "低落" in msgs[1].content
        assert "SENTINEL_INTENT_1a2b" not in msgs[1].content

    def test_probe_example_can_be_overridden(self, monkeypatch):
        _use_overrides(monkeypatch,
                       {"probe_examples": {"phq_sleep": "SENTINEL_PROBE_3c4d"}})
        ctx = care_prompts.probe_context_text("phq_sleep")
        assert "SENTINEL_PROBE_3c4d" in ctx

    def test_tone_can_be_overridden(self, monkeypatch):
        _use_overrides(monkeypatch, {"tone": {"gentle": "SENTINEL_TONE_5e6f"}})
        from gzhu_cup2.agent.care import mode as cmode
        assert cmode.tone_hint("gentle") == "SENTINEL_TONE_5e6f"

    def test_warm_observation_can_be_overridden(self, monkeypatch):
        _use_overrides(monkeypatch,
                       {"observations": {"mood_down": "SENTINEL_OBS_7a8b"}})
        msgs = care_prompts.build_care_llm_prompt(
            "warm", context={"mood_down": True})
        assert "SENTINEL_OBS_7a8b" in msgs[1].content

    def test_output_tail_can_be_overridden(self, monkeypatch):
        _use_overrides(monkeypatch, {"output_tail": "SENTINEL_TAIL_9c0d"})
        msgs = care_prompts.build_care_llm_prompt("checkin")
        assert "SENTINEL_TAIL_9c0d" in msgs[1].content

    def test_narrative_instruction_can_be_overridden(self, monkeypatch):
        _use_overrides(monkeypatch,
                       {"narrative": {"instruction": "SENTINEL_NARR_1e2f {{persona}}"}})
        captured = _capture_narrative(monkeypatch)
        pnarrative._generate_llm_text("")
        assert "SENTINEL_NARR_1e2f" in captured["human"]

    def test_override_reported_for_debugging(self, monkeypatch):
        card = _use_overrides(monkeypatch, {"intents": {"checkin": "X"}})
        assert pprompts.has_override(card, "intents", "checkin") is True
        assert pprompts.has_override(card, "intents", "soothe") is False
        assert "intents" in pprompts.describe_overrides(card)


# ============================ 2. 安全项不可覆盖 ============================
class TestSafetyNotOverridable:
    def test_safety_tail_cannot_be_overridden(self, monkeypatch):
        """角色卡写了 safety_tail 也必须被代码版本强制替换回去。"""
        _use_overrides(monkeypatch, {"safety_tail": "SENTINEL_BYPASS_3a4b"})
        msgs = care_prompts.build_care_llm_prompt("checkin")
        assert "SENTINEL_BYPASS_3a4b" not in msgs[0].content
        assert PROMPT_DEFAULTS["safety_tail"] in msgs[0].content

    def test_safety_tail_present_without_any_override(self):
        msgs = care_prompts.build_care_llm_prompt("checkin")
        assert PROMPT_DEFAULTS["safety_tail"] in msgs[0].content

    def test_safety_tail_present_in_narrative(self, monkeypatch):
        captured = _capture_narrative(monkeypatch)
        _use_overrides(monkeypatch, {"safety_tail": "SENTINEL_BYPASS_5c6d"})
        pnarrative._generate_llm_text("")
        assert "SENTINEL_BYPASS_5c6d" not in captured["system"]
        assert PROMPT_DEFAULTS["safety_tail"] in captured["system"]

    def test_forbidden_filter_is_still_hardcoded(self):
        """确定性禁词过滤必须留在代码里(不是卡可配置项)。"""
        from gzhu_cup2.agent.care import content as ccontent
        assert ccontent._FORBIDDEN_RE.search("风险等级是 high")
        assert "forbidden" not in PROMPT_DEFAULTS


# ============================ 3. 代码默认不得含角色专名 ============================
class TestDefaultsHaveNoCharacterNames:
    def test_defaults_carry_no_proper_nouns(self):
        """方针第三节第 2 条:代码默认里不得出现角色专名,一律用宏。"""
        blob = json.dumps(PROMPT_DEFAULTS, ensure_ascii=False)
        for name in CHARACTER_NAMES:
            assert name not in blob, f"代码默认里出现了角色专名:{name}"

    def test_defaults_use_macros(self):
        blob = json.dumps(PROMPT_DEFAULTS, ensure_ascii=False)
        assert "{{char}}" in blob


# ============================ 渲染结果带上了当前角色 ============================
@pytest.mark.usefixtures("test_card")
class TestRenderedWithActiveCard:
    """装上一张卡(conftest 的 test_card,真实落盘)后,主动关怀与叙事的提示词里
    应当出现该角色 —— 仓库不带预设卡,所以卡名由夹具决定。"""

    def test_proactive_prompt_uses_active_card(self):
        msgs = care_prompts.build_care_llm_prompt("checkin")
        system, human = msgs[0].content, msgs[1].content
        assert TEST_CARD_NAME in system and TEST_CARD_NAME in human
        for name in ("团团",):
            assert name not in system and name not in human

    def test_proactive_system_contains_full_persona(self):
        system = care_prompts.build_care_llm_prompt("checkin")[0].content
        assert "【核心人格】" in system

    def test_narrative_prompt_uses_active_card(self, monkeypatch):
        captured = _capture_narrative(monkeypatch)
        pnarrative._generate_llm_text("")
        assert TEST_CARD_NAME in captured["system"]
        assert TEST_CARD_NAME in captured["human"]
        assert "团团" not in captured["system"] + captured["human"]
        # 旧世界书的字段不再出现在叙事提示词里
        assert "世界设定" not in captured["human"]

    def test_narrative_injects_refs_when_present(self, monkeypatch):
        captured = _capture_narrative(monkeypatch)
        pnarrative._generate_llm_text("你上次说过:「在学吉他」")
        assert "在学吉他" in captured["human"]


# ============================ 旧硬编码已移除(守卫) ============================
class TestLegacyHardcodingRemoved:
    def test_narrative_template_removed(self):
        assert not hasattr(pnarrative, "_template_text")

    def test_content_layer_holds_no_phrasing(self):
        from gzhu_cup2.agent.care import content as ccontent
        assert not hasattr(ccontent, "_PROBE_EXAMPLES")

    def test_legacy_cards_module_removed(self):
        with pytest.raises(ImportError):
            import gzhu_cup2.agent.pet.cards  # noqa: F401

    def test_legacy_card_files_removed(self):
        root = Path(__file__).resolve().parent.parent / "src" / "gzhu_cup2" / "agent" / "pet"
        assert not (root / "cards.py").exists()
        assert not (root / "cards").exists()

    def test_care_prompts_has_no_hardcoded_intent_table(self):
        assert not hasattr(care_prompts, "_KIND_INSTRUCTION")


# ============================ 辅助 ============================
def _capture_narrative(monkeypatch):
    """把叙事生成里的 LLM 换成抓取提示词的假引擎。"""
    captured = {}

    class _FakeLLM:
        def invoke(self, messages, **kwargs):
            captured["messages"] = messages
            captured["system"] = messages[0].content
            captured["human"] = messages[1].content
            return AIMessage(content="今天晒了太阳。")

    import gzhu_cup2.agent.core.models.LLM_base as llm_base
    monkeypatch.setattr(llm_base, "get_llm", lambda **kw: _FakeLLM())
    return captured
