# -*- coding: utf-8 -*-
"""统一设置面(/api/settings)的 HTTP 契约。

它存在的意义是**收敛**:此前每加一个设置就要新增一条接口、前端也要多写一遍
三段式 load/save 代码。这里守三件事:
  - 聚合正确(一次拿到各域);
  - 部分更新只动传了的键;
  - **未知键明确报错**(静默忽略会让用户以为改了、其实没改)。
"""
import json
import os

import pytest



@pytest.fixture
def client(privacy_state_file, pet_state_file, care_state_file, monkeypatch):
    from fastapi.testclient import TestClient
    import gzhu_cup2.entrypoints.api as server_mod
    from gzhu_cup2.agent.config import config as cfg
    from gzhu_cup2.agent.web import engine, sessions

    # 关掉夜间自动休眠:默认窗口 23:00–08:00(`cfg.PET_NIGHT_SLEEP_*`),而本模块只关心
    # default_level / manual_level 的语义 —— 不关的话,晚上跑就把档位解析成 asleep/auto,
    # 断言随墙上时间红绿(实测 23:31 必红、白天全绿)。
    # 夜间规则本身由 test_pet_mode.py / test_pet_mode_api.py 注入固定时间覆盖。
    monkeypatch.setattr(cfg, "PET_NIGHT_SLEEP_ENABLED", False)

    sessions.reset()
    engine.reset()
    return TestClient(server_mod.app)


class TestSnapshot:
    def test_groups_present(self, client):
        st = client.get("/api/settings").json()
        for group in ("care", "pet", "mode", "privacy", "connection"):
            assert group in st, f"缺少分组: {group}"

    def test_care_group_shape(self, client):
        care = client.get("/api/settings").json()["care"]
        assert care["enabled"] is True
        # 只读运行参数也要给:用户有权知道「系统会多久找我一次」
        assert care["max_per_day"] >= 1
        assert len(care["quiet_window"]) == 2

    def test_privacy_group_reuses_same_source(self, client):
        """设置面与 /api/memory/privacy 必须**同源**,不能各算各的。"""
        st = client.get("/api/settings").json()["privacy"]
        direct = client.get("/api/memory/privacy").json()
        assert st["recording"] == direct["recording"]
        assert st["memory_dir"] == direct["memory_dir"]

    def test_connection_key_is_masked(self, client):
        """连接摘要里绝不能出现明文 Key。"""
        conn = client.get("/api/settings").json()["connection"]
        assert "api_key" not in conn
        assert set(conn) >= {"base_url", "api_key_set", "api_key_masked", "active"}


class TestPatch:
    def test_partial_update_touches_only_given_keys(self, client):
        client.patch("/api/settings", json={"care.enabled": False})
        st = client.get("/api/settings").json()
        assert st["care"]["enabled"] is False
        # 没传的键不受影响
        assert st["privacy"]["recording"] is True
        assert st["pet"]["daily_enabled"] is True

    def test_unknown_key_is_rejected_not_ignored(self, client):
        r = client.patch("/api/settings", json={"care.nonexistent": True})
        assert r.status_code == 400
        # 报错信息里要列出可用键,否则用户无从下手
        assert "可用" in r.json()["detail"]

    def test_unknown_key_rejects_whole_request(self, client):
        """一次 PATCH 里混了未知键 → 整体拒绝(不做"部分生效"这种难排查的行为)。"""
        r = client.patch("/api/settings",
                         json={"care.enabled": False, "typo.key": 1})
        assert r.status_code == 400
        assert client.get("/api/settings").json()["care"]["enabled"] is True

    def test_multi_key_update(self, client):
        st = client.patch("/api/settings", json={
            "care.enabled": False,
            "privacy.recording": False,
        }).json()
        assert st["care"]["enabled"] is False
        assert st["privacy"]["recording"] is False

    def test_empty_patch_is_noop(self, client):
        before = client.get("/api/settings").json()
        after = client.patch("/api/settings", json={}).json()
        assert before["care"] == after["care"]
        assert before["privacy"]["recording"] == after["privacy"]["recording"]

    def test_values_wrapper_form_also_accepted(self, client):
        st = client.patch("/api/settings", json={"values": {"care.enabled": False}}).json()
        assert st["care"]["enabled"] is False

    def test_mode_default_level(self, client):
        st = client.patch("/api/settings", json={"mode.default_level": "quiet"}).json()
        assert st["mode"]["default_level"] == "quiet"
        assert st["mode"]["level"] == "quiet"

    def test_mode_manual_with_minutes(self, client):
        """一个键的多种形态:传 minutes 就是"到点自动恢复"。"""
        st = client.patch("/api/settings", json={
            "mode.manual_level": {"level": "dnd", "minutes": 60},
        }).json()
        assert st["mode"]["level"] == "dnd" and st["mode"]["source"] == "manual"
        assert st["mode"]["until"], "带 minutes 的覆盖必须有到期时间"

    def test_mode_manual_clear_with_empty_string(self, client):
        client.patch("/api/settings", json={"mode.manual_level": "dnd"})
        st = client.patch("/api/settings", json={"mode.manual_level": ""}).json()
        assert st["mode"]["source"] == "default"
        assert st["mode"]["manual_level"] is None

    def test_pet_greeting_not_patchable(self, client):
        """开场白由角色卡决定,设置面板不再提供第二个写入口(未知键 400)。"""
        r = client.patch("/api/settings", json={"pet.greeting": "早上好呀。"})
        assert r.status_code == 400
