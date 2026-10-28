# -*- coding: utf-8 -*-
"""记忆隐私控制面的 HTTP 契约:/api/memory/privacy、/export、/item、/all。

不触网、不起后台调度。重点守两件事:
  - **知情权不受开关限制**:/privacy 在 MEMORY_INSIGHT_ENABLED=False 时也要答得上
    (它只说「在不在、记不记」,不吐原文);
  - **删除接口是白名单**,别变成「删任意 KV」的通用后门。
"""
import pytest


@pytest.fixture
def client(privacy_state_file, pet_state_file, care_state_file):
    from fastapi.testclient import TestClient
    import gzhu_cup2.entrypoints.api as server_mod
    from gzhu_cup2.agent.web import engine, sessions

    sessions.reset()
    engine.reset()
    return TestClient(server_mod.app)


class TestPrivacyStatus:
    def test_default_and_shape(self, client):
        st = client.get("/api/memory/privacy").json()
        assert st["recording"] is True
        # 「数据在哪」也要给:没有这两个字段,用户就无从知道数据落在哪
        assert "memory_dir" in st
        assert isinstance(st["files"], list)

    def test_pause_and_resume(self, client):
        r = client.patch("/api/memory/privacy", json={"recording": False})
        assert r.status_code == 200 and r.json()["recording"] is False
        assert client.get("/api/memory/privacy").json()["recording"] is False

        client.patch("/api/memory/privacy", json={"recording": True})
        assert client.get("/api/memory/privacy").json()["recording"] is True

    def test_status_available_even_when_insight_disabled(self, client, monkeypatch):
        """知情权是底线:/privacy 不受 MEMORY_INSIGHT_ENABLED 约束。

        (对照:同一开关关掉后 /api/memory/debug 会拒绝 —— 见下一个用例。)
        """
        from gzhu_cup2.agent.web import routes_admin
        monkeypatch.setattr(routes_admin.cfg, "MEMORY_INSIGHT_ENABLED", False)
        assert client.get("/api/memory/privacy").status_code == 200
        assert client.get("/api/memory/debug").status_code in (403, 503)

    def test_export_respects_insight_switch(self, client, monkeypatch):
        from gzhu_cup2.agent.web import routes_admin
        monkeypatch.setattr(routes_admin.cfg, "MEMORY_INSIGHT_ENABLED", False)
        assert client.get("/api/memory/export").status_code in (403, 503)


class TestExport:
    def test_shape(self, client):
        body = client.get("/api/memory/export").json()
        for key in ("user", "exported_at", "facts", "episodes",
                    "risk_events", "pending", "day_buckets"):
            assert key in body, f"导出缺少字段: {key}"


class TestDelete:
    def test_unknown_kind_is_rejected(self, client):
        r = client.delete("/api/memory/item", params={"kind": "whatever", "key": "k"})
        assert r.status_code == 400

    def test_whitelisted_kind_is_accepted(self, client):
        r = client.delete("/api/memory/item", params={"kind": "facts", "key": "nope"})
        assert r.status_code == 200 and r.json()["ok"] is True

    def test_wipe_requires_explicit_confirm(self, client):
        """不可逆操作不该靠一次误点完成。"""
        r = client.delete("/api/memory/all")
        assert r.status_code == 400

    def test_wipe_reports_what_was_removed(self, client):
        r = client.delete("/api/memory/all", params={"confirm": 1})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert isinstance(r.json()["removed"], dict)

    def test_wipe_clears_all_user_derived_data(self, client):
        """清空要覆盖**全部**用户派生数据:四类记忆 + ticks/risk_state/pet_daily。

        后者是从不向用户展示的内部派生数据,但同样是用户数据 ——
        留着它们就是「假删除」(知情权原则)。
        """
        from gzhu_cup2.agent.long_memory import risk_state as mrisk
        from gzhu_cup2.agent.long_memory import store as mstore
        from gzhu_cup2.agent.long_memory import ticks as mticks
        from gzhu_cup2.agent.web import sessions

        user = sessions.default_user()
        mstore.put_item(mstore.ns_facts(user), "f1", {"text": "x"})
        mstore.put_item(mstore.ns_episodes(user), "e1", {"digest": "x"})
        mstore.put_item(mticks.ns_ticks(user), "2026-01-01", {"count": 1})
        mstore.put_item(mrisk.ns_risk_state(user), "state", {"tier": "stable"})
        mstore.put_item(("pet", user, "daily"), "2026-01-01", {"text": "x"})

        r = client.delete("/api/memory/all", params={"confirm": 1})
        assert r.status_code == 200
        removed = r.json()["removed"]
        for kind in ("facts", "episodes", "ticks", "risk_state", "pet_daily"):
            assert kind in removed, f"清空回执缺少命名空间: {kind}"

        assert mstore.list_items(mstore.ns_facts(user)) == []
        assert mstore.list_items(mstore.ns_episodes(user)) == []
        assert mstore.list_items(mticks.ns_ticks(user)) == []
        assert mstore.list_items(mrisk.ns_risk_state(user)) == []
        assert mstore.list_items(("pet", user, "daily")) == []
