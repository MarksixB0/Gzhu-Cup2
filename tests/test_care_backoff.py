# -*- coding: utf-8 -*-
"""「未获回应就退避」的行为测试。

解决两个方向的错误:
  - 旧行为:所有门槛都是 `gap_hours >= X`,于是**越沉默越发得勤**;
  - 硬停方案:未回应即永久停止 → **越沉默越消失**,对心理健康产品是危险方向。

本设计:间隔随沉默单调变长**但封顶**(永不静默)+ 未回应期间不再发提问型。

钉死六件事:
  1. 退避按 streak 取表并封顶;
  2. 从未投递过 → 不受退避约束;
  3. 未获回应时提问型(probe/recall/soothe)被封,存在感型(petlife/checkin)仍可发;
  4. **守护升温仍能穿透**未回应封锁(安全通道);
  5. `mark_blocked` 不消耗退避次数(那条消息从未到达用户);
  6. coordinator 发现用户回话后把 streak 清零。
"""
import time

import pytest

from gzhu_cup2.agent.care.policy import CareSnapshot, _backoff_hours, decide

NOW = time.time()
HOUR = 3600.0


@pytest.fixture(autouse=True)
def _daytime_no_night_sleep(monkeypatch):
    """关掉夜间自动休眠,让走真实时钟的用例不再随墙上时间红绿。

    ``TestCoordinatorResetsOnReply`` 直接调 ``coordinator.evaluate`` 且不传固定时刻,
    而关怀调度在 policy 之前还有一道桌宠打扰等级门控:默认窗口 23–8 会在夜里把档位
    解析成 asleep,于 streak 清零之前就短路返回 —— 断言只关心「回话后是否清零」,
    不关的话晚上跑必红(实测 23:37 复现)。纯函数用例(``snap`` 显式传
    quiet_start/end)与本模块其它用例不受影响。
    """
    from gzhu_cup2.agent.config import config as cfg
    monkeypatch.setattr(cfg, "PET_NIGHT_SLEEP_ENABLED", False)
    yield


def snap(**over):
    """宽松基底:除被测项外全部"最容易触发",便于隔离退避逻辑。"""
    base = dict(
        enabled=True, now=NOW,
        quiet_start_hour=0, quiet_end_hour=0,
        max_per_day=10, cooldown_hours=0.0,
        recall_min_gap_hours=0.0, checkin_gap_hours=0.0, soothe_min_gap_hours=0.0,
        probe_min_gap_hours=0.0, probe_max_gap_hours=999.0,
        # 注意:petlife 的触发条件含 `petlife_min_gap_hours > 0`(0 = 关闭该类型),
        # 所以这里不能设 0 —— 否则存在感型会退化成 checkin。
        petlife_min_gap_hours=1.0,
        last_proactive_ts=None, date_count=0,
        last_user_ts=NOW - 24 * HOUR,
        initiable_topics=["用户喜欢喝茶"],
        recent_distress=False,
        probe_candidates=["phq_sleep"],
        petlife_text="今天我晒了太阳",
        last_petlife_ts=None,
        last_delivered_ts=None, unanswered_streak=0,
        guard_tier="stable",
    )
    base.update(over)
    return CareSnapshot(**base)


# ============================ 1. 退避表与封顶 ============================
class TestBackoffTable:
    @pytest.mark.parametrize("streak,expected", [
        (1, 24.0), (2, 72.0), (3, 168.0),
        (4, 168.0), (99, 168.0),      # 封顶:永不静默
    ])
    def test_table_and_cap(self, streak, expected):
        assert _backoff_hours(streak) == expected

    def test_zero_streak_is_safe(self):
        assert _backoff_hours(0) == 24.0   # 不应崩;实际 streak>=1 才走退避


# ============================ 2. 退避 gate ============================
class TestBackoffGate:
    def test_blocked_before_interval(self):
        d = decide(snap(unanswered_streak=1,
                        last_delivered_ts=NOW - 1 * HOUR,
                        last_proactive_ts=NOW - 1 * HOUR))
        assert d.due is False and d.reason == "unanswered_backoff"

    def test_released_after_interval(self):
        d = decide(snap(unanswered_streak=1,
                        last_delivered_ts=NOW - 25 * HOUR,
                        last_proactive_ts=NOW - 25 * HOUR))
        assert d.due is True

    def test_second_streak_needs_three_days(self):
        """streak=2 → 要 72h:71h 挡住,73h 放行。"""
        blocked = decide(snap(unanswered_streak=2,
                              last_delivered_ts=NOW - 71 * HOUR,
                              last_proactive_ts=NOW - 71 * HOUR))
        assert blocked.due is False and blocked.reason == "unanswered_backoff"
        passed = decide(snap(unanswered_streak=2,
                             last_delivered_ts=NOW - 73 * HOUR,
                             last_proactive_ts=NOW - 73 * HOUR))
        assert passed.due is True

    def test_never_delivered_is_not_throttled(self):
        """从未成功投递过(last_delivered_ts=None)→ 不受退避约束。"""
        d = decide(snap(unanswered_streak=0, last_delivered_ts=None))
        assert d.due is True


# ============================ 3. 类型降级 ============================
class TestQuietAsk:
    def test_ask_types_blocked_when_unanswered(self):
        """只给提问型输入时,未回应 → 本次静默(不再追问)。"""
        d = decide(snap(unanswered_streak=1,
                        last_delivered_ts=NOW - 25 * HOUR,
                        last_proactive_ts=NOW - 25 * HOUR,
                        recent_distress=True,
                        probe_candidates=["phq_sleep"],
                        initiable_topics=["用户喜欢喝茶"],
                        petlife_text=None,          # 掐掉存在感型
                        checkin_gap_hours=999.0))
        assert d.due is False

    def test_presence_type_still_allowed_when_unanswered(self):
        """存在感型不受封锁:沉默期还能说一句"我今天…"。"""
        d = decide(snap(unanswered_streak=1,
                        last_delivered_ts=NOW - 25 * HOUR,
                        last_proactive_ts=NOW - 25 * HOUR,
                        probe_candidates=["phq_sleep"],
                        initiable_topics=["用户喜欢喝茶"]))
        assert d.due is True and d.kind == "petlife"

    def test_ask_types_work_again_after_reply(self):
        """用户回过话(streak=0)→ 提问型立即解封。"""
        d = decide(snap(unanswered_streak=0, last_delivered_ts=None))
        assert d.due is True and d.kind == "probe"

    def test_flag_can_disable_type_degradation(self, monkeypatch):
        from gzhu_cup2.agent.config import config as cfg
        monkeypatch.setattr(cfg, "CARE_QUIET_ASK_WHEN_UNANSWERED", False)
        d = decide(snap(unanswered_streak=1,
                        last_delivered_ts=NOW - 25 * HOUR,
                        last_proactive_ts=NOW - 25 * HOUR))
        assert d.due is True and d.kind == "probe"


# ============================ 4. 安全通道不被退避废掉 ============================
class TestGuardElevatedPierces:
    def test_elevated_after_distress_still_fires(self):
        """守护升温分支排在退避 gate **之前**,必须仍能穿透。"""
        d = decide(snap(unanswered_streak=5,
                        last_delivered_ts=NOW - 1 * HOUR,
                        last_proactive_ts=NOW - 1 * HOUR,
                        guard_tier="elevated", recent_distress=True))
        assert d.due is True
        assert d.kind == "soothe"
        assert d.reason == "guard_elevated_after_distress"

    def test_not_elevated_is_still_throttled(self):
        """没有守护升温时,退避照常生效(避免这条通道被滥用)。"""
        d = decide(snap(unanswered_streak=5,
                        last_delivered_ts=NOW - 1 * HOUR,
                        last_proactive_ts=NOW - 1 * HOUR,
                        guard_tier="stable", recent_distress=True))
        assert d.due is False and d.reason == "unanswered_backoff"


# ============================ 5/6. 运行时计数 ============================
class TestRuntimeStreak:
    def test_mark_sent_increments(self, care_state_file):
        from gzhu_cup2.agent.care import runtime as crt
        assert crt.counters("u1")["unanswered_streak"] == 0
        crt.mark_sent("u1", "checkin", "在的")
        c = crt.counters("u1")
        assert c["unanswered_streak"] == 1
        assert c["last_delivered_ts"] is not None
        crt.mark_sent("u1", "checkin", "在的")
        assert crt.counters("u1")["unanswered_streak"] == 2

    def test_mark_blocked_does_not_consume_streak(self, care_state_file):
        """生成失败/命中禁词时消息**没有投递**,不该消耗退避次数。

        这也是硬停方案会踩的坑:若用 last_proactive_ts 判定"未获回应",
        且用户从未发言而首次就失败,会永久静默 —— 退避 + 这个区分避开了它。
        """
        from gzhu_cup2.agent.care import runtime as crt
        crt.mark_blocked("u2", "checkin")
        c = crt.counters("u2")
        assert c["unanswered_streak"] == 0
        assert c["last_delivered_ts"] is None
        assert c["last_proactive_ts"] is not None   # 冷却仍然推进

    def test_reset_unanswered_clears(self, care_state_file):
        from gzhu_cup2.agent.care import runtime as crt
        crt.mark_sent("u3", "checkin", "在的")
        crt.mark_sent("u3", "checkin", "在的")
        c = crt.reset_unanswered("u3")
        assert c["unanswered_streak"] == 0 and c["last_delivered_ts"] is None
        assert crt.counters("u3")["unanswered_streak"] == 0


class TestCoordinatorResetsOnReply:
    def test_user_reply_clears_streak(self, care_state_file, monkeypatch):
        """用户在最后一条已投递消息之后说过话 → coordinator 清零 streak。"""
        from gzhu_cup2.agent.care import content as ccontent
        from gzhu_cup2.agent.care import coordinator as ccoord
        from gzhu_cup2.agent.care import runtime as crt
        from gzhu_cup2.agent.long_memory import ledger, retrieve, ticks, trend

        now = time.time()
        crt.mark_sent("u-reply", "checkin", "在的", now=now - 100)
        assert crt.counters("u-reply")["unanswered_streak"] == 1

        # 用户刚发言(活跃时间晚于上次投递)
        # 假实现必须接受 now=(真 last_activity_ts 的签名就是 (user, now=None));
        # coordinator 现在会把 now 透传下来。签名过窄的桩会直接把 evaluate
        # 打成 TypeError —— 这正是本轮修的那个调用点。
        _active = now
        monkeypatch.setattr(ticks, "last_activity_ts",
                            lambda u, now=None: _active)
        monkeypatch.setattr(ledger, "has_episodes", lambda u: False)
        monkeypatch.setattr(ledger, "uncovered_dims", lambda u, days=None, now=None: [])
        monkeypatch.setattr(retrieve, "list_initiable", lambda u, limit=None: [])
        monkeypatch.setattr(trend, "window_stats", lambda u, days=None, now=None: None)
        monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: "在的呀")

        ccoord.evaluate("u-reply", now=now)
        assert crt.counters("u-reply")["unanswered_streak"] == 0

    def test_user_silence_keeps_streak(self, care_state_file, monkeypatch):
        """用户没回话 → streak 保留(退避继续生效)。"""
        from gzhu_cup2.agent.care import content as ccontent
        from gzhu_cup2.agent.care import coordinator as ccoord
        from gzhu_cup2.agent.care import runtime as crt
        from gzhu_cup2.agent.long_memory import ledger, retrieve, ticks, trend

        now = time.time()
        crt.mark_sent("u-silent", "checkin", "在的", now=now - 100)

        # 用户最后一次活跃**早于**上次投递 → 没回
        _active = now - 200
        monkeypatch.setattr(ticks, "last_activity_ts",
                            lambda u, now=None: _active)
        monkeypatch.setattr(ledger, "has_episodes", lambda u: False)
        monkeypatch.setattr(ledger, "uncovered_dims", lambda u, days=None, now=None: [])
        monkeypatch.setattr(retrieve, "list_initiable", lambda u, limit=None: [])
        monkeypatch.setattr(trend, "window_stats", lambda u, days=None, now=None: None)
        monkeypatch.setattr(ccontent, "_llm_generate", lambda messages: "在的呀")

        ccoord.evaluate("u-silent", now=now)
        assert crt.counters("u-silent")["unanswered_streak"] == 1
