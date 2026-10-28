# -*- coding: utf-8 -*-
"""读侧「按问题取用」行为测试(离线,不触网)。

锁住的是 42 天注入实测里**唯一真正提升召回**的那一刀,以及它周围的护栏:

  | 机制 | 实测依据 |
  |---|---|
  | 命中档优先(base 低也能被问到) | 静态排序 9 题覆盖 5/9 → 按问题取用 9/9 |
  | **命中档限量插队(不得整体越过非命中档)** | 硬分层下 R3 轨迹丢 P4/P6/P12(75.0%→58.3%);限量后三条轨迹均不低于改动前 |
  | 相关**分档**内再按分数 | 加权式下高分但不切题的同类事实仍会挤掉唯一答案 |
  | 状态道上限 + 风险内容豁免 | 7/17 条配额曾给情绪碎片;限流不能压掉风险留痕 |
  | 读侧近义去冗 | 问"外婆"时 14/17 条配额被同一命题的不同措辞占满 |
  | 无 query 行为不变 | care / 洞察面板拿不到问题,不能跟着变 |
"""
import time

from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.long_memory import store as mem_store
from gzhu_cup2.agent.long_memory import retrieve

USER = "u-readside"

# 30 条互不近义、分数都不低的"占位事实"(用来把字符预算吃满)
_FILLER_WORDS = [
    "天气", "午餐", "交通", "排队", "雨伞", "课表", "邮箱", "钥匙", "水杯", "耳机",
    "窗户", "走廊", "电梯", "板凳", "窗帘", "台灯", "日历", "袜子", "篮球", "铅笔",
    "邮票", "地图", "灯笼", "陶艺", "木工", "油漆", "风扇", "插座", "拖鞋", "围巾",
]


def _fill_store(extra=()):
    """30 条高分占位事实 + 调用方指定的事实。"""
    for w in _FILLER_WORDS:
        mem_store.merge_fact(USER, "用户记录了关于%s的备注" % w, "other", 0.9)
    for text, cat, imp in extra:
        mem_store.merge_fact(USER, text, cat, imp)


def _texts(sel):
    return [str(v.get("text") or "") for v in sel["facts"]]


class TestQueryDrivenRecall:
    def test_low_score_answer_is_reachable_when_asked(self):
        """核心回归:库里"分数低但切题"的事实,在被问到时必须进上下文。

        实测对应现象:问「我外婆后来身体怎么样了」时,静态排序下
        「用户的外婆要做手术」进不去(被高分情绪句挤掉),而它是唯一答案。
        """
        _fill_store(extra=[("用户的外婆要做手术", "family", 0.4)])
        static = retrieve.select_context(USER, purpose="reply")
        assert not any("外婆" in t for t in _texts(static)), \
            "前置条件:静态排序下这条低分事实进不去(否则本用例失去意义)"

        asked = retrieve.select_context(USER, purpose="reply", query="我外婆后来身体怎么样了？")
        assert any("外婆" in t for t in _texts(asked)), "被问到时必须能取到"

    def test_off_topic_high_score_still_wins_without_query(self):
        """没有问题时,排序仍是 memory_score(向后兼容,不能跟着相关性漂)。"""
        _fill_store(extra=[("用户的外婆要做手术", "family", 0.4)])
        a = retrieve.select_context(USER, purpose="reply")
        b = retrieve.select_context(USER, purpose="reply", query=None)
        assert _texts(a) == _texts(b)

    def test_on_topic_tier_is_reported(self):
        _fill_store(extra=[("用户的外婆要做手术", "family", 0.4)])
        sel = retrieve.select_context(USER, purpose="reply", query="我外婆身体怎么样")
        assert sel["query"] == "我外婆身体怎么样"
        assert any(e["on_topic"] for e in sel["explain"])
        assert sel["intent"]["topics"], "意图识别不应为空"

    def test_explain_has_lane_and_reason(self):
        _fill_store(extra=[("用户的外婆要做手术", "family", 0.4)])
        sel = retrieve.select_context(USER, purpose="reply", query="我外婆身体怎么样")
        row = next(e for e in sel["explain"] if "外婆" in e["text"])
        assert row["lane"] in ("event", "state", "archive")
        assert row["selected"] is True and row["reason"] is None
        unselected = [e for e in sel["explain"] if not e["selected"]]
        assert unselected and all(e["reason"] for e in unselected), "落选必须给出原因"


class TestLaneGuard:
    STATE_FACTS = [
        "用户有失眠问题", "用户觉得很累", "用户白天一直发呆", "用户感到有些孤独",
        "用户提不起劲", "用户莫名心慌", "用户压力很大", "用户最近有点烦躁",
    ]

    def test_state_lane_is_capped_for_reply(self):
        for t in self.STATE_FACTS:
            mem_store.merge_fact(USER, t, "health", 0.9)
        mem_store.merge_fact(USER, "用户在读大学", "school", 0.9)
        sel = retrieve.select_context(USER, purpose="reply")
        n_state = sum(1 for v in sel["facts"] if v["text"] in self.STATE_FACTS)
        assert n_state <= cfg.MEMORY_RETRIEVE_STATE_MAX
        # 档案类不该被情绪碎片挤掉
        assert any("读大学" in t for t in _texts(sel))

    def test_risk_content_is_exempt_from_state_cap(self):
        """风险豁免按**内容**判:它可能长得像普通情绪句(category=emotion、无 protected)。"""
        for t in self.STATE_FACTS:
            mem_store.merge_fact(USER, t, "emotion", 0.9)
        mem_store.merge_fact(USER, "用户最近总是觉得活着没意思", "emotion", 0.9)
        sel = retrieve.select_context(USER, purpose="reply")
        assert any("活着没意思" in t for t in _texts(sel)), "风险留痕必须进上下文,不受状态限流"

    def test_care_purpose_is_not_capped(self):
        """care 是"情绪道":主动关怀正需要状态内容,不做状态限流。"""
        for t in self.STATE_FACTS:
            mem_store.merge_fact(USER, t, "health", 0.9)
        sel = retrieve.select_context(USER, purpose="care")
        n_state = sum(1 for v in sel["facts"] if v["text"] in self.STATE_FACTS)
        assert n_state > cfg.MEMORY_RETRIEVE_STATE_MAX
        assert sel["state_cap"] is None


class TestReadDedup:
    """读侧去冗是**纵深防御**:写库时的近义合并已经吃掉大多数重复,
    但库里仍可能留着历史重复(旧数据、迁移、直写路径)。故这里用 put_item 直写造现场。"""

    @staticmethod
    def _raw(key, text, mentions=1):
        now = time.time()
        mem_store.put_item(mem_store.ns_facts(USER), key, {
            "type": "fact", "text": text, "category": "family",
            "importance": 0.9, "mentions": mentions,
            "first_seen": now, "last_seen": now,
            "archived": False, "protected": False,
        })

    def test_near_duplicate_not_served_twice(self):
        self._raw("k1", "用户的外婆要做手术")
        self._raw("k2", "用户的外婆将做手术")
        sel = retrieve.select_context(USER, purpose="reply")
        got = [t for t in _texts(sel) if "外婆" in t]
        assert len(got) == 1, "同一命题的两种措辞只应出现一次: %s" % got
        dup = [e for e in sel["explain"] if e["reason"] == "duplicate_in_context"]
        assert dup, "去冗必须给出可解释的落选原因"

    def test_distinct_facts_about_same_entity_survive(self):
        """去冗只吃"同一个命题",不吃"同一实体的不同事实"(否则 P5 那类题会失答)。"""
        self._raw("k1", "用户的弟弟沈嘉树今年高三")
        self._raw("k2", "用户的弟弟沈嘉树明年就要高考了")
        sel = retrieve.select_context(USER, purpose="reply")
        assert len([t for t in _texts(sel) if "弟弟" in t]) == 2

    def test_debug_view_keeps_everything(self):
        self._raw("k1", "用户的外婆要做手术")
        self._raw("k2", "用户的外婆将做手术")
        sel = retrieve.select_context(USER, purpose="debug")
        assert len([t for t in _texts(sel) if "外婆" in t]) == 2
        assert sel["read_dedup"] is False


class TestContextRendering:
    def test_recent_state_has_two_horizons(self):
        """【近期状态】必须同时给"近 3 天"和"近三周" —— 只给 3 天会把三周低落读成平稳。"""
        from gzhu_cup2.agent.long_memory import ticks as mticks
        now = time.time()
        for i in range(6):                      # 够 MEMORY_TREND_LONG_MIN_DAYS
            ts = now - i * 86400.0
            day = time.strftime("%Y-%m-%d", time.localtime(ts))
            mem_store.put_item(mticks.ns_ticks(USER), day, {
                "day": day, "count": 4, "neg_sum": 2.4, "neg_n": 4,
                "lex_neg_sum": 1.6, "lex_neg_n": 4, "last_ts": ts,
            })
        ctx = retrieve.build_memory_context(USER)
        assert "【近期状态】" in ctx
        assert "近三周" in ctx, ctx
        assert ctx.count("- ") >= 2, "近期状态应是两行(近 3 天 + 近三周)"

    def test_query_does_not_leak_internal_marks(self):
        _fill_store(extra=[("用户的外婆要做手术", "family", 0.4)])
        ctx = retrieve.build_memory_context(USER, purpose="reply", query="我外婆身体怎么样")
        assert "[family]" not in ctx          # 内部标记不得进 reply


class TestQueryPromoteCap:
    """「按问题取用」必须是**限量插队**,不能整体越过非命中档(硬分层)。

    为什么单独立一组用例(第三轮 R3 实测,2026-09-14):
      硬分层下 `on_topic` 是排序键第一位,于是一旦用户那句话与库内文本**字面不重合**,
      整条档案事实就被排到所有命中档事实之后、再被字符预算挤出配额。
      R3 轨迹(问「我是做什么工作的」,库里写「用户在深圳做后端开发快两年了」)
      一次丢掉 P4/P6/P12 三题,读侧覆盖 75.0% → 58.3%;而 R2 的 9/9 是**样本内**结果
      (阈值本就在 R2 的探测题上调出),不能外推。
      限量插队后同一份冻结记忆库实测:v1 50.0% / R2 88.9% / R3 75.0%,
      **三条轨迹都不低于改动前的旧实现**。
    """

    ON_TOPIC = [
        "用户的外婆要做手术", "用户的外婆骨折住院了", "用户的外婆下周要去复查",
        "用户的外婆术后恢复得不错", "用户的外婆已经出院回家了",
    ]
    Q = "我外婆后来身体怎么样了？"

    def _sel(self, cap):
        old = cfg.MEMORY_RETRIEVE_RELEVANCE_PROMOTE_MAX
        cfg.MEMORY_RETRIEVE_RELEVANCE_PROMOTE_MAX = cap
        try:
            return retrieve.select_context(USER, purpose="reply", query=self.Q)
        finally:
            cfg.MEMORY_RETRIEVE_RELEVANCE_PROMOTE_MAX = old

    def test_cap_zero_equals_pure_static(self):
        """回滚开关:置 0 即等价于纯静态序(不按问题取用)。"""
        _fill_store(extra=[(t, "family", 0.3) for t in self.ON_TOPIC])
        assert _texts(self._sel(0)) == _texts(retrieve.select_context(USER, purpose="reply"))

    def test_cap_bounds_how_many_on_topic_facts_jump_the_queue(self):
        """核心不变量:排在最前面的命中事实条数不得超过 cap。"""
        _fill_store(extra=[(t, "family", 0.3) for t in self.ON_TOPIC])
        cap = 2
        rows = self._sel(cap)["explain"]        # explain 的顺序 = 实际取用顺序
        prefix = 0
        for e in rows:
            if not e["on_topic"]:
                break
            prefix += 1
        assert prefix <= cap, "最多允许 %d 条命中事实插队,实际 %d" % (cap, prefix)
        assert sum(1 for e in rows if e["on_topic"]) > cap, \
            "前置条件:切题事实要多于 cap,否则本用例失去意义"

    def test_large_cap_restores_old_hard_tiering(self):
        """把 cap 调到很大即回到旧的硬分层行为(便于回滚对比,而不是把它删掉)。"""
        _fill_store(extra=[(t, "family", 0.3) for t in self.ON_TOPIC])
        rows = self._sel(999)["explain"]
        n_on = sum(1 for e in rows if e["on_topic"])
        first_off = next((i for i, e in enumerate(rows) if not e["on_topic"]), None)
        assert n_on > 2 and first_off is not None
        assert all(e["on_topic"] for e in rows[:first_off]), \
            "硬分层下所有命中事实都应排在第一个非命中档事实之前"
