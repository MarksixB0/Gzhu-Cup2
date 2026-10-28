# -*- coding: utf-8 -*-
"""关怀(包 C)离线测试:probe 文案安全、policy probe 分支、runtime/coordinator 组装。

不触网;runtime 状态文件用临时文件隔离;长期记忆 API 用 monkeypatch 控制。
"""
import time

import pytest

from gzhu_cup2.agent.care import content as ccontent
from gzhu_cup2.agent.care.policy import CareSnapshot, decide
from gzhu_cup2.agent.care import runtime as cruntime
from gzhu_cup2.agent.care import coordinator as ccoord
from gzhu_cup2.agent.long_memory.tags import DIM_KEYS
from gzhu_cup2.agent.pet.character import PROMPT_DEFAULTS

HOUR = 3600.0
DAY = 86400.0
NOW = time.time()


# 关怀运行时状态隔离夹具(care_care_state_file)已统一收到 tests/conftest.py。


# ============================ content:probe 文案 ============================
# 示例话术已迁到**角色卡可覆盖的提示词集**(pet/character/prompts.py)——
# content 层不再持有任何话术文本,所以这里从默认值取。
_PROBE_EXAMPLES = PROMPT_DEFAULTS["probe_examples"]


class TestProbeContent:
    def test_all_dims_have_safe_probe_examples(self):
        for k in DIM_KEYS:
            assert k in _PROBE_EXAMPLES, f"维度 {k} 缺少人话示例"
            example = _PROBE_EXAMPLES[k]
            assert example
            assert ccontent._forbidden(example) is False, f"probe 示例泄漏内部标记: {k} -> {example}"

    def test_unknown_dim_falls_back_to_generic(self):
        ctx = ccontent._probe_context_text("bogus")
        assert ctx and "bogus" not in ctx
        assert ccontent._forbidden(ctx) is False

    def test_probe_context_includes_example(self):
        ctx = ccontent._probe_context_text("phq_sleep")
        assert _PROBE_EXAMPLES["phq_sleep"] in ctx
        assert ccontent._forbidden(ctx) is False


# ============================ policy:probe 分支 ============================
def snap(**over):
    base = dict(
        enabled=True, now=NOW, quiet_start_hour=0, quiet_end_hour=0,
        max_per_day=2, cooldown_hours=4,
        recall_min_gap_hours=24, checkin_gap_hours=48,
        soothe_min_gap_hours=1,
        probe_min_gap_hours=6, probe_max_gap_hours=72,
        last_proactive_ts=None, date_count=0, last_user_ts=None,
        initiable_topics=[], recent_distress=False, probe_candidates=[],
    )
    base.update(over)
    return CareSnapshot(**base)


class TestDecideProbe:
    def test_probe_when_gap_in_window_and_candidates(self):
        d = decide(snap(last_user_ts=NOW - 8 * HOUR,
                        probe_candidates=["phq_sleep", "phq_appetite"]))
        assert d.due is True and d.kind == "probe"
        assert d.topic == "phq_sleep"
        assert d.reason == "fill_gap"

    def test_no_probe_when_user_still_around(self):
        d = decide(snap(last_user_ts=NOW - 2 * HOUR,
                        probe_candidates=["phq_sleep"]))
        assert d.due is False or d.kind != "probe"

    def test_no_probe_when_gap_too_long(self):
        # 超过 probe_max(72h) 不再发探询 → 走 checkin 语义
        d = decide(snap(last_user_ts=NOW - 4 * DAY,
                        probe_candidates=["phq_sleep"]))
        assert d.due is True and d.kind == "checkin"

    def test_no_probe_without_candidates(self):
        d = decide(snap(last_user_ts=NOW - 8 * HOUR))
        assert d.due is False or d.kind != "probe"

    def test_soothe_still_outranks_probe(self):
        d = decide(snap(last_user_ts=NOW - 8 * HOUR,
                        recent_distress=True,
                        probe_candidates=["phq_sleep"]))
        assert d.kind == "soothe"


# ============================ runtime:持久化与 outbox ============================
class TestRuntimeStore:
    def test_enabled_default_and_toggle(self, care_state_file):
        assert cruntime.enabled("u") is True
        cruntime.set_enabled("u", False)
        assert cruntime.enabled("u") is False

    def test_mark_sent_pending_and_dismiss(self, care_state_file):
        msg_id = cruntime.mark_sent("u", "probe", "最近睡得好吗？", topic="phq_sleep")
        c = cruntime.counters("u")
        assert c["date_count"] == 1 and c["last_proactive_ts"] is not None
        pending = cruntime.pending("u")
        assert len(pending) == 1 and pending[0]["id"] == msg_id
        assert cruntime.dismiss(msg_id) is True
        assert cruntime.pending("u") == []

    def test_prune_expired_drops_stale_but_keeps_fresh(self, care_state_file, monkeypatch):
        """过期未读消息必须能被**物理**清除,而不是只在读取时被过滤掉。

        回归:``pending()`` 只在读取时按保留期过滤,``mark_sent()`` 只在发送新消息时
        顺带清理。若某用户此后不再收到任何主动消息,过期条目就永久留在 state 文件里
        —— 既不显示也永不释放(慢性泄漏)。``prune_expired`` 补上"只清理"这条路径。
        """
        from gzhu_cup2.agent.config import config as _cfg
        monkeypatch.setattr(_cfg, "CARE_PENDING_MAX_AGE_HOURS", 168)
        now = time.time()
        with cruntime._STATE.locked() as data:
            data["pending"] = [
                {"id": "old", "user": "u", "kind": "checkin",
                 "text": "很久以前", "created_ts": now - 999 * HOUR},
                {"id": "fresh", "user": "u", "kind": "checkin",
                 "text": "刚刚", "created_ts": now},
            ]

        assert len(cruntime.pending("u")) == 1          # 读取时已过滤
        assert cruntime.prune_expired(now=now) == 1     # 现在真的清掉
        with cruntime._STATE.view() as data:
            assert [m["id"] for m in data["pending"]] == ["fresh"]

    def test_prune_expired_is_noop_when_nothing_stale(self, care_state_file):
        cruntime.mark_sent("u", "checkin", "在的")
        assert cruntime.prune_expired() == 0
        assert len(cruntime.pending("u")) == 1

    def test_ritual_is_not_delivered_across_days(self, care_state_file):
        """晚安只当晚有效:前一晚 22:30 的 ritual 次日不得补弹。

        回归:outbox 全局保留期曾是 168h(7 天),导致前一晚的晚安第二天启动时
        才被 pollPending 弹出(实测:22:30 的「晚安」次日 18:00 才出现)。
        """
        from datetime import datetime, timedelta

        last_night = (datetime.now() - timedelta(days=1)).replace(
            hour=22, minute=30, second=0, microsecond=0).timestamp()
        with cruntime._STATE.locked() as data:
            data["pending"] = [{
                "id": "gn", "user": "u", "kind": "ritual",
                "text": "晚安呀", "created_ts": last_night,
            }]

        assert cruntime.pending("u") == []          # 读取时不补弹
        assert cruntime.prune_expired() == 1        # 且被物理清掉

    def test_time_sensitive_kinds_expire_before_global_retention(self, care_state_file):
        """checkin/soothe 过了时效就不再补发,不必等满全局 7 天。"""
        now = time.time()
        with cruntime._STATE.locked() as data:
            data["pending"] = [
                {"id": "old", "user": "u", "kind": "checkin",
                 "text": "在吗", "created_ts": now - 8 * HOUR},
                {"id": "new", "user": "u", "kind": "checkin",
                 "text": "刚刚", "created_ts": now},
            ]

        assert [m["id"] for m in cruntime.pending("u")] == ["new"]


# ============================ coordinator:组装评估 ============================
class TestCoordinator:
    def _fake_memory(self, monkeypatch, last_active_hours_ago, neg_avg=None,
                     llm_text="团团想你了，最近还好吗？"):
        """把长期记忆只读 API 与 cfg 阈值替换成可控假实现(避免夜间免扰时段影响),
        并注入假的 LLM 生成器(coordinator 现走 generate_message,无模板兜底)。"""
        from gzhu_cup2.agent.config import config as _cfg
        monkeypatch.setattr(_cfg, "CARE_QUIET_START_HOUR", 0)
        monkeypatch.setattr(_cfg, "CARE_QUIET_END_HOUR", 0)
        # 关怀调度还叠加了桌宠打扰等级门控(coordinator 在 policy 之前短路 quiet 及以上);
        # 夜间窗口 23–8 会把档位解析成 asleep,于是夜里即使免扰时段已关仍不下发 —— 这里
        # 一并关掉夜间自动休眠,断言才只随逻辑、不随墙上时间红绿。
        monkeypatch.setattr(_cfg, "PET_NIGHT_SLEEP_ENABLED", False)
        # probe 总开关默认关;本类要验证 probe 下链路,显式打开
        monkeypatch.setattr(_cfg, "CARE_PROBE_ENABLED", True)
        monkeypatch.setattr("gzhu_cup2.agent.long_memory.ticks.last_activity_ts",
                            lambda u, **kw: NOW - last_active_hours_ago * HOUR)
        monkeypatch.setattr("gzhu_cup2.agent.long_memory.trend.window_stats",
                            lambda u, days=None, now=None: (
                                {"turn_count": 3, "neg_avg": neg_avg,
                                 "risk_max_rank": 0} if neg_avg is not None else None))
        monkeypatch.setattr("gzhu_cup2.agent.long_memory.ledger.has_episodes", lambda u: True)
        monkeypatch.setattr("gzhu_cup2.agent.long_memory.ledger.uncovered_dims",
                            lambda u, days=None, now=None: ["phq_sleep", "phq_appetite"])
        monkeypatch.setattr("gzhu_cup2.agent.long_memory.retrieve.list_initiable", lambda u: [])
        monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: llm_text)

    def test_probe_downlink_via_coordinator(self, care_state_file, monkeypatch):
        self._fake_memory(monkeypatch, last_active_hours_ago=8,
                          llm_text="团团关心你最近睡得好不好呀")
        msg = ccoord.evaluate("u-co", now=NOW)
        assert msg is not None
        assert msg["kind"] == "probe" and msg["topic"] == "phq_sleep"
        assert msg["text"] == "团团关心你最近睡得好不好呀"
        # 已进 outbox,且同一维度记录了最近探询时间
        assert len(cruntime.pending("u-co")) == 1
        assert "phq_sleep" in cruntime.dims_map("u-co")

    def test_soothe_downlink_via_coordinator(self, care_state_file, monkeypatch):
        # 2 小时前以低落收尾(neg 0.7,低风险)→ soothe 优先于 probe
        self._fake_memory(monkeypatch, last_active_hours_ago=2, neg_avg=0.7,
                          llm_text="团团一直陪着你，随时都在哦。")
        msg = ccoord.evaluate("u-soothe", now=NOW)
        assert msg is not None and msg["kind"] == "soothe"
        assert msg["text"] == "团团一直陪着你，随时都在哦。"

    def test_disabled_user_silent(self, care_state_file, monkeypatch):
        self._fake_memory(monkeypatch, last_active_hours_ago=8)
        cruntime.set_enabled("u-off", False)
        assert ccoord.evaluate("u-off", now=NOW) is None

    def test_no_activity_user_silent(self, care_state_file):
        # 从未活跃(无 ticks)的用户不被调度(不主动问候陌生人)
        assert ccoord.evaluate("u-nomem", now=NOW) is None

    def test_recent_distress_passes_now_to_last_activity(self, care_state_file, monkeypatch):
        """``_recent_distress`` 必须把 ``now`` 透传给 ``last_activity_ts``。

        回归:``ticks.last_activity_ts`` 的 ``now`` 是**上界**。历史回放(把"现在"
        锚定到过去某天)时若省略它,就会读到未来的日桶,算出负数或错误的
        "距最近活跃",从而误判"刚以低落收尾"—— 这正是 ticks 模块里已经修过一轮的
        同类缺陷(回放读到未来日桶),当时漏掉了这个调用点。
        """
        seen = {}
        monkeypatch.setattr("gzhu_cup2.agent.long_memory.ticks.last_activity_ts",
                            lambda u, now=None: seen.setdefault("now", now))

        ccoord._recent_distress("u-replay", 1234567.0)
        assert seen.get("now") == 1234567.0

    def test_evaluate_passes_now_to_last_activity(self, care_state_file, monkeypatch):
        """``evaluate`` 自身的活跃度检查同样是回放敏感点。"""
        self._fake_memory(monkeypatch, last_active_hours_ago=8, neg_avg=None)
        seen = []
        monkeypatch.setattr("gzhu_cup2.agent.long_memory.ticks.last_activity_ts",
                            lambda u, now=None: (seen.append(now),
                                                 NOW - 8 * HOUR)[1])
        ccoord.evaluate("u-replay2", now=NOW)
        assert NOW in seen, f"evaluate 未把 now 透传下去: seen={seen}"

    def test_global_switch_off_short_circuits_everything(self, care_state_file, monkeypatch):
        """CARE_ENABLED=False 时,即使是"完全符合条件"的用户也不得出消息。

        判据:决策函数 cpolicy.decide 根本不该被调用 —— 这样才证明总开关在
        最前面短路,而不是靠下游条件恰好不满足。
        """
        from gzhu_cup2.agent.config import config as _cfg
        from gzhu_cup2.agent.care import policy as cpolicy
        self._fake_memory(monkeypatch, last_active_hours_ago=8, neg_avg=None)
        monkeypatch.setattr(_cfg, "CARE_ENABLED", False)
        called = []
        monkeypatch.setattr(cpolicy, "decide", lambda snap: called.append(snap))

        assert ccoord.evaluate("u-switch-off", now=NOW) is None
        assert called == []


# ============================ 观察式关怀(warm / guard) ============================
class TestWarmContent:
    FORBIDDEN = ("PHQ", "GAD", "C-SSRS", "风险", "等级", "量表", "分数", "危急", "热线", "检测", "抑郁", "0.5", "0.6")

    def test_no_facts_returns_none(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "团团注意到你的变化了。")
        assert ccontent.generate_message("warm") is None
        assert ccontent.generate_message("warm", warm_facts={}) is None
        assert ccontent.generate_message("warm", warm_facts={"mood_down": False, "quieter": False}) is None

    def test_mood_down_warm(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "团团最近感觉你好像没什么精神，要记得好好照顾自己呀。")
        text = ccontent.generate_message("warm", warm_facts={"mood_down": True})
        assert text is not None
        for token in self.FORBIDDEN:
            assert token not in text, f"warm 文案泄漏: {text}"

    def test_quieter_warm(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "最近你来找团团说话的次数少了，有点想你了。")
        text = ccontent.generate_message("warm", warm_facts={"quieter": True})
        assert text is not None
        assert "话" in text or "找我" in text
        for token in self.FORBIDDEN:
            assert token not in text

    def test_both_warm(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "你最近说话少了，也没什么精神，团团有点担心你。")
        text = ccontent.generate_message("warm", warm_facts={"mood_down": True, "quieter": True})
        assert text is not None
        assert "说话少了" in text or "没精神" in text
        for token in self.FORBIDDEN:
            assert token not in text

    def test_warm_forbidden_token_returns_none(self, monkeypatch):
        monkeypatch.setattr(ccontent, "_llm_generate",
                            lambda messages: "你的风险等级是 high")
        assert ccontent.generate_message("warm", warm_facts={"mood_down": True}) is None


class TestDecideGuardElevated:
    def test_elevated_after_distress_soothe(self):
        # elevated + recent_distress + 已过 soothe 间隔但仍在 cooldown 内 → 破例放行 soothe
        d = decide(snap(guard_tier="elevated", recent_distress=True,
                        last_user_ts=NOW - 2 * HOUR,
                        last_proactive_ts=NOW - 2 * HOUR))  # 2h 前发过:>1h soothe、<4h cooldown
        assert d.due is True and d.kind == "soothe"
        assert d.reason == "guard_elevated_after_distress"

    def test_elevated_distress_still_respects_soothe_gap(self):
        # elevated 但距上次主动仅 30min(< soothe 间隔) → 仍挡,不升温打扰
        d = decide(snap(guard_tier="elevated", recent_distress=True,
                        last_user_ts=NOW - 2 * HOUR,
                        last_proactive_ts=NOW - 30 * 60))
        assert d.due is False and d.reason == "cooldown"

    def test_elevated_without_distress_not_forced(self):
        # elevated 但非低落收尾 → 不强行 soothe,走正常决策(可能 probe/checkin)
        d = decide(snap(guard_tier="elevated", recent_distress=False,
                        last_user_ts=NOW - 8 * HOUR,
                        probe_candidates=["phq_sleep"]))
        assert d.kind != "soothe"

    def test_stable_tier_unchanged(self):
        # 默认 guard_tier=stable:即使低落收尾,冷却仍挡(不升温)
        d = decide(snap(recent_distress=True, last_user_ts=NOW - 2 * HOUR,
                        last_proactive_ts=NOW - 30 * 60))
        assert d.due is False and d.reason == "cooldown"
class TestCareApi:
    def _client(self):
        from fastapi.testclient import TestClient
        import gzhu_cup2.entrypoints.api as server_mod
        from gzhu_cup2.agent.web import engine, sessions

        sessions.reset()
        engine.reset()
        return TestClient(server_mod.app)

    def test_status_settings_pending_dismiss(self, care_state_file):
        c = self._client()
        # 初始:本地默认用户开
        st = c.get("/api/care/status").json()
        assert st["enabled"] is True and st["pending_count"] == 0

        # 手动塞一条 outbox → 拉取可见
        msg_id = cruntime.mark_sent("local_user", "checkin", "嗨，最近好吗？")
        pending = c.get("/api/care/pending").json()["messages"]
        assert len(pending) == 1 and pending[0]["id"] == msg_id

        # 确认已读 → 清空
        assert c.post(f"/api/care/pending/{msg_id}/dismiss").status_code == 200
        assert c.get("/api/care/pending").json()["messages"] == []

        # 关闭开关(直接走 care 层;PATCH /api/care/settings 已随前端裁撤删除)
        cruntime.set_enabled("local_user", False)
        assert c.get("/api/care/status").json()["enabled"] is False

    def test_dismiss_missing_404(self, care_state_file):
        c = self._client()
        assert c.post("/api/care/pending/no-such-id/dismiss").status_code == 404

    def test_evaluate_endpoint_returns_empty_when_no_activity(self, care_state_file):
        c = self._client()
        r = c.post("/api/care/evaluate")
        assert r.status_code == 200
        assert r.json()["messages"] == []


# ============================ 开发提速预设(DEV_FAST_MODE) ============================
class TestDevFastModePreset:
    """DEV_FAST_MODE 的预设键必须是 config 里真实存在的项 —— 拼错会静默失效。"""

    def test_preset_keys_are_real_config_names(self):
        from gzhu_cup2.agent.config import config as cfg
        for key in cfg._DEV_FAST_PRESET:
            assert hasattr(cfg, key), f"提速预设里的键不是 config 项:{key}"

    def test_preset_only_touches_care_and_memory_gates(self):
        """提速预设只应改"等待时间/上限",不得碰安全项与角色卡配置。"""
        from gzhu_cup2.agent.config import config as cfg
        forbidden_prefixes = ("MODEL_", "PET_ACTIVE_CARD", "PET_CARD_DIR",
                              "CRISIS_", "MEMORY_PROTECTED_")
        for key in cfg._DEV_FAST_PRESET:
            assert not key.startswith(forbidden_prefixes), f"提速预设不得改:{key}"

    def test_fast_preset_actually_relaxes_gates(self):
        from gzhu_cup2.agent.config import config as cfg
        p = cfg._DEV_FAST_PRESET
        assert p["CARE_EVAL_INTERVAL_SECONDS"] < 60
        assert p["CARE_COOLDOWN_HOURS"] < 4
        assert p["CARE_MAX_PER_DAY"] > 2
        # start == end 表示全天无免扰
        assert p["CARE_QUIET_START_HOUR"] == p["CARE_QUIET_END_HOUR"]
        assert p["CARE_REQUIRE_ACTIVITY"] is False
        assert p["MEMORY_CONSOLIDATE_MIN_TURNS"] < 5

    def test_dev_fast_mode_is_a_bool_switch(self):
        from gzhu_cup2.agent.config import config as cfg
        assert isinstance(cfg.DEV_FAST_MODE, bool)
