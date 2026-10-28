# -*- coding: utf-8 -*-
"""打扰等级的 HTTP 层契约:/api/pet/mode 的形状与「部分更新」语义。

与 test_pet_mode.py 的分工:那边测纯函数、状态层与 care 门控;这里只测**接口契约**
(部分更新、清除覆盖、非法档位回落、字段不被误改)。两者都不触网、不起调度线程。
"""
import pytest


@pytest.fixture
def client(pet_state_file, monkeypatch):
    from fastapi.testclient import TestClient
    import gzhu_cup2.entrypoints.api as server_mod
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.web import engine, sessions

    # 关掉夜间自动休眠:接口层读真实时钟,默认窗口 23:00–08:00 会在夜里把档位解析成
    # asleep/auto —— 本模块只测「部分更新」语义,不关的话断言随墙上时间红绿
    # (实测 23:37 必红、白天全绿)。夜间窗口键本身仍断言真实默认值。
    monkeypatch.setattr(cfg, "PET_NIGHT_SLEEP_ENABLED", False)

    sessions.reset()
    engine.reset()
    return TestClient(server_mod.app)


class TestPetModeApi:
    def test_default_shape(self, client):
        st = client.get("/api/pet/mode").json()
        assert st["level"] == "active" and st["source"] == "default"
        # 前端要靠这两个字段显示「现在是几档、夜间窗口是什么」
        assert st["choices"] == ["active", "quiet", "dnd"]
        assert st["night_window"] == [23, 8]
        assert st["level"] in st["levels"]

    def test_patch_default_level(self, client):
        r = client.patch("/api/pet/mode", json={"default_level": "quiet"})
        assert r.status_code == 200
        assert r.json()["default_level"] == "quiet"
        assert client.get("/api/pet/mode").json()["level"] == "quiet"

    def test_patch_manual_with_minutes(self, client):
        st = client.patch(
            "/api/pet/mode", json={"manual_level": "dnd", "manual_minutes": 60}
        ).json()
        assert st["level"] == "dnd" and st["source"] == "manual"
        assert st["until"], "带 minutes 的临时覆盖必须有到期时间(否则就变成永久了)"

    def test_manual_without_minutes_is_open_ended(self, client):
        st = client.patch("/api/pet/mode", json={"manual_level": "quiet"}).json()
        assert st["source"] == "manual" and st["until"] is None

    def test_clear_manual_with_empty_string(self, client):
        client.patch("/api/pet/mode", json={"manual_level": "dnd"})
        st = client.patch("/api/pet/mode", json={"manual_level": ""}).json()
        assert st["source"] == "default" and st["manual_level"] is None

    def test_unknown_level_falls_back_to_default(self, client):
        st = client.patch("/api/pet/mode", json={"default_level": "nonsense"}).json()
        assert st["default_level"] == "active"

    def test_partial_update_leaves_other_fields_alone(self, client):
        client.patch("/api/pet/mode", json={"default_level": "quiet"})
        st = client.patch("/api/pet/mode", json={"night_sleep": False}).json()
        assert st["default_level"] == "quiet", "未提供的字段必须不动"
        assert st["night_sleep"] is False

    def test_empty_patch_changes_nothing(self, client):
        before = client.get("/api/pet/mode").json()
        after = client.patch("/api/pet/mode", json={}).json()
        assert before["level"] == after["level"]
        assert before["default_level"] == after["default_level"]

    def test_asleep_is_not_manually_settable(self, client):
        """休眠只由夜间规则进入(裁定 ①):手动传 asleep 会被规范化掉。

        这里断言的是「不会真把用户置成休眠档」——具体回落成哪一档由 normalize 决定,
        关键是**结果不等于 asleep**,否则快捷键/接口就成了绕过裁定的后门。
        """
        st = client.patch("/api/pet/mode", json={"manual_level": "asleep"}).json()
        assert st["level"] != "asleep"
