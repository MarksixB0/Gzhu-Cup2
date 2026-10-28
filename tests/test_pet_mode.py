# -*- coding: utf-8 -*-
"""打扰等级(Interruption Level):纯函数解析 + 状态读写 + care 门控。

对应 PLAN_NEXT.md §2(已裁定)。三条约束各自有对应断言:
  1. 等级**有序** —— 「更安静」必须包含「较安静」的全部行为(test_at_least_*);
  2. 来源优先级 —— 手动临时 > 自动(夜间) > 用户默认(test_manual_* / test_night_*);
  3. quiet 及以上**真能拦住**主动消息(TestCareGate)。
"""
from datetime import datetime

import pytest


def _at(hour: int, day: int = 1) -> float:
    """2026-01-<day> <hour>:00 的时间戳(本地时区,与 is_quiet_hour 的取值口径一致)。"""
    return datetime(2026, 1, day, hour, 0).timestamp()


# ============================ 纯函数:有序与规范化 ============================

class TestLevelOrdering:
    def test_rank_is_ordered(self):
        from gzhu_cup2.agent.pet import mode
        assert mode.rank("active") < mode.rank("quiet") < mode.rank("dnd") < mode.rank("asleep")

    def test_at_least_is_monotonic(self):
        """「更安静」必须包含「较安静」—— 这是有序等级存在的唯一理由。"""
        from gzhu_cup2.agent.pet import mode
        assert mode.at_least("dnd", "quiet") is True
        assert mode.at_least("dnd", "dnd") is True
        assert mode.at_least("asleep", "dnd") is True
        assert mode.at_least("quiet", "dnd") is False
        assert mode.at_least("active", "quiet") is False

    def test_unknown_level_falls_back_to_default(self):
        """脏数据不得变成意外永久静默:解析不出就回落到默认档。"""
        from gzhu_cup2.agent.pet import mode
        assert mode.normalize("nonsense") == mode.normalize(None) == "active"
        assert mode.normalize("QUIET") == "quiet"   # 大小写宽容
        assert mode.normalize("  dnd  ") == "dnd"

    def test_manual_choices_exclude_asleep(self):
        """休眠只由夜间规则进入,不给用户手动选(裁定 ①)。"""
        from gzhu_cup2.agent.pet import mode
        assert "asleep" not in mode.MANUAL_CHOICES
        assert set(mode.MANUAL_CHOICES) < set(mode.LEVELS)


# ============================ 纯函数:resolve 的优先级 ============================

class TestResolve:
    def test_default_in_daytime(self):
        from gzhu_cup2.agent.pet import mode
        r = mode.resolve(default_level="quiet", now=_at(12))
        assert r["level"] == "quiet" and r["source"] == "default"

    def test_manual_overrides_default(self):
        from gzhu_cup2.agent.pet import mode
        r = mode.resolve(default_level="quiet", manual_level="active", now=_at(12))
        assert r["level"] == "active" and r["source"] == "manual"

    def test_expired_manual_is_ignored(self):
        from gzhu_cup2.agent.pet import mode
        r = mode.resolve(default_level="active", manual_level="dnd",
                         manual_until=_at(12) - 1, now=_at(12))
        assert r["level"] == "active" and r["source"] == "default"

    def test_manual_with_future_until_is_kept(self):
        from gzhu_cup2.agent.pet import mode
        r = mode.resolve(default_level="active", manual_level="quiet",
                         manual_until=_at(13), now=_at(12))
        assert r["level"] == "quiet" and r["source"] == "manual"
        assert r["until"] == _at(13)

    def test_night_auto_sleeps(self):
        from gzhu_cup2.agent.pet import mode
        r = mode.resolve(default_level="active", night_sleep=True, now=_at(2))
        assert r["level"] == "asleep" and r["source"] == "auto" and r["reason"] == "night"

    def test_night_boundary_edges(self):
        """23–8 时段:23 点进入、8 点退出(与 care.policy.is_quiet_hour 同一口径)。"""
        from gzhu_cup2.agent.pet import mode
        assert mode.resolve(default_level="active", now=_at(23))["level"] == "asleep"
        assert mode.resolve(default_level="active", now=_at(7))["level"] == "asleep"
        assert mode.resolve(default_level="active", now=_at(8))["level"] == "active"
        assert mode.resolve(default_level="active", now=_at(12))["level"] == "active"

    def test_manual_beats_night(self):
        """深夜用户手动点醒 —— 明确意愿压过自动规则。"""
        from gzhu_cup2.agent.pet import mode
        r = mode.resolve(default_level="active", manual_level="active",
                         night_sleep=True, now=_at(2))
        assert r["level"] == "active" and r["source"] == "manual"

    def test_night_sleep_can_be_disabled(self):
        from gzhu_cup2.agent.pet import mode
        r = mode.resolve(default_level="active", night_sleep=False, now=_at(2))
        assert r["level"] == "active" and r["source"] == "default"


# ============================ 状态层(读写 + 到期) ============================

class TestRuntimeState:
    @pytest.fixture(autouse=True)
    def _daytime_no_night_sleep(self, monkeypatch):
        """关掉夜间自动休眠,让读**真实时钟**的用例不再随墙上时间红绿。

        本类多数用例不传 ``now``(走 ``time.time()``),默认窗口 23–8 会在夜里把档位
        解析成 asleep/auto —— 断言只关心 default/manual 的语义,不关的话晚上跑必红、
        白天全绿(实测 23:37 复现)。``test_night_sleep_toggle`` 自己显式传 night_sleep,
        不受影响;夜间规则本身由本模块 TestResolve 用固定时间覆盖。
        """
        from gzhu_cup2.agent.config import config as cfg
        monkeypatch.setattr(cfg, "PET_NIGHT_SLEEP_ENABLED", False)

    def test_defaults_to_active(self, pet_state_file):
        from gzhu_cup2.agent.pet import runtime as prt
        m = prt.current_mode("u1")
        assert m["level"] == "active" and m["source"] == "default"

    def test_set_default_level_persists(self, pet_state_file):
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_level("u1", "quiet")
        assert prt.current_mode("u1")["level"] == "quiet"
        assert prt.mode_status("u1")["default_level"] == "quiet"

    def test_manual_overrides_and_clear(self, pet_state_file):
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_level("u1", "quiet")
        m = prt.set_manual("u1", "dnd")
        assert m["level"] == "dnd" and m["source"] == "manual"
        m = prt.clear_manual("u1")
        assert m["level"] == "quiet" and m["source"] == "default"

    def test_manual_until_expiry_is_read_time(self, pet_state_file):
        """到期判定发生在**读取时**,不必有定时任务去清理旧状态。"""
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_manual("u1", "dnd", until=_at(12) - 1)
        assert prt.current_mode("u1", now=_at(12))["source"] == "default"
        # 未到期时仍生效
        prt.set_manual("u1", "dnd", until=_at(13))
        assert prt.current_mode("u1", now=_at(12))["level"] == "dnd"

    def test_night_sleep_toggle(self, pet_state_file):
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_night_sleep("u1", False)
        assert prt.current_mode("u1", now=_at(2))["level"] == "active"
        prt.set_night_sleep("u1", True)
        assert prt.current_mode("u1", now=_at(2))["level"] == "asleep"

    def test_users_are_isolated(self, pet_state_file):
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_manual("u1", "dnd")
        assert prt.current_mode("u2")["source"] == "default"

    def test_mode_status_shape(self, pet_state_file):
        from gzhu_cup2.agent.pet import runtime as prt
        st = prt.mode_status("u1")
        for key in ("level", "source", "until", "default_level", "manual_level",
                    "night_sleep", "choices", "levels", "preset_minutes", "night_window"):
            assert key in st, f"前端要用的字段缺失: {key}"
        # 降级必须**可被看见**:档位清单与时间窗一并给出,前端不必自带一份枚举
        assert st["choices"] == ["active", "quiet", "dnd"]
        assert st["night_window"] == [23, 8]


# ============================ care 门控 ============================

class TestCareGate:
    """等级 ≥ quiet 时,主动消息评估必须直接短路(PLAN_NEXT.md §2.1)。"""

    @pytest.fixture
    def care_ready(self, pet_state_file, monkeypatch):
        """把 evaluate 的前置门槛都放开,使门控成为唯一变量。"""
        from gzhu_cup2.agent.care import coordinator as ccoord
        monkeypatch.setattr(ccoord.cfg, "CARE_ENABLED", True)
        monkeypatch.setattr(ccoord.cruntime, "enabled", lambda user: True)
        # 测试环境没有真实活跃记录;不放开这条,门控就不是唯一变量了
        monkeypatch.setattr(ccoord.cfg, "CARE_REQUIRE_ACTIVITY", False)
        # 关掉夜间自动休眠:test_active_reaches_policy 不传 now(走真实时钟),默认窗口
        # 23–8 会在夜里把档位解析成 asleep,把 care 门控提前短路 —— 该用例只想验证
        # active 档不被门控拦住,不关的话晚上跑必红。显式传 now 的休眠用例不受影响。
        monkeypatch.setattr(ccoord.cfg, "PET_NIGHT_SLEEP_ENABLED", False)
        return ccoord

    def test_active_reaches_policy(self, care_ready, monkeypatch):
        """active 档**不**被本门控拦住 —— 它必须能走到决策层。"""
        from gzhu_cup2.agent.pet import runtime as prt
        reached = {"n": 0}

        def _snap(*a, **k):
            reached["n"] += 1
            return object()

        prt.set_level("u1", "active")
        monkeypatch.setattr(care_ready, "_build_snapshot", _snap)
        monkeypatch.setattr(care_ready.cpolicy, "decide", lambda snap: _Fail())
        assert care_ready.evaluate("u1") is None
        assert reached["n"] == 1, "active 档不该被等级门控拦住"

    def test_quiet_blocks_before_any_policy_work(self, care_ready, monkeypatch):
        """安静及以上**手动**档都在 policy 之前短路。

        这里只列 ``MANUAL_CHOICES``：休眠不开放手动（裁定 ①），它由夜间规则进入 ——
        那条路径由下一个用例覆盖。
        """
        from gzhu_cup2.agent.pet import runtime as prt

        def _boom(*a, **k):  # pragma: no cover - 走到这里说明门控没拦住
            raise AssertionError("安静及以上应当在 policy 之前就短路")

        monkeypatch.setattr(care_ready, "_build_snapshot", _boom)
        for level in ("quiet", "dnd"):
            prt.set_manual("u1", level)
            assert care_ready.evaluate("u1") is None

    def test_asleep_from_night_rule_also_blocks(self, care_ready, monkeypatch):
        """休眠档由**夜间规则**进入，进入后同样门控（它是最安静的一档）。

        与 `test_asleep_is_not_manually_settable`（test_pet_mode_api.py）配对，
        一起守住「手动设不了休眠、但夜间真能进休眠」这两件事。
        """
        from gzhu_cup2.agent.pet import runtime as prt

        def _boom(*a, **k):  # pragma: no cover
            raise AssertionError("休眠档也应当在 policy 之前短路")

        monkeypatch.setattr(care_ready, "_build_snapshot", _boom)
        prt.set_night_sleep("u1", True)
        night = datetime(2026, 1, 1, 2, 0).timestamp()
        assert care_ready.evaluate("u1", now=night) is None

    def test_manual_quiet_also_blocks(self, care_ready, monkeypatch):
        """临时静默与默认档走同一条门控。"""
        from gzhu_cup2.agent.pet import runtime as prt
        prt.set_level("u1", "active")
        prt.set_manual("u1", "quiet")
        monkeypatch.setattr(care_ready, "_build_snapshot",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该走到这里")))
        assert care_ready.evaluate("u1") is None


class _Fail:
    """decide 的替身:表示「不 due」。"""

    due = False
