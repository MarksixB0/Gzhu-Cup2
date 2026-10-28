# -*- coding: utf-8 -*-
"""1.3.2 的向后兼容:旧状态文件 / 旧留痕记录 / 旧用户档案都要能直接读。

本文件只锁「旧数据不被新字段卡住」,不重复测新功能本身:
  - 旧 ``care_state.json`` 没有 ``last_profile_like_ts``;
  - 旧留痕记录没有 ``fts_applied / candidates / intent_topics``;
  - 旧 ``user_profile.json`` 只有称呼、没有 likes/dislikes。

升级路径上这三类文件都是**已存在的本地数据**,不兼容就等于用户升级后崩溃或
偏好被清空 —— 所以用回归钉住,而不是靠"新字段都有默认值"的口头约定。
"""
import json

from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.core import user_profile as uprofile
from gzhu_cup2.agent.long_memory import store as mstore
from gzhu_cup2.agent.long_memory import trace as mtrace
from gzhu_cup2.agent.pet import narrative as pn

_OLD_TS = 1792000000.0          # 2026-10-14 前后的任意历史时刻
_NOW = _OLD_TS + 6 * 3600.0


def _write_old_care_state(path: str, user: str) -> None:
    """1.3.1 及更早的 care 状态:没有 last_profile_like_ts。"""
    entry = {
        "enabled": True, "date": "2026-10-14", "date_count": 2,
        "last_proactive_ts": _OLD_TS, "last_petlife_ts": None,
        "dims": {"sleep": _OLD_TS},
        "last_delivered_ts": _OLD_TS, "unanswered_streak": 1,
        "last_farewell_ts": None,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"users": {user: entry}, "pending": []}, f, ensure_ascii=False)


def test_old_care_state_reads_without_new_key(care_state_file):
    user = "compat_care"
    _write_old_care_state(care_state_file, user)

    c = cruntime.counters(user)
    # 新字段按 None 兜底,不是 KeyError
    assert c["last_profile_like_ts"] is None
    # 旧字段必须原样保留:升级不该把用户的关怀节奏清空
    assert c["last_proactive_ts"] == _OLD_TS
    assert cruntime.dims_map(user) == {"sleep": _OLD_TS}


def test_mark_sent_adds_new_key_and_keeps_old_ones(care_state_file):
    user = "compat_care_mark"
    _write_old_care_state(care_state_file, user)

    cruntime.mark_sent(user, "recall", "篮球最近有打吗？",
                       topic="篮球", profile_like=True, now=_NOW)

    c = cruntime.counters(user)
    assert c["last_profile_like_ts"] == _NOW
    # 既有维度记录不被这次写入覆盖
    assert cruntime.dims_map(user)["sleep"] == _OLD_TS


def test_old_trace_record_is_returned_as_is():
    """旧记录缺新字段时,读侧不得报错,也不得被补写成假值。"""
    user = "compat_trace"
    old = {
        "ts": _OLD_TS, "purpose": "reply", "query": "考研", "budget": 480,
        "chars": 120, "fts_engaged": False, "fts_hits": 0,
        "n_considered": 3, "n_selected": 1,
        "selected": [{"lane": "fact", "base": 0.5, "rel": 0.9, "text": "考研"}],
        "episode_keys": [], "drop_reasons": {"budget": 2},
    }
    mstore.put_item(mtrace.ns(user), "%013d-reply-legacy" % int(_OLD_TS * 1000), old)

    rows = mtrace.list_recent(user)
    assert len(rows) == 1
    assert rows[0]["query"] == "考研"
    assert rows[0]["fts_hits"] == 0
    # 缺键保持缺失:前端按 undefined 处理,而不是读到一个伪造的 False
    assert "fts_applied" not in rows[0]
    assert "candidates" not in rows[0]


def test_record_tolerates_sel_without_fts_keys():
    """旧调用方(或旧 select_context 形态)只给 facts/explain 也必须能落痕。"""
    user = "compat_trace_min"
    mtrace.record(user, "reply", {"query": "q", "budget": 480,
                                 "chars": 0, "explain": []})

    rows = mtrace.list_recent(user)
    assert len(rows) == 1
    assert rows[0]["fts_engaged"] is False
    assert rows[0]["fts_applied"] is False
    assert rows[0]["candidates"] == {}
    assert rows[0]["intent_topics"] == []


def test_old_profile_without_likes_yields_no_preference_block(pet_state_file, monkeypatch):
    """旧 user_profile.json 只有称呼 → 不生成偏好段,叙事仍走原路径。"""
    path = uprofile._STATE.path
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"display_name": "小林", "onboarding": {"seen": 3}}, f,
                  ensure_ascii=False)

    assert uprofile.get_field("likes") == ""
    assert pn.preference_refs() == ""

    seen = {}

    def _fake(refs):
        seen["refs"] = refs
        return "今天天气不错。"

    monkeypatch.setattr(pn, "_llm_generate", _fake)
    item = pn.ensure_today("compat_profile_u")
    assert item is not None
    assert seen["refs"] == ""      # 没有偏好素材时与旧版本行为一致
