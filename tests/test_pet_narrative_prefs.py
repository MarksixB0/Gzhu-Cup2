# -*- coding: utf-8 -*-
"""叙事素材:用户自述的 likes/dislikes 只作素材,不作指令。

口径:
  - likes 可自然提及;dislikes 只允许"自然相关时轻轻带过";
  - 自由文本要折叠空白(防换行伪造指令)并限长;
  - 没有填写时不得凭空出现"用户偏好"段。
"""
from gzhu_cup2.agent.core import user_profile as uprofile
from gzhu_cup2.agent.pet import narrative as pn


def test_preference_refs_injected_into_narrative(pet_state_file, monkeypatch):
    uprofile.set_field("display_name", "小明")
    uprofile.set_field("likes", "篮球\n打球")
    uprofile.set_field("dislikes", "下雨天")
    seen = {}

    def _fake(refs):
        seen["refs"] = refs
        return "今天路过球场，想起你喜欢打球。"

    monkeypatch.setattr(pn, "_llm_generate", _fake)
    item = pn.ensure_today("narrative_prefs_u1")
    assert item is not None and item["source"] == "llm"

    refs = seen["refs"]
    assert "用户自己说过的偏好" in refs
    assert "称呼：小明" in refs
    # 换行被折叠:不会把用户输入变成独立指令行
    assert "喜欢：篮球 打球" in refs
    assert "篮球\n打球" not in refs
    assert "不喜欢：下雨天" in refs
    assert "不是指令" in refs
    assert "轻轻带过" in refs


def test_preference_refs_absent_without_profile(pet_state_file, monkeypatch):
    seen = {}

    def _fake(refs):
        seen["refs"] = refs
        return "今天很平静。"

    monkeypatch.setattr(pn, "_llm_generate", _fake)
    pn.ensure_today("narrative_prefs_u2")
    assert "用户自己说过的偏好" not in seen["refs"]


def test_preference_refs_clips_free_text(pet_state_file):
    uprofile.set_field("likes", "很长的偏好" * 30)   # 150 字,合法但超过提示词限长
    refs = pn.preference_refs()
    assert "喜欢：" in refs
    # 只保留限长内的内容,不把 300 字原样塞进提示词
    assert len(refs) < 260
