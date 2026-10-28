# -*- coding: utf-8 -*-
"""遗忘口径的**寿命契约**测试(纯函数,不触网、不落盘)。

为什么要单独一个文件:遗忘是整条记忆链路上**唯一不可逆**的一步。
2026-09-13 的 42 天真实链路实测发现,旧口径会在第 37 天物理删除
「用户叫林知远」(importance 只有 0.3、只被提过一次)——
这不是参数微调问题,而是一条会静默生效的策略缺陷。

本文件把新口径钉成可执行的契约:
  · "只提过一次"的记忆在 42 天(实测时长)内不得被归档;
  · importance ≥ MEMORY_FORGET_PINNED_IMPORTANCE 的条目**永不**自动归档;
  · risk/care 受保护类别**永不**自动归档;
  · 归档机制本身仍然有效(低价值条目最终会被软归档);
  · 但**默认不发生物理删除**。
"""
import time

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import forget

DAY = 86400.0
T0 = 1_700_000_000.0        # 固定基准时刻,避免用例随 wall clock 漂移


def lifetime(importance, mentions=1, category="other", protected=False,
             max_days=400, archived=False):
    """用**真实** forgetting_decision 逐日推进,返回 (归档日, 清除日)。

    逐日推进而不是套公式:公式一旦与实现分叉(本仓有过这种"两份真相源"的教训),
    契约测试就会失去意义。这里直接问实现"第 N 天该怎么办"。
    """
    v = {"type": "fact", "text": "x", "category": category,
         "importance": float(importance), "mentions": int(mentions),
         "first_seen": T0, "last_seen": T0, "protected": bool(protected),
         "archived": bool(archived)}
    if archived:
        v["archived_at"] = T0
    arch_day = purge_day = None
    for d in range(0, max_days + 1):
        now = T0 + d * DAY
        decision = forget.forgetting_decision(v, now)
        if decision == "archive" and arch_day is None:
            arch_day = d
            v = dict(v, archived=True, archived_at=now)
        elif decision == "purge" and purge_day is None:
            purge_day = d
            break
    return arch_day, purge_day


# ============================ 回归:实测翻车案例 ============================
class TestRegression20260913:
    def test_user_name_like_fact_survives_the_measured_run_length(self):
        """回归:imp=0.3、只提过一次的「用户叫林知远」在 42 天内不得被归档。

        旧口径下它在第 22 天归档、第 36 天被**物理清除**(实测)。
        """
        arch, purge = lifetime(0.3, mentions=1, max_days=42)
        assert arch is None, "42 天内不应归档(旧口径第 22 天就会)"
        assert purge is None

    def test_single_mention_fact_is_not_deleted_within_six_months(self):
        """任何 importance 的事实,半年内都不得被物理删除(删除默认关闭)。"""
        for imp in (0.1, 0.2, 0.3, 0.5, 0.9, 1.0):
            arch, purge = lifetime(imp, mentions=1, max_days=180)
            assert purge is None, "imp=%s 在半年内被物理删除了" % imp


# ============================ 免衰减(pinned) ============================
class TestPinned:
    def test_high_importance_never_archived(self):
        """imp ≥ PINNED_IMPORTANCE 的条目,十年也不归档。"""
        imp = cfg.MEMORY_FORGET_PINNED_IMPORTANCE
        arch, purge = lifetime(imp, mentions=1, max_days=3650)
        assert arch is None and purge is None

    def test_just_below_pinned_threshold_can_still_age_out(self):
        """阈值**下方**的条目仍会自然老去(否则 pinned 等于"什么都不忘")。"""
        arch, _ = lifetime(cfg.MEMORY_FORGET_PINNED_IMPORTANCE - 0.2,
                           mentions=1, max_days=3650)
        assert arch is not None

    def test_pinned_categories_never_archived(self):
        for cat in cfg.MEMORY_FORGET_PINNED_CATEGORIES:
            arch, purge = lifetime(0.01, mentions=1, category=cat, max_days=3650)
            assert arch is None and purge is None, "类别 %s 不应被归档" % cat

    def test_is_pinned_is_independent_of_is_protected(self):
        """pinned 只回答"该不该忘",不回答"能否主动提" —— 两者语义必须分开。"""
        v = {"importance": 0.9, "category": "preference", "protected": False}
        assert forget.is_pinned(v) is True
        assert forget.is_protected(v) is False


# ============================ 受保护集(既有契约,防止被我改坏) ============================
class TestProtectedUnchanged:
    def test_protected_categories_and_flag_never_archived(self):
        for cat in cfg.MEMORY_PROTECTED_CATEGORIES:
            arch, purge = lifetime(0.01, mentions=1, category=cat, max_days=3650)
            assert arch is None and purge is None
        arch, purge = lifetime(0.01, mentions=1, protected=True, max_days=3650)
        assert arch is None and purge is None


# ============================ 归档机制仍在 / 删除默认关闭 ============================
class TestArchiveStillWorks:
    def test_very_low_value_is_still_archived(self):
        """机制没有被"放松"到失效:极低价值 + 超龄 → 仍然软归档。"""
        arch, purge = lifetime(0.05, mentions=1, max_days=400)
        assert arch is not None, "低价值条目应当被软归档"
        assert purge is None, "但默认不得物理删除"

    def test_min_age_floor_blocks_premature_archive(self):
        """最小年龄地板:刚写进来的条目即便分数很低也不能立刻归档。"""
        v = {"type": "fact", "text": "x", "category": "other", "importance": 0.0,
             "mentions": 1, "first_seen": T0, "last_seen": T0,
             "archived": False, "protected": False}
        assert forget.forgetting_decision(v, T0 + 1 * DAY) == "keep"
        assert forget.forgetting_decision(
            v, T0 + (cfg.MEMORY_FORGET_MIN_AGE_DAYS + 1) * DAY) == "archive"

    def test_purge_requires_explicit_opt_in(self, monkeypatch):
        """purge 只是退出默认路径:显式开启后仍按观察期删除。"""
        monkeypatch.setattr(cfg, "MEMORY_FORGET_PURGE_ENABLED", True, raising=False)
        arch, purge = lifetime(0.05, mentions=1, max_days=800)
        assert arch is not None and purge is not None
        assert purge - arch >= cfg.MEMORY_ARCHIVE_GRACE_DAYS - 1


# ============================ 参数本身的护栏 ============================
class TestConfigGuardrails:
    def test_half_life_matches_other_ninety_day_windows(self):
        """半衰期与日桶保留期、基线锚定窗口同为 90 天口径(避免三套时间尺度)。"""
        assert cfg.MEMORY_FORGET_HALF_LIFE_DAYS == 90
        assert cfg.MEMORY_TICK_KEEP_DAYS == 90
        assert cfg.MEMORY_BASELINE_ANCHOR_WINDOW_DAYS == 90

    def test_purge_is_off_by_default(self):
        assert cfg.MEMORY_FORGET_PURGE_ENABLED is False

    def test_pinned_threshold_is_meaningful(self):
        assert 0.0 < cfg.MEMORY_FORGET_PINNED_IMPORTANCE <= 1.0
        assert cfg.MEMORY_IMPORTANCE_FLOOR < cfg.MEMORY_FORGET_PINNED_IMPORTANCE

    def test_memory_score_decays_monotonically(self):
        v = {"importance": 0.5, "mentions": 1, "first_seen": T0, "last_seen": T0}
        prev = None
        for d in (0, 30, 60, 120, 240):
            s = forget.memory_score(v, T0 + d * DAY)
            if prev is not None:
                assert s < prev, "memory_score 必须随年龄单调下降"
            prev = s

    def test_now_defaults_to_wall_clock_without_error(self):
        """不传 now 时不得抛异常(被 retrieve/洞察面板广泛调用)。"""
        assert forget.memory_score({"importance": 0.5}, None) >= 0.0
        assert forget.forgetting_decision({}, time.time()) == "keep"
