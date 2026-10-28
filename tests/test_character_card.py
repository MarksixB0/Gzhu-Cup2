# -*- coding: utf-8 -*-
"""角色卡(pet.character)离线测试:结构校验 / 加载与回落 / 渲染优先级。

不触网、不依赖 LLM。重点钉死三条语义(用户裁定 2026-09):
  1. **角色卡 > config 默认值**:有卡时长度/风格规则一律以卡为准;
  2. **没有角色卡就完整回落 config**,且不残留任何人格文本(最纯净的 AI);
  3. **卡损坏必须留下可诊断的 reason**,不得静默当成"成功用上了卡"。

⚠ 仓库里**没有任何预设角色卡**(人格由用户自己在界面上创建),所以本文件要"用上一张卡"
的地方一律用 conftest 现造的测试卡(`test_card` 夹具 / `write_test_card`),
不再依赖磁盘上恰好存在某张卡。

注意:本文件刻意**不用 pytest 的 tmp_path** —— 本机 Python 的临时目录不可用
(仓库里残留的 .ptmp* / pytest-cache-files-* 目录同样 Permission denied),
改用测试自管的 tests/_card_tmp 目录。
"""
import json
import os
import shutil

import pytest

from conftest import TEST_CARD_NAME, TEST_USER_NAME

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.pet import character as pchar
from gzhu_cup2.agent.pet.character import loader as ploader
from gzhu_cup2.agent.pet.character import renderer as prenderer
from gzhu_cup2.agent.pet.character import schema as pschema

FALLBACK_RULE = "回复请控制在3-5句、约50-150字。"

MIN_CARD = {
    "spec": "chara_card_v2",
    "spec_version": "2.0",
    "data": {"name": "测试角色", "description": "你是测试角色。"},
}

_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_card_tmp")


@pytest.fixture
def cards_dir():
    """测试自管的临时卡目录(用完即删)。"""
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
    os.makedirs(_TMP_DIR, exist_ok=True)
    yield _TMP_DIR
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clear_card_cache():
    """每个用例前后清缓存,避免用例间互相污染。"""
    pchar.clear_cache()
    yield
    pchar.clear_cache()


def _write_card(path, raw):
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    return str(path)


def _write_raw(path, text):
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as f:
        f.write(text)
    return str(path)


def _card_at(cards_dir, name="c1", raw=None):
    """在临时目录写一张卡(默认即 MIN_CARD)。"""
    return _write_card(os.path.join(cards_dir, f"{name}.json"),
                       raw if raw is not None else MIN_CARD)


# ============================ 结构校验 ============================
class TestSchema:
    def test_minimal_card_parses(self):
        card = pschema.parse_card(MIN_CARD)
        assert card.spec == pschema.CARD_SPEC
        assert card.data.name == "测试角色"
        assert card.data.has_persona()

    def test_spec_must_be_v2(self):
        with pytest.raises(pschema.CardError, match="spec"):
            pschema.parse_card({**MIN_CARD, "spec": "chara_card_v1"})

    def test_missing_data_rejected(self):
        with pytest.raises(pschema.CardError, match="data"):
            pschema.parse_card({"spec": "chara_card_v2"})

    def test_empty_name_rejected(self):
        raw = {"spec": "chara_card_v2", "spec_version": "2.0",
               "data": {"name": "", "description": "x"}}
        with pytest.raises(pschema.CardError, match="校验失败"):
            pschema.parse_card(raw)

    def test_unknown_fields_are_preserved(self):
        """V2 规范:未知键值不得丢弃。"""
        raw = {"spec": "chara_card_v2", "spec_version": "2.0",
               "data": {**MIN_CARD["data"], "future_field": {"a": 1}},
               "top_extra": True}
        card = pschema.parse_card(raw)
        assert card.data.future_field == {"a": 1}
        assert card.top_extra is True

    def test_non_prompt_field_cannot_be_read_as_prompt(self):
        """creator_notes 必须无法通过 prompt_text 取到(防泄漏的结构性保证)。"""
        card = pschema.parse_card(MIN_CARD)
        with pytest.raises(pschema.CardError):
            card.data.prompt_text("creator_notes")
        for f in pschema.NON_PROMPT_FIELDS:
            assert f not in pschema.PROMPT_FIELDS

    def test_persona_absent_when_no_text(self):
        raw = {"spec": "chara_card_v2", "spec_version": "2.0",
               "data": {"name": "空壳"}}
        assert pschema.parse_card(raw).data.has_persona() is False


# ============================ 加载与回落 ============================
class TestLoader:
    def test_repo_ships_no_character_card(self):
        """出厂状态:仓库里一张卡都没有(人格只由用户创建)。"""
        assert pchar.list_cards() == []

    def test_list_cards_sees_json_files(self, cards_dir, monkeypatch):
        """目录扫描:一卡一文件,按名排序;非 .json 与隐藏文件不算卡。"""
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        _card_at(cards_dir, "b")
        _card_at(cards_dir, "a")
        _write_raw(os.path.join(cards_dir, "notes.txt"), "不是卡")
        _write_raw(os.path.join(cards_dir, ".hidden.json"), "{}")
        assert pchar.list_cards() == ["a", "b"]

    def test_load_card_reads_what_is_on_disk(self, cards_dir, monkeypatch):
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        _card_at(cards_dir, "c1")
        card = pchar.load_card("c1", strict=True)
        assert card.data.name == "测试角色"
        assert card.data.has_persona()
        # 安全块按用户裁定不写进卡:system_prompt 必须为空
        assert card.data.system_prompt == ""

    def test_missing_card_raises_when_strict(self, cards_dir, monkeypatch):
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        with pytest.raises(pschema.CardError, match="不存在"):
            pchar.load_card("nope", strict=True)

    def test_missing_card_returns_none_when_lenient(self, cards_dir, monkeypatch):
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        assert pchar.load_card("nope", strict=False) is None

    def test_missing_dir_is_not_an_error(self, cards_dir, monkeypatch):
        """目录不存在 = 合法的"未搭载角色卡",不是异常。"""
        monkeypatch.setattr(cfg, "PET_CARD_DIR", os.path.join(cards_dir, "nope"))
        assert pchar.list_cards() == []

    def test_resolve_empty_name_falls_back(self):
        r = pchar.resolve_card("")
        assert r.active is False and r.source == "fallback"
        assert r.card is None and r.reason

    def test_resolve_broken_card_falls_back_with_reason(self, cards_dir, monkeypatch):
        """坏卡必须回落,但 reason 要能说明坏在哪(不许静默成功)。"""
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        _write_card(os.path.join(cards_dir, "broken.json"),
                    {"spec": "wrong", "data": {}})
        r = pchar.resolve_card("broken")
        assert r.active is False and r.source == "fallback"
        assert "broken" in r.requested
        assert "spec" in r.reason

    def test_resolve_not_json_falls_back_with_reason(self, cards_dir, monkeypatch):
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        _write_raw(os.path.join(cards_dir, "bad.json"), "{ not json")
        r = pchar.resolve_card("bad")
        assert r.active is False and "JSON" in r.reason

    def test_card_without_persona_falls_back(self, cards_dir, monkeypatch):
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        _write_card(os.path.join(cards_dir, "hollow.json"),
                    {"spec": "chara_card_v2", "spec_version": "2.0",
                     "data": {"name": "空壳"}})
        r = pchar.resolve_card("hollow")
        assert r.active is False and "人格文本" in r.reason

    def test_resolve_active_uses_config_switch(self, cards_dir, monkeypatch):
        """可切换:改 config 的卡名即换卡;置空即不搭载角色卡。"""
        _card_at(cards_dir, "a", {**MIN_CARD, "data": {"name": "甲", "description": "甲人格"}})
        _card_at(cards_dir, "b", {**MIN_CARD, "data": {"name": "乙", "description": "乙人格"}})
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)

        monkeypatch.setattr(cfg, "PET_ACTIVE_CARD", "a")
        assert pchar.resolve_active().card.data.name == "甲"
        monkeypatch.setattr(cfg, "PET_ACTIVE_CARD", "b")
        assert pchar.resolve_active().card.data.name == "乙"
        monkeypatch.setattr(cfg, "PET_ACTIVE_CARD", "")
        assert pchar.resolve_active().active is False

    def test_runtime_selection_beats_config_default(self, cards_dir, monkeypatch):
        """运行时选择(active_card.json)优先于 config 出厂默认 ——
        这正是"在界面上点『使用』"能生效的原因。"""
        _card_at(cards_dir, "a", {**MIN_CARD, "data": {"name": "甲", "description": "甲人格"}})
        _card_at(cards_dir, "b", {**MIN_CARD, "data": {"name": "乙", "description": "乙人格"}})
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        monkeypatch.setattr(cfg, "PET_ACTIVE_CARD", "a")

        pchar.set_active_card("b")
        assert pchar.active_card_name() == "b"
        assert pchar.resolve_active().card.data.name == "乙"

        pchar.set_active_card("")     # 空串 = 不搭载角色卡(覆盖掉 config 默认)
        assert pchar.active_card_name() == ""
        assert pchar.resolve_active().active is False

    def test_set_active_unknown_card_rejected(self, cards_dir, monkeypatch):
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        with pytest.raises(pschema.CardError, match="不存在"):
            pchar.set_active_card("查无此卡")

    def test_cache_invalidated_when_file_changes(self, cards_dir, monkeypatch):
        """热替换:改了文件内容,下一次加载必须是新的(不重启)。"""
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        p = os.path.join(cards_dir, "hot.json")
        _write_card(p, {**MIN_CARD, "data": {"name": "旧名", "description": "旧人格"}})
        assert pchar.load_card("hot").data.name == "旧名"
        _write_card(p, {**MIN_CARD, "data": {"name": "新名字更长", "description": "新人格内容"}})
        assert pchar.load_card("hot").data.name == "新名字更长"

    def test_load_returns_copy_not_cache(self, cards_dir, monkeypatch):
        monkeypatch.setattr(cfg, "PET_CARD_DIR", cards_dir)
        _card_at(cards_dir, "copy")
        first = pchar.load_card("copy")
        first.data.name = "被改坏了"
        assert pchar.load_card("copy").data.name == "测试角色"

    def test_describe_reports_diagnostics(self):
        d = pchar.resolve_active().describe()
        assert d["source"] in ("card", "fallback")
        assert isinstance(d["available"], list)


# ============================ 卡的增删改 ============================
class TestCardCrud:
    """界面上的「新增 / 编辑 / 删除」对应的后端语义。"""

    def test_create_then_visible_in_list(self):
        pchar.create_card(name="新角色", description="你是新角色。", first_mes="你好。")
        assert pchar.list_cards() == ["新角色"]
        card = pchar.load_card("新角色", strict=True)
        assert card.data.description == "你是新角色。"
        assert card.data.first_mes == "你好。"

    def test_create_rejects_duplicate(self):
        pchar.create_card(name="重名", description="第一个")
        with pytest.raises(pschema.CardError, match="已经有一张"):
            pchar.create_card(name="重名", description="第二个")

    def test_create_rejects_empty_body(self):
        with pytest.raises(pschema.CardError, match="提示词正文不能为空"):
            pchar.create_card(name="空正文", description="   ")

    @pytest.mark.parametrize("bad", ["", "   ", "a/b", "a\\b", 'a:b', "a?b", "x" * 65])
    def test_create_rejects_unsafe_name(self, bad):
        """卡名不该有机会变成路径或非法文件名。"""
        with pytest.raises(pschema.CardError):
            pchar.create_card(name=bad, description="正文")

    def test_update_only_touches_given_fields(self):
        pchar.create_card(name="改我", description="原正文", first_mes="原开场白")
        pchar.update_card(name="改我", description="新正文")
        card = pchar.load_card("改我", strict=True)
        assert card.data.description == "新正文"
        assert card.data.first_mes == "原开场白"     # 没传的字段保持原样

    def test_update_rejects_empty_body(self):
        pchar.create_card(name="改我", description="原正文")
        with pytest.raises(pschema.CardError, match="提示词正文不能为空"):
            pchar.update_card(name="改我", description="  ")

    def test_update_preserves_unknown_fields(self):
        """V2 规范:改写不得丢掉本应用不认识的字段。"""
        pchar.create_card(name="带扩展", description="正文")
        path = os.path.join(pchar.card_dir(), "带扩展.json")
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        raw["data"]["custom_field"] = "SENTINEL_KEEP_1a2b"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        pchar.clear_cache()

        pchar.update_card(name="带扩展", description="改过的正文")
        assert pchar.load_card("带扩展").data.custom_field == "SENTINEL_KEEP_1a2b"

    def test_delete_removes_card(self):
        pchar.create_card(name="删我", description="正文")
        pchar.delete_card("删我")
        assert pchar.list_cards() == []
        with pytest.raises(pschema.CardError):
            pchar.load_card("删我", strict=True)

    def test_delete_missing_card_rejected(self):
        with pytest.raises(pschema.CardError, match="不存在"):
            pchar.delete_card("查无此卡")

    def test_delete_active_card_clears_selection(self):
        """删掉正在用的那张卡,启用选择必须一并清空 —— 不留悬空引用。"""
        pchar.create_card(name="在用", description="正文")
        pchar.set_active_card("在用")
        assert pchar.active_card_name() == "在用"

        pchar.delete_card("在用")
        assert pchar.active_card_name() == ""
        assert pchar.resolve_active().source == "fallback"


# ============================ 渲染与优先级 ============================
class TestRenderer:
    def test_card_wins_over_config_length_rule(self, test_card):
        """有卡:config 的长度规则被卡的说话风格取代。"""
        out = prenderer.render_persona(pchar.resolve_card(test_card),
                                       fallback_length_rule=FALLBACK_RULE)
        assert out.source == "card"
        assert FALLBACK_RULE not in out.post_history
        assert "交流风格" in out.post_history

    def test_no_card_falls_back_to_config_length_rule(self):
        """无卡:完整回落 config,拿到原样保留的默认值。"""
        out = prenderer.render_persona(pchar.resolve_card(""),
                                       fallback_length_rule=FALLBACK_RULE)
        assert out.active is False and out.source == "fallback"
        assert out.post_history == FALLBACK_RULE

    def test_no_card_leaves_no_persona_text(self):
        """无卡时不得残留任何人格文本(最纯净的 AI)。"""
        out = prenderer.render_persona(pchar.resolve_card(""),
                                       fallback_length_rule=FALLBACK_RULE)
        assert out.core == "" and out.mes_example == "" and out.card_system_prompt == ""
        assert out.char_name == "助手"

    def test_core_contains_persona_and_scenario(self, test_card):
        out = prenderer.render_persona(pchar.resolve_card(test_card),
                                       fallback_length_rule=FALLBACK_RULE)
        assert TEST_CARD_NAME in out.core
        assert "【场景】" in out.core
        assert "【核心人格】" in out.core

    def test_all_sections_survive_rendering(self, test_card):
        """卡里写的章节必须全部出现在渲染产物里(不许丢语义)。"""
        out = prenderer.render_persona(pchar.resolve_card(test_card),
                                       fallback_length_rule=FALLBACK_RULE)
        blob = out.core + out.post_history
        for sec in ("身份", "核心人格", "核心价值", f"与 {TEST_USER_NAME}", "行为模式",
                    "兴趣", "知识边界", "长期关系", "AI 行为", "交流风格",
                    "表达方式", "标点", "回复规则", "输出规则"):
            assert sec in blob, f"章节丢失: {sec}"

    def test_mes_example_macros_substituted(self, test_card):
        out = prenderer.render_persona(pchar.resolve_card(test_card),
                                       fallback_length_rule=FALLBACK_RULE)
        assert "{{user}}" not in out.mes_example
        assert "{{char}}" not in out.mes_example
        assert out.user_name in out.mes_example
        assert out.char_name in out.mes_example

    def test_user_name_priority(self, test_card, monkeypatch):
        # 1) 显式 override 最高
        assert prenderer.resolve_user_name(None, "小明") == "小明"
        # 2) config 次之
        monkeypatch.setattr(cfg, "PET_USER_DISPLAY_NAME", "配置名")
        assert prenderer.resolve_user_name(None) == "配置名"
        # 3) 再是卡内声明
        monkeypatch.setattr(cfg, "PET_USER_DISPLAY_NAME", "")
        card = pchar.resolve_card(test_card).card
        assert prenderer.resolve_user_name(card) == TEST_USER_NAME
        # 4) 都没有 -> 「你」
        assert prenderer.resolve_user_name(None) == "你"

    def test_card_system_prompt_returned_separately(self):
        """卡自带 system_prompt 不得被合并进 core(调用方须放在安全块之后)。"""
        raw = {**MIN_CARD, "data": {"name": "X", "description": "人格",
                                    "system_prompt": "卡的全局指令"}}
        card = pschema.parse_card(raw)
        r = ploader.ResolvedCard(card, "card", "x", "")
        out = prenderer.render_persona(r, fallback_length_rule=FALLBACK_RULE)
        assert out.card_system_prompt == "卡的全局指令"
        assert "卡的全局指令" not in out.core

    def test_greetings_rendered_only_with_card(self, test_card):
        assert prenderer.render_greetings(pchar.resolve_card("")) == []
        greetings = prenderer.render_greetings(pchar.resolve_card(test_card))
        assert len(greetings) >= 3  # first_mes + 2 alternate


# ============================ 泄漏防护 ============================
def _render_inline(data_patch, *, name="哨兵角色"):
    """用内联卡(不落盘)渲染,便于把哨兵串塞进任意字段。"""
    card = pschema.parse_card({
        "spec": "chara_card_v2", "spec_version": "2.0",
        "data": {"name": name, "description": "你是哨兵角色。", **data_patch},
    })
    resolved = ploader.ResolvedCard(card, "card", "inline", "")
    out = prenderer.render_persona(resolved, fallback_length_rule=FALLBACK_RULE)
    return out, out.core + out.post_history + out.mes_example + out.card_system_prompt


class TestNoLeak:
    """规范规定 creator_notes/tags/first_mes 不得进提示词。

    用哨兵串验证(不能拿真实 tag 直接做子串判断 —— 例如 tag「文学部」本来就
    合法地出现在 description 里,那不算泄漏;只有"整个字段被注入"才是)。
    """

    def test_creator_notes_never_rendered(self):
        _, blob = _render_inline({"creator_notes": "SENTINEL_NOTES_9f3a"})
        assert "SENTINEL_NOTES_9f3a" not in blob

    def test_tags_never_rendered(self):
        _, blob = _render_inline({"tags": ["SENTINEL_TAG_7b21"]})
        assert "SENTINEL_TAG_7b21" not in blob

    def test_greetings_never_rendered_into_prompt(self):
        """开场白属于对话内容,不是人格定义,不得进提示词。"""
        _, blob = _render_inline({"first_mes": "SENTINEL_GREET_1c4d",
                                  "alternate_greetings": ["SENTINEL_GREET_2e5f"]})
        assert "SENTINEL_GREET_1c4d" not in blob
        assert "SENTINEL_GREET_2e5f" not in blob

    def test_unknown_extension_never_rendered(self):
        """extensions 是附属数据,绝不参与提示词。"""
        _, blob = _render_inline({"extensions": {"x": "SENTINEL_EXT_3a6b"}})
        assert "SENTINEL_EXT_3a6b" not in blob
