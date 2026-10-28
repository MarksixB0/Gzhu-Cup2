# -*- coding: utf-8 -*-
"""守护层(algo)契约测试。

锁三件核心语义:
  1. 单信号可以推分 —— 多路信号不要求"两件同时成立"才生效;
  2. 尺度永不坍缩 —— 收缩估计给 σ 下界,z 不退化;
  3. 沉默是状态,不是缺失 —— 沉默按 lapse/lapse_decay 处理,不判"痊愈"。
"""
from datetime import datetime, timedelta

import pytest

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.algo import skeleton as guard
from gzhu_cup2.agent.algo.components import features as feat
from gzhu_cup2.agent.algo.contracts import RawData
from gzhu_cup2.agent.long_memory import ticks as mticks

USER = "u-guard"


def _ts(offset: int, hour: int = 12) -> float:
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (base + timedelta(days=offset, hours=hour)).timestamp()


def _features(now=None):
    """fetch → features 两段连跑,等价于旧 features.compute(user)。"""
    from gzhu_cup2.agent.algo.components import fetch as fetch_mod
    return feat.compute(fetch_mod.fetch(USER, now=now))


def _day(offset: int, neg: float, count: int = 3, behavior=None,
         risk: str = "low", hour: int = 12) -> None:
    """往当日桶里写 count 轮(走真实聚合路径,与生产一致)。"""
    for _ in range(count):
        mticks.bump_day_bucket(USER, {
            "risk_level": risk, "emotion": "neutral", "intensity": 0.5,
            "negative_score": neg, "lex_neg": neg, "behavior": behavior,
        }, ts=_ts(offset, hour))


def _baseline(days: int = 14, neg: float = 0.30) -> None:
    for d in range(-days, 0):
        _day(d, neg)


# ============================ 尺度 ============================
class TestScale:
    def test_sigma_has_floor_even_when_all_days_identical(self):
        """基线里每天一模一样时,σ 也不能是 0。

        朴素 MAD 在这种情况下返回 0,于是 z 走 999 分支,"显著"与否只取决于
        有没有比基线高一个步进 —— 这正是绝对地板常数存在的由来。
        收缩估计:σ² = (n·s² + κ·σ0²)/(n+κ),s²=0 时仍有 σ0 撑着。
        """
        _baseline(14, 0.30)
        _day(0, 0.30)
        f = _features()
        assert f is not None
        assert f["base"]["neg_var"] == 0.0        # 前提:基线内部确实零方差
        assert f["sigma_shrunk"] > 0.03           # 但尺度没有被压成 0
        assert f["sigma_shrunk"] < 0.08           # 也不是常数先验原样透传

    def test_delta_is_measured_against_median_not_mean(self):
        """基线用中位数:短历史下隔离带会缩短,基线尾部可能混进 1-2 天恶化日,
        均值会被拖高,中位数不会。"""
        for d in range(-14, -6):
            _day(d, 0.30)                          # 基线主体
        _day(-8, 0.60)                             # 基线尾部混进一天恶化
        for d in range(-6, 0):
            _day(d, 0.55)                          # 近窗确实恶化
        _day(0, 0.55)
        f = _features()
        assert f["base"]["neg_median"] == pytest.approx(0.30, abs=0.02)
        # 若用均值,基线会被那天 0.45 拖到 ~0.32,delta 被吃掉 0.02
        assert f["base"]["neg_mean"] > f["base"]["neg_median"] + 0.01
        assert f["delta"] > 0.20


# ============================ 单信号 ============================
class TestSingleSignalMayFire:
    def test_pure_mood_decline_reaches_watch(self):
        """纯情绪恶化(无词表高位、无疏离、无症状族)**可以**触发。

        这是与旧实现相比最重要的语义变更:旧实现里这条会被 AND 门挡掉,而挡它的
        那个"第二通道"实测与情绪通道 r=0.945 —— 等于没挡,只是把门槛藏起来。
        """
        _baseline(14, 0.30)
        for d in range(-6, 0):
            _day(d, 0.55)
        _day(0, 0.55)
        a = guard.analyze(USER)
        assert a["tier"] in ("watch", "elevated")
        assert a["score"]["s_change"] > 0.5

    def test_flat_high_grumble_does_not_reach_elevated(self):
        """"口头禅型"(每天都烦/累,但生活功能完好)没有**变化** → 不进 elevated。

        它的绝对水平甚至比真恶化的人更高,所以只靠"绝对分"必然误报;
        现行设计让它走 chronic(最多 watch),elevated 必须由变化或症状族支撑。
        """
        _baseline(21, 0.40)
        for d in range(-6, 0):
            _day(d, 0.40)
        _day(0, 0.40)
        a = guard.analyze(USER)
        assert a["tier"] != "elevated"
        assert a["score"]["s_change"] < 0.25       # 确实没有变化


# ============================ 症状族 ============================
class TestSymptomFamilies:
    def test_anhedonia_density_alone_triggers(self):
        """情绪分完全不动、只有快感缺失表述 → 仍然要进 watch。

        "隐晦持续低落"与"持续抱怨但稳定"在情绪强度上完全重叠(后者甚至更高),
        唯一分得开的就是这一族 —— 所以它必须能**单独**触发,不能只做增益。
        """
        _baseline(21, 0.30)
        for d in range(-6, 0):
            _day(d, 0.30, behavior=["anhedonia"])
        _day(0, 0.30, behavior=["anhedonia"])
        a = guard.analyze(USER)
        assert a["score"]["s_behavior"] > 0.5
        assert a["tier"] == "watch"

    def test_single_day_symptom_is_not_enough(self):
        """一天命中不算密度:分母至少按 5 天算。"""
        _baseline(21, 0.30)
        _day(0, 0.30, behavior=["somatic"])
        a = guard.analyze(USER)
        assert a["score"]["s_behavior"] == 0.0


# ============================ 沉默 ============================
class TestLapse:
    def _worsen_then_silence(self):
        # 隔天来一次的稀疏节奏。沉默来临后(连续 7 个零日)对旧段而言
        # 是统计上确凿的参与度塌陷(泊松段龄读数可确认)。
        for d in range(-20, -7, 2):
            _day(d, 0.30)
        for d in range(-6, 0, 2):
            _day(d, 0.55)
        _day(0, 0.55)
        first = guard.analyze(USER)
        return first

    def test_short_silence_holds_tier(self):
        """沉默期档位必须保持,不降。

        段龄读数下塌陷被确认 → 分数自立走正常判定(mode=normal),
        "不降"的职责由档位逻辑接管;不能回到 lapse 闩锁把已确认的
        恶化按"没事"处理。
        """
        first = self._worsen_then_silence()
        assert first["tier"] in ("watch", "elevated")
        now = _ts(7)
        a = guard.analyze(USER, now=now, prev_tier=first["tier"], prev_since=_ts(0))
        assert a["tier"] == first["tier"]
        assert a["mode"] == "normal"

    def test_confirmed_collapse_breaks_lapse_hold(self):
        """每天都来的人突然消失 7 天(参与度塌陷被 BOCPD 确认)→ 沉默闩锁让位。

        沉默保持的前提是"沉默含义不明";当塌陷已被前驱路径确认,
        沉默本身就是证据,继续闩锁会把已确认的恶化按"没事"处理。
        """
        _baseline(14, 0.30)
        for d in range(-6, 0):
            _day(d, 0.55)
        _day(0, 0.55)
        first = guard.analyze(USER)
        assert first["tier"] in ("watch", "elevated")
        now = _ts(7)
        a = guard.analyze(USER, now=now, prev_tier=first["tier"], prev_since=_ts(0))
        assert a["mode"] == "normal"
        assert a["tier"] in ("watch", "elevated")

    def test_long_silence_decays(self):
        """超过 21 天开始降档 —— 沉默要被尊重,但不能永久闩锁。"""
        from gzhu_cup2.agent.algo.components.machine import TIER_RANK
        first = self._worsen_then_silence()
        now = _ts(30)
        a = guard.analyze(USER, now=now, prev_tier=first["tier"], prev_since=_ts(0))
        assert a["mode"] == "lapse_decay"
        assert TIER_RANK[a["tier"]] < TIER_RANK[first["tier"]]


# ============================ 数据不足 / 危机保护 ============================
class TestGuards:
    def test_insufficient_data_holds_previous(self):
        _baseline(14, 0.30)
        _day(0, 0.30)
        a = guard.analyze(USER, prev_tier="watch", prev_since=_ts(-1))
        assert a["tier"] == "watch"
        assert a["mode"] in ("insufficient", "normal")

    def test_crisis_aftermath_blocks_downgrade(self):
        """危机后 72h 内不允许降档。

        危机轮被刻意解耦出情绪趋势(所以趋势会显示"平稳"),如果没有这一层,
        一个刚说完"我买好了绳子"的人第二天就会被判 stable。
        """
        _baseline(14, 0.30)
        _day(-1, 0.30, risk="crisis")
        _day(0, 0.30)
        a = guard.analyze(USER, prev_tier="elevated", prev_since=_ts(-10),
                          protected_until=_ts(2))
        assert a["tier"] == "elevated"
        assert a["reason"] == "crisis_protected"


# ============================ 健壮性(坏输入不许伪装成"没事") ============================
class TestRobustness:
    """这些用例锁的是"算不出来时选择沉默",而不是"算不出来就判正常"。

    动机:所有分数最终都要和阈值比大小,而 NaN 与任何数比较都是 False ——
    一个 NaN 会安静地走到 stable 分支,表现为"系统认为这个人没事"。
    这是本项目里最危险的一类失败(比误报危险得多),所以逐层设卡。
    """

    def test_nan_score_does_not_downgrade_an_elevated_user(self):
        from gzhu_cup2.agent.algo.components import machine as mach
        f = {"idle_days": 0}
        res = mach.apply(f, {"s_total": float("nan")}, "elevated",
                        prev_since=_ts(-1), now=_ts(0))
        # 分数坏了 → 既不能升也不能立刻降;停留期内保持原档
        assert res["tier"] == "elevated"
        assert res["reason"] == "dwell_guard"

    def test_score_is_always_finite_on_garbage_buckets(self):
        """日桶里塞 None / 字符串 / NaN 时,分数仍必须是有限值。"""
        from gzhu_cup2.agent.algo.components import score as sc_mod
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        buckets = {}
        for i in range(10):
            day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            buckets[day] = {"count": "abc", "neg_n": "x", "neg_sum": "y",
                            "neg_sumsq": [], "hour_n": {}}
        f = feat.compute(RawData(now=_ts(0), buckets=buckets))
        sc = sc_mod.compute(f)
        for k, v in sc.items():
            if isinstance(v, float):
                assert v == v and abs(v) != float("inf"), f"{k} 不是有限值: {v}"

    def test_bad_threshold_order_falls_back_to_defaults(self, monkeypatch):
        """进出阈值抄反(enter < exit)时回退默认值,而不是让档位反复横跳。"""
        from gzhu_cup2.agent.algo.components import machine as mach
        monkeypatch.setattr(cfg, "GUARD_ENTER_WATCH", 0.20, raising=False)
        monkeypatch.setattr(cfg, "GUARD_EXIT_WATCH", 0.90, raising=False)
        p = mach.params()
        assert p["enter_watch"] == 0.55
        assert p["exit_watch"] == 0.38
        assert p["enter_elevated"] > p["enter_watch"] > p["exit_watch"]

    def test_nan_threshold_falls_back_to_default(self, monkeypatch):
        from gzhu_cup2.agent.algo.components import machine as mach
        monkeypatch.setattr(cfg, "GUARD_ENTER_ELEVATED", float("nan"),
                            raising=False)
        assert mach.params()["enter_elevated"] == 0.78


# ============================ 洞察面板(开发者模式)契约 ============================
class TestInsightPanelContract:
    """洞察页是唯一把内部分数翻成人话的地方,所以它的**口径**也算契约。

    这里锁的是三类曾经真实出过错的东西:
      · "资料不够"的判据口径与面板文案不一致,导致自相矛盾;
      · 症状族密度乘错了分母,把 5 天窗当 7 天窗;
      · 落盘档位与瞬时档只给一个,面板无法解释"为什么还没降档"。
    """

    USER2 = "u-guard-panel"

    @staticmethod
    def _write(user: str, offsets, neg: float = 0.30, behavior=None) -> None:
        """写两天同一时刻的轮次。⚠ offsets 是**相对今天的偏移**:负数才是过去
        (``_ts`` 是"今天零点 + offset 天"),传正数会写到未来,而日桶读取有
        上界过滤 —— 桶会写入成功但永远读不出来。"""
        for off in offsets:
            for hour in (12, 15):
                mticks.bump_day_bucket(user, {
                    "risk_level": "low", "emotion": "neutral", "intensity": 0.5,
                    "negative_score": neg, "lex_neg": neg, "behavior": behavior,
                }, ts=_ts(off, hour=hour))

    def test_data_basis_uses_recent_window(self):
        """数据不足时,面板必须按"最近 7 天有几天"解释,而不是"总共几天"。

        复现的历史 bug:14 天窗里有 8 天记录、但最近 7 天只有 1 天 →
        判 insufficient,而前端引用总天数与 7 天门槛,显示
        「需要至少 7 天的记录才敢下结论，现在只有 8 天」。
        """
        from gzhu_cup2.agent.web import routes_admin as ra
        self._write(self.USER2, list(range(-14, -7)) + [-2])
        v = ra._guard_view(self.USER2)
        assert v["data_sufficient"] is False
        b = v["data_basis"]
        assert b["basis"] == "recent"
        # 依据必须自洽:不足 = 近窗天数 < 门槛
        assert b["recent_n_days"] < b["min_recent_days"]
        # 而 90 天锚定窗的总天数确实 >= 门槛 —— 证明"两个数都对,但混着说就错"
        assert b["days_total"] >= b["min_recent_days"]

    def test_family_span_is_density_denominator(self):
        """family_span 必须是 max(5, 近窗有记录天数),前端据此把密度翻成天数。"""
        from gzhu_cup2.agent.web import routes_admin as ra
        user = "u-guard-panel-fam"
        self._write(user, [-2, -1, 0], neg=0.42, behavior=["withdraw"])
        v = ra._guard_view(user)
        blk = v["score"]
        assert blk is not None
        n_recent = blk["windows"]["recent"]["n_days"]
        assert blk["family_span"] == max(5, n_recent)
        rate = blk["families"]["withdraw"]
        # 密度 × 分母 必须能还原成"有几天":允许 1 天的舍入误差
        assert abs(rate * blk["family_span"] - 3) <= 1.0

    def test_tier_and_instant_tier_are_both_reported(self):
        """落盘档位与瞬时档必须同时给出 —— 面板要能解释"为什么是这个档位"。

        构造用"每天都来然后消失":塌陷被 BOCPD 确认后沉默闩锁让位
        (见 TestLapse::test_confirmed_collapse_breaks_lapse_hold),
        此时 tier 来自状态机正常判定,tier_instant 来自瞬时口径,
        两者都缺席面板就无法解释档位来由。
        """
        from gzhu_cup2.agent.web import routes_admin as ra
        user = "u-guard-panel-silent"
        self._write(user, list(range(-20, -10)))
        v = ra._guard_view(user)
        assert v["mode"] == "normal"          # 塌陷确认:沉默闩锁让位,按分数正常判
        assert v["tier"] in ("watch", "elevated")
        assert "tier_instant" in v and "tier" in v
        assert "tier_reason" in v
