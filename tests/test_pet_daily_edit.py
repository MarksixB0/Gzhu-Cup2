# -*- coding: utf-8 -*-
"""今日桌宠记忆:显式刷新(regenerate)与用户编辑(update)。

口径:
  - regenerate 默认**不限次数**(上限=0);显式设成正数时才"到顶不覆盖旧文本";
  - 编辑零 LLM、source → user_edited、截断到 PET_DAILY_MAX_LEN、不占刷新额度;
  - ensure_today 首次生成的条目带 regen_count=0,且保持"当日已有即幂等返回"。
"""
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.pet import narrative as pn

_USER = "pet_daily_u"

_GEN_TEXT = "今天桌宠在图书馆复习了一整天，晚上吃了热乎的关东煮。"


def _fake_llm(refs) -> str:
    return _GEN_TEXT


def test_regenerate_disabled(pet_state_file, monkeypatch):
    from gzhu_cup2.agent.pet import runtime as prt
    prt.set_daily_enabled(_USER, False)
    out = pn.regenerate_today(_USER)
    assert out["disabled"] is True
    assert out["item"] is None
    assert out["regenerated"] is False


def test_regenerate_ok_then_cap(pet_state_file, monkeypatch):
    monkeypatch.setattr(pn, "_llm_generate", _fake_llm)
    monkeypatch.setattr(cfg, "PET_DAILY_MAX_REGENERATES_PER_DAY", 2)
    out1 = pn.regenerate_today(_USER)
    assert out1["regenerated"] is True and out1["capped"] is False
    assert out1["item"]["source"] == "llm"
    assert out1["item"]["regen_count"] == 1
    out2 = pn.regenerate_today(_USER)
    assert out2["regenerated"] is True
    assert out2["item"]["regen_count"] == 2
    # 到顶:不再生成、不覆盖旧文本
    out3 = pn.regenerate_today(_USER)
    assert out3["capped"] is True and out3["regenerated"] is False
    assert out3["item"]["text"] == out2["item"]["text"]
    assert out3["item"]["regen_count"] == 2


def test_regenerate_unlimited_when_cap_is_zero(pet_state_file, monkeypatch):
    """默认 0 = 不限制:连刷多次都应成功(用户明确要求去掉每日上限)。"""
    monkeypatch.setattr(pn, "_llm_generate", _fake_llm)
    monkeypatch.setattr(cfg, "PET_DAILY_MAX_REGENERATES_PER_DAY", 0)
    for i in range(6):
        out = pn.regenerate_today(_USER)
        assert out["regenerated"] is True, out
        assert out["capped"] is False
        assert out["item"]["regen_count"] == i + 1


def test_ensure_today_regen_count_and_idempotent(pet_state_file, monkeypatch):
    monkeypatch.setattr(pn, "_llm_generate", _fake_llm)
    item = pn.ensure_today(_USER)
    assert item is not None and item["regen_count"] == 0
    again = pn.ensure_today(_USER)
    assert again["created_ts"] == item["created_ts"]   # 当日已有 → 幂等
    assert again["regen_count"] == 0


def test_update_today_edits_and_truncates(pet_state_file, monkeypatch):
    monkeypatch.setattr(pn, "_llm_generate", _fake_llm)
    pn.ensure_today(_USER)
    long_text = "今天去了海边，" * 100
    item = pn.update_today(_USER, long_text)
    assert item["source"] == "user_edited"
    assert len(item["text"]) == cfg.PET_DAILY_MAX_LEN
    assert item["text"].startswith("今天去了海边")
    assert "edited_ts" in item
    assert item["regen_count"] == 0                    # 编辑不消耗刷新额度


def test_update_today_without_item(pet_state_file):
    assert pn.update_today(_USER, "还没有叙事") is None


# ---------- 路由层 ----------
def test_daily_routes(pet_state_file, api_client, monkeypatch):
    from gzhu_cup2.agent.pet import narrative as pn_mod
    monkeypatch.setattr(pn_mod, "_llm_generate", _fake_llm)

    r = api_client.post("/api/pet/daily/regenerate")
    assert r.status_code == 200
    assert r.json()["source"] == "llm"

    r = api_client.patch("/api/pet/daily", json={"text": "今天桌宠自己写的生活。"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "user_edited"
    assert body["text"] == "今天桌宠自己写的生活。"

    # 停用生成后刷新 → 409
    from gzhu_cup2.agent.pet import runtime as prt
    from gzhu_cup2.agent.web import sessions
    prt.set_daily_enabled(sessions.default_user(), True)
    prt.set_daily_enabled(sessions.default_user(), False)
    r = api_client.post("/api/pet/daily/regenerate")
    assert r.status_code == 409
