# -*- coding: utf-8 -*-
"""P1 新增 HTTP 端点离线冒烟(不触网、不起后台调度)。

覆盖:/api/pet/settings 开关读写、/api/pet/daily 今日叙事读取(未生成→text=None)、
/api/memory/debug 增显 risk_facets 与 baseline 字段。
"""
import pytest


# pet 运行时状态隔离夹具(pet_state_file)与 client 夹具见下。


@pytest.fixture
def client(pet_state_file):
    from fastapi.testclient import TestClient
    import gzhu_cup2.entrypoints.api as server_mod
    from gzhu_cup2.agent.web import engine, sessions

    sessions.reset()
    engine.reset()
    return TestClient(server_mod.app)


class TestPetEndpoints:
    def test_settings_default_and_toggle(self, client):
        st = client.get("/api/pet/settings").json()
        assert st["user"] == "local_user"
        assert st["daily_enabled"] is True
        r = client.patch("/api/pet/settings", json={"daily_enabled": False})
        assert r.status_code == 200 and r.json()["daily_enabled"] is False
        assert client.get("/api/pet/settings").json()["daily_enabled"] is False

    def test_daily_returns_none_when_not_generated(self, client):
        body = client.get("/api/pet/daily").json()
        assert "text" in body and body["text"] is None

    def test_daily_shape_after_manual_store(self, client):
        from gzhu_cup2.agent.pet import narrative as pn
        pn._llm_generate = lambda refs: "今天我晒了太阳。"
        try:
            life = pn.ensure_today("local_user")
            assert life is not None and life["text"]
            body = client.get("/api/pet/daily").json()
            assert body["text"] == life["text"]
        finally:
            pn._llm_generate = None


class TestMemoryDebugEnriched:
    def test_debug_has_risk_and_baseline_fields(self, client):
        body = client.get("/api/memory/debug").json()
        assert "backend" in body and isinstance(body["users"], list)
        for u in body["users"]:
            assert "risk_facets" in u
            assert "baseline" in u
            # 画像观测面:trait_card / trigger_mining 必须有真实消费方,
            # 否则就是"写了+测了但没人用"的死代码
            assert "trait_card" in u
            assert "trigger_mining" in u

    def test_debug_risk_facets_respects_configured_window(self, client):
        """risk_facets 必须遵循 MEMORY_RISK_EVENTS_RECENT_DAYS,而不是全时段。

        此前端点固定传 days=None,配置里的 30 天口径等于形同虚设。
        """
        import time
        from gzhu_cup2.agent.config import config as cfg
        from gzhu_cup2.agent.long_memory import store as mem_store

        old_ts = time.time() - (cfg.MEMORY_RISK_EVENTS_RECENT_DAYS + 5) * 86400.0
        mem_store.add_risk_event(cfg.MEMORY_USER_KEY, "ideation", "很久以前的想法", ts=old_ts)
        mem_store.add_risk_event(cfg.MEMORY_USER_KEY, "plan", "最近的想法", ts=time.time())

        users = client.get("/api/memory/debug").json()["users"]
        me = next(u for u in users if u["user"] == cfg.MEMORY_USER_KEY)
        assert me["risk_facets"] == {"plan": 1}              # 窗口内
        assert me["risk_facets_total"] == {"ideation": 1, "plan": 1}  # 全时段


class TestMemoryDebugContract:
    """前端「调试」面板(components/DebugSettings.vue)依赖的字段必须齐全。

    这是一个**跨端契约测试**:少一个字段,界面上就会显示 undefined,
    而 Python 侧的测试全绿 —— 所以必须在后端钉住。
    字段清单与 frontend/src/types.ts::MemoryDebugUser 一一对应。
    """

    TOP_KEYS = {"backend", "users"}
    USER_KEYS = {
        "user", "pending_turns", "fact_count", "facts",
        "episode_count", "episodes", "tick_days_7", "tick_turns_7",
        "recent_summary", "risk_facets", "risk_facets_total",
        "baseline", "trait_card", "trigger_mining",
    }

    def test_payload_shape_matches_frontend_contract(self, client):
        body = client.get("/api/memory/debug").json()
        assert set(body) == self.TOP_KEYS, f"顶层字段变了: {sorted(body)}"
        assert isinstance(body["backend"], str)
        assert isinstance(body["users"], list)
        for u in body["users"]:
            missing = self.USER_KEYS - set(u)
            assert not missing, f"前端契约缺字段: {sorted(missing)}"

    def test_always_present_fields_have_expected_types(self, client):
        users = client.get("/api/memory/debug").json()["users"]
        assert users, "至少要有一个默认用户条目"
        for u in users:
            assert isinstance(u["user"], str)
            assert isinstance(u["pending_turns"], int)
            assert isinstance(u["fact_count"], int)
            assert isinstance(u["facts"], list)
            assert isinstance(u["episode_count"], int)
            assert isinstance(u["episodes"], list)
            assert isinstance(u["tick_days_7"], int)
            assert isinstance(u["tick_turns_7"], int)
            assert isinstance(u["risk_facets"], dict)
            assert isinstance(u["risk_facets_total"], dict)
            assert isinstance(u["trigger_mining"], list)

    def test_optional_sections_are_null_or_object(self, client):
        """baseline / trait_card / recent_summary 数据不足时必须为 null,
        前端靠这个判空来决定是否渲染该区块。"""
        users = client.get("/api/memory/debug").json()["users"]
        for u in users:
            assert u["recent_summary"] is None or isinstance(u["recent_summary"], str)
            for key in ("baseline", "trait_card"):
                assert u[key] is None or isinstance(u[key], dict), f"{key} 类型不对"

    def test_baseline_keys_when_present(self, client):
        """有基线时字段名必须与前端 MemoryBaseline 一致。"""
        import time
        from gzhu_cup2.agent.config import config as cfg
        from gzhu_cup2.agent.long_memory import ticks as mticks

        # 造出足够多的"有记录日",让基线能建立(需要 ≥2 个基线点)
        now = time.time()
        for i in range(6):
            mticks.bump_day_bucket(
                cfg.MEMORY_USER_KEY,
                {"negative_score": 0.6, "emotion": "sad"},
                ts=now - i * 86400.0,
            )

        users = client.get("/api/memory/debug").json()["users"]
        me = next(u for u in users if u["user"] == cfg.MEMORY_USER_KEY)

        # 断言非空 —— 否则下面的字段名断言会变成"空跑",测试看着绿其实没测东西
        assert me["baseline"] is not None, "6 个有记录日应足以建立基线"
        assert me["trait_card"] is not None

        assert set(me["baseline"]) == {
            "mu", "sigma", "estimator", "baseline_days", "drift_days",
            "above_count", "consecutive_above", "sustained_worsening",
            # 绝对位移地板(与 z 并列的第二道闸)及其阈值回显,见 config 同名常量
            "min_abs_delta",
            # 绝对位移兜底(MAD 膨胀时 z 钝化,位移够大仍判漂移日),阈值回显
            "abs_alert",
        }
        assert set(me["trait_card"]) == {"traits", "dominant_emotion", "evidence"}
        assert set(me["trait_card"]["traits"]) == {
            "baseline_mood", "stability", "negative_tendency", "social_orientation",
        }
        assert set(me["trait_card"]["evidence"]) == {
            "days", "turn_count", "neg_avg", "volatility",
            "negative_share", "risk_rank_max", "topic_episodes", "social_episodes",
        }
