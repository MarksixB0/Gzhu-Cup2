# -*- coding: utf-8 -*-
"""相对时间锚定解析器离线测试(dates.resolve_event / resolve_event_date /
absolutize,确定性,不触网)。

锚点固定为 2026-09-09(周三),期望日期全部手工按日历钉死 —— 这份测试的意义
就是把「明天/下周」这类相对表达在日历上的真实落点锁住,防止解析规则漂移:
规则漂了,宠物就会在错误的日期上把「明天要考试」当成还没发生的事。
"""
import re
from datetime import datetime

import pytest

from gzhu_cup2.agent.long_memory import dates

ANCHOR = datetime(2026, 9, 9, 20, 0)  # 2026-09-09 是周三


def _resolve(text):
    return dates.resolve_event_date(text, ANCHOR)


# ============================ 日偏移 ============================
class TestDayOffsets:
    def test_today_is_anchor(self):
        assert _resolve("用户今天要考试") == "2026-09-09"

    def test_tomorrow_plus_one(self):
        assert _resolve("用户明天要考试") == "2026-09-10"

    def test_mingri_alias(self):
        assert _resolve("用户明日返校") == "2026-09-10"

    def test_day_after_tomorrow(self):
        assert _resolve("用户后天有比赛") == "2026-09-11"

    def test_two_days_after_tomorrow(self):
        assert _resolve("用户大后天搬家") == "2026-09-12"

    def test_yesterday_minus_one(self):
        assert _resolve("用户昨天去了医院") == "2026-09-08"

    def test_day_before_yesterday(self):
        assert _resolve("用户前天开始失眠") == "2026-09-07"

    def test_da_hou_tian_wins_over_hou_tian(self):
        # 「大后天」包含「后天」,必须先匹配大后天,否则 +2 覆盖 +3
        assert _resolve("用户大后天搬家") == "2026-09-12"


# ============================ 周偏移 ============================
class TestWeekdayOffsets:
    def test_next_weekday_this_week(self):
        # 锚点周三 → 周四就是明天(本周内)
        assert _resolve("用户周四有组会") == "2026-09-10"

    def test_same_weekday_rolls_forward_seven(self):
        # 锚点本身是周三 → 「周三」必须是下一个周三(+7),而不是今天
        assert _resolve("用户周三要汇报") == "2026-09-16"

    def test_weekday_after_weekend(self):
        # 周三之后最近的周一在下一周
        assert _resolve("用户周一要交报告") == "2026-09-14"

    def test_xingqi_alias(self):
        assert _resolve("用户星期五去看牙") == "2026-09-11"

    def test_libai_alias(self):
        assert _resolve("用户礼拜天要回家") == "2026-09-13"

    def test_zhou_tian_is_sunday(self):
        assert _resolve("用户周天有约") == "2026-09-13"

    def test_weekday_digit(self):
        assert _resolve("用户周6去爬山") == "2026-09-12"

    def test_next_week_strictly_after_this_week(self):
        # 「下周四」:本周四(9-10)已过锚点后的第一个周四,下周四才是 9-17
        assert _resolve("用户下周四需参加毕业答辩") == "2026-09-17"

    def test_next_week_monday(self):
        # 下一周从周一(9-14)起算
        assert _resolve("用户下周一面试") == "2026-09-14"

    def test_xia_xingqi_alias(self):
        assert _resolve("用户下星期五出差") == "2026-09-18"


# ============================ 月日 / 时长 ============================
class TestMonthDayAndDurations:
    def test_month_day_uses_anchor_year(self):
        assert _resolve("用户9月20号要返校") == "2026-09-20"

    def test_month_day_ri_alias(self):
        assert _resolve("用户9月20日有考试") == "2026-09-20"

    def test_days_later(self):
        assert _resolve("用户3天后体检") == "2026-09-12"

    def test_days_later_chinese_numeral(self):
        assert _resolve("用户三天后体检") == "2026-09-12"

    def test_weeks_later(self):
        assert _resolve("用户3周后复诊") == "2026-09-30"

    def test_one_week_later(self):
        assert _resolve("用户一周后复诊") == "2026-09-16"


# ============================ 不可解析 → None ============================
class TestUnresolvable:
    def test_no_time_word(self):
        assert _resolve("用户喜欢跑步") is None

    def test_empty_text(self):
        assert _resolve("") is None

    def test_blank_text(self):
        assert _resolve("   ") is None

    def test_invalid_calendar_date(self):
        assert _resolve("用户2月30日有考试") is None

    def test_invalid_month(self):
        assert _resolve("用户13月1日有考试") is None

    def test_invalid_day(self):
        assert _resolve("用户9月32日有考试") is None

    def test_vague_expression_not_supported(self):
        # 无法落到某一周/某一月的表达仍刻意不解析(见模块 docstring)
        assert _resolve("用户过段时间要考试") is None
        assert _resolve("用户以后再说") is None

    def test_month_and_week_ranges_are_supported(self):
        # 旧口径把「下个月」当不可解析;现在锚到区间首日(粒度按月,见 TestRanges)
        assert _resolve("用户下个月要考试") == "2026-10-01"


# ============================ 锚点不合法:不抛异常 ============================
class TestMalformedAnchor:
    @pytest.mark.parametrize("anchor", [None, "not-a-date", 123, object()])
    def test_malformed_anchor_returns_none_without_raising(self, anchor):
        assert dates.resolve_event_date("用户明天要考试", anchor) is None

    def test_bare_date_anchor_does_not_raise(self):
        # 纯 date(无时间部分)同样支持算术与 strftime,应正常解析
        assert dates.resolve_event_date(
            "用户明天要考试", datetime(2026, 9, 9).date()) == "2026-09-10"


# ============================ resolve_event(片段+日期) ============================
class TestResolveEvent:
    def test_returns_matched_literal_and_date(self):
        # 返回的片段必须是 text 里精确出现的字面量,供 absolutize 直接 replace
        assert dates.resolve_event("用户明天要考试", ANCHOR) == ("明天", "2026-09-10")

    def test_next_week_matched_literal(self):
        assert dates.resolve_event("用户下周四需参加毕业答辩", ANCHOR) == \
            ("下周四", "2026-09-17")

    def test_month_day_matched_literal(self):
        assert dates.resolve_event("用户9月20号要返校", ANCHOR) == ("9月20号", "2026-09-20")

    def test_weekday_matched_literal(self):
        assert dates.resolve_event("用户周五有组会", ANCHOR) == ("周五", "2026-09-11")

    def test_unresolvable_returns_none(self):
        assert dates.resolve_event("用户喜欢跑步", ANCHOR) is None

    def test_empty_text_returns_none(self):
        assert dates.resolve_event("", ANCHOR) is None

    def test_resolve_event_date_is_thin_wrapper(self):
        # 薄包装:签名与行为保持不变,只取日期部分
        assert dates.resolve_event_date("用户明天要考试", ANCHOR) == "2026-09-10"
        assert dates.resolve_event_date("用户喜欢跑步", ANCHOR) is None


# ============================ absolutize(原地绝对化) ============================
class TestAbsolutize:
    def test_tomorrow_becomes_absolute(self):
        assert dates.absolutize("用户明天有考试", ANCHOR) == "用户在2026-09-10有考试"

    def test_next_week_becomes_absolute(self):
        assert dates.absolutize("用户下周四需参加毕业答辩", ANCHOR) == \
            "用户在2026-09-17需参加毕业答辩"

    def test_month_day_becomes_absolute(self):
        assert dates.absolutize("用户9月20号要返校", ANCHOR) == "用户在2026-09-20要返校"

    def test_today_becomes_absolute(self):
        assert dates.absolutize("用户今天有考试", ANCHOR) == "用户在2026-09-09有考试"

    def test_only_first_match_replaced(self):
        assert dates.absolutize("用户明天有考试,明天还要复习", ANCHOR) == \
            "用户在2026-09-10有考试,明天还要复习"

    def test_unresolvable_returns_text_unchanged(self):
        text = "用户喜欢跑步"
        assert dates.absolutize(text, ANCHOR) == text

    def test_empty_text_unchanged(self):
        assert dates.absolutize("", ANCHOR) == ""

    def test_none_text_unchanged(self):
        assert dates.absolutize(None, ANCHOR) == ""

    @pytest.mark.parametrize("anchor", [None, "not-a-date", 123, object()])
    def test_malformed_anchor_returns_text_unchanged_without_raising(self, anchor):
        text = "用户明天要考试"
        assert dates.absolutize(text, anchor) == text


# ============================ 中文数字月日 / 跨年回绕 ============================
class TestChineseMonthDayAndYearRoll:
    def test_chinese_month_day(self):
        # 口头常写中文数字,裸阿拉伯数字的规则漏掉这一整类
        assert _resolve("用户十月三十一号有考试") == "2026-10-31"

    def test_chinese_month_day_multi_digit(self):
        assert _resolve("用户十二月五号返校") == "2026-12-05"

    def test_year_roll_when_bare_month_day_far_back(self):
        # 12-28 说「1月5号」→ 说的是明年,按锚点年份会算成一年前
        assert dates.resolve_event_date("用户1月5号考试", datetime(2026, 12, 28)) == "2027-01-05"

    def test_year_roll_on_month_end(self):
        assert dates.resolve_event_date("用户1月底复查", datetime(2026, 12, 28)) == "2027-01-31"

    def test_year_roll_keeps_near_past(self):
        # 只隔几周的裸月日仍按过去理解(在回忆,不是在约)
        assert dates.resolve_event_date("用户9月20号住过院", datetime(2026, 10, 28)) == "2026-09-20"


# ============================ 整周 / 整月区间 ============================
class TestRanges:
    """不带具体哪天的周/月表达:锚到**区间首日**,粒度由 absolutize 保留。"""

    def test_next_week_without_weekday(self):
        # 锚点 09-09(周三),周日起算的下一周是 09-13~09-19
        assert dates.resolve_event_date("用户下周有考试", ANCHOR, week_start="sunday") == "2026-09-13"

    def test_next_next_week(self):
        assert dates.resolve_event_date("用户下下周有考试", ANCHOR, week_start="sunday") == "2026-09-20"

    def test_this_week(self):
        assert dates.resolve_event_date("用户这周状态不好", ANCHOR, week_start="sunday") == "2026-09-06"

    def test_last_week(self):
        assert dates.resolve_event_date("用户上周很累", ANCHOR, week_start="sunday") == "2026-08-30"

    def test_week_range_does_not_swallow_weekday(self):
        # 「下周三」是具体的一天,不能被整周规则吃掉
        assert dates.resolve_event("用户下周三有考试", ANCHOR) == ("下周三", "2026-09-16")

    def test_week_range_does_not_swallow_weekend(self):
        assert dates.resolve_event("用户下周末回家", ANCHOR) == ("下周末", "2026-09-19")

    def test_month_range(self):
        assert _resolve("用户下个月要搬家") == "2026-10-01"
        assert _resolve("用户上个月很忙") == "2026-08-01"
        assert _resolve("用户这个月有点乱") == "2026-09-01"


# ============================ 周末 / 月末 ============================
class TestWeekendAndMonthEnd:
    def test_this_weekend_is_saturday(self):
        assert dates.resolve_event("用户这周末回家", ANCHOR) == ("这周末", "2026-09-12")

    def test_next_weekend(self):
        assert dates.resolve_event("用户下周末回家", ANCHOR) == ("下周末", "2026-09-19")

    def test_weekend_anchor_on_saturday_is_today(self):
        assert dates.resolve_event_date("用户这周末考试", datetime(2026, 9, 12)) == "2026-09-12"

    def test_weekend_anchor_on_sunday_points_forward(self):
        # 周日(周日起算的当周最后一天)说「这周末」→ 仍是当周的那个周六
        assert dates.resolve_event_date("用户这周末考试", datetime(2026, 9, 13)) == "2026-09-19"

    def test_bare_month_end(self):
        assert dates.resolve_event("用户月底交论文", ANCHOR) == ("月底", "2026-09-30")

    def test_this_month_end(self):
        assert dates.resolve_event_date("用户这个月底交论文", ANCHOR) == "2026-09-30"

    def test_last_month_end(self):
        assert dates.resolve_event_date("用户上个月底搬完家", ANCHOR) == "2026-08-31"

    def test_next_month_end(self):
        assert dates.resolve_event_date("用户下个月底体检", ANCHOR) == "2026-10-31"

    def test_named_month_end(self):
        assert dates.resolve_event_date("用户十二月底交报告", ANCHOR) == "2026-12-31"

    def test_month_end_in_leap_february(self):
        assert dates.resolve_event_date("用户2月底复查", datetime(2028, 1, 15)) == "2028-02-29"


# ============================ 上周X / 下下周X ============================
class TestWeekShifts:
    def test_last_week_weekday_is_in_the_past(self):
        assert dates.resolve_event("用户上周三去了医院", ANCHOR) == ("上周三", "2026-09-02")

    def test_week_after_next_weekday(self):
        assert dates.resolve_event("用户下下周四有答辩", ANCHOR) == ("下下周四", "2026-09-24")

    def test_next_week_ge_alias_still_works(self):
        assert dates.resolve_event_date("用户下个星期三考试", ANCHOR) == "2026-09-16"

    def test_week_shift_lands_on_weekday_for_every_anchor(self):
        """21 天锚点 × 7 个 token × 三档(上周/下周/下下周)全部落在目标星期几。"""
        from datetime import timedelta
        weekdays = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6}
        for week_start in ("sunday", "monday"):
            for i in range(21):
                anchor = datetime(2026, 10, 12) + timedelta(days=i)
                for token, wd in weekdays.items():
                    for prefix, future in (("上", False), ("下", True), ("下下", True)):
                        iso = dates.resolve_event_date(f"我{prefix}周{token}有安排", anchor,
                                                       week_start=week_start)
                        got = datetime.strptime(iso, "%Y-%m-%d")
                        assert got.weekday() == wd, (week_start, anchor, prefix, token)
                        assert (got > anchor) is future, (week_start, anchor, prefix, token)


# ============================ 绝对化的粒度 ============================
class TestAbsolutizeGranularity:
    def test_week_range_keeps_week_granularity(self):
        # 写成某一天会让读侧把整周读成"那天的事"
        assert dates.absolutize("用户下周有考试", ANCHOR, week_start="sunday") == \
            "用户在2026-09-13那一周有考试"

    def test_month_range_keeps_month_granularity(self):
        assert dates.absolutize("用户下个月要搬家", ANCHOR) == "用户在2026-10月要搬家"

    def test_day_granularity_is_plain_date(self):
        assert dates.absolutize("用户下周有考试", ANCHOR).count("那一周") == 1
        assert dates.absolutize("用户明天有考试", ANCHOR) == "用户在2026-09-10有考试"

    def test_weekday_prefix_is_consumed(self):
        # 「本周六」整体替换,不能只剩一个孤零零的「本」
        assert dates.absolutize("用户本周六有约", ANCHOR) == "用户在2026-09-12有约"


# ============================ 回读粒度(event_span) ============================
class TestEventSpan:
    def test_week_span_covers_seven_days(self):
        assert dates.event_span("2026-11-01", "用户在2026-11-01那一周有考试") == \
            ("2026-11-01", "2026-11-07", "week")

    def test_month_span_ends_at_month_end(self):
        assert dates.event_span("2028-02-01", "用户在2028-02月要搬家") == \
            ("2028-02-01", "2028-02-29", "month")

    def test_day_fact_is_single_day(self):
        assert dates.event_span("2026-11-04", "用户在2026-11-04有考试") == \
            ("2026-11-04", "2026-11-04", "day")

    @pytest.mark.parametrize("event_date", ["", "not-a-date", None])
    def test_malformed_input_degrades_to_single_day(self, event_date):
        assert dates.event_span(event_date)[2] == "day"


# ============================ 带前缀的月日(本月15号) ============================
class TestMonthPrefixedDay:
    """「本月/这个月/上个月/下个月 + N号」必须落到那一天,不能只落在月首。

    漏这条时「本月」会先被整月区间吃掉,event_date 变成月首、文本变成
    「在2026-09月15号」——日期错半个月,还会被判成整月而永不过期。
    """

    def test_ben_yue_prefixed_day(self):
        assert dates.resolve_event("用户本月15号发工资", ANCHOR) == ("本月15号", "2026-09-15")

    def test_zhe_ge_yue_prefixed_day(self):
        assert dates.resolve_event("用户这个月15号发工资", ANCHOR) == \
            ("这个月15号", "2026-09-15")

    def test_next_month_prefixed_day(self):
        assert dates.resolve_event("用户下个月3号体检", ANCHOR) == ("下个月3号", "2026-10-03")

    def test_last_month_prefixed_day(self):
        assert dates.resolve_event("用户上个月5号搬的家", ANCHOR) == ("上个月5号", "2026-08-05")

    def test_next_month_crosses_year(self):
        assert dates.resolve_event_date("用户下个月3号体检", datetime(2026, 12, 20)) == "2027-01-03"

    def test_day_missing_in_that_month_is_not_guessed(self):
        # 9 月没有 31 号 → 不解析,绝不给一个假日期
        assert dates.resolve_event("用户本月三十一号交房租", ANCHOR) is None

    def test_absolutize_keeps_the_day(self):
        assert dates.absolutize("用户本月15号发工资", ANCHOR) == "用户在2026-09-15发工资"


# ============================ 片段不完整 → 放弃解析 ============================
class TestPartialMatchGuard:
    """规则没吃全时宁可少记一个日期,也不让改写把用户原话写残。

    「上上个周三」只命中「上个周三」、「3个月底」只命中「月底」,交给
    ``text.replace`` 改写就会留下「上在2026-09-02」「3个在2026-09-30」这类
    既非日期也非中文的残字,而且一旦落库不可逆。
    """

    INCOMPLETE = [
        "用户上上个周三去了医院",
        "用户下下个星期有考试",
        "用户上上个月很崩溃",
        "用户下下个月要实习",
        "用户下下个周末回家",
        "用户3个月底要交房租",
    ]

    @pytest.mark.parametrize("text", INCOMPLETE)
    def test_incomplete_phrase_gives_no_date(self, text):
        assert dates.resolve_event(text, ANCHOR) is None

    @pytest.mark.parametrize("text", INCOMPLETE)
    def test_incomplete_phrase_text_is_untouched(self, text):
        assert dates.absolutize(text, ANCHOR) == text

    @pytest.mark.parametrize("text", [
        "用户这个周五答辩",
        "用户本月15号发工资",
        "用户下个月要搬家",
        "用户上上个周三去了医院",
        "用户3个月底要交房租",
    ])
    def test_no_orphan_prefix_left_behind(self, text):
        # 属性:改写后不得留下「这个在」「上在」「3个在」这类孤字粘连
        out = dates.absolutize(text, ANCHOR)
        assert not re.search(r"[上下这本个]在\d{4}-", out), out

    def test_weekly_prefix_is_consumed(self):
        # 「这个周五」整体命中,不能只剩「周五」而把「这个」留在句子里
        assert dates.resolve_event("用户这个周五答辩", ANCHOR) == ("这个周五", "2026-09-11")
        assert dates.absolutize("用户这个周五答辩", ANCHOR) == "用户在2026-09-11答辩"


# ============================ 优先级:具体日 > 粗粒度区间 ============================
class TestSpecificityFirst:
    """一句话里既有「这周/下个月」又有「明天」时,取更具体的那一天。

    反过来会让 event_date 落到锚点之前 —— 「这周很焦虑,明天要考试」被记成
    上周某天,而「明天」还留在文本里,读侧自相矛盾。
    """

    def test_tomorrow_beats_this_week(self):
        assert dates.resolve_event_date("用户这周很焦虑，明天要考试", ANCHOR) == "2026-09-10"

    def test_tomorrow_beats_next_month(self):
        assert dates.resolve_event_date("用户明天有模拟考，下个月要交论文", ANCHOR) == "2026-09-10"

    def test_day_after_tomorrow_beats_next_week(self):
        assert dates.resolve_event_date("用户下周有考试，后天报名", ANCHOR) == "2026-09-11"

    @pytest.mark.parametrize("text", [
        "用户这周有点忙，明天有课",
        "用户下周有考试，后天报名",
        "用户明天有模拟考，下个月交论文",
    ])
    def test_future_only_sentence_never_lands_in_the_past(self, text):
        # 属性:只含未来时间词的句子,解析结果不得早于锚点当天
        assert dates.resolve_event_date(text, ANCHOR) >= "2026-09-09"


# ============================ 跨年回绕:两个方向 ============================
class TestYearRollBothWays:
    def test_year_end_says_january(self):
        assert dates.resolve_event_date("用户1月5号考试", datetime(2026, 12, 28)) == "2027-01-05"

    def test_january_recalls_last_december(self):
        # 1 月初回忆去年 12 月,不能算成今年 12 月(那是 11 个月后的未来)
        assert dates.resolve_event_date("用户12月31号很难过", datetime(2027, 1, 5)) == "2026-12-31"

    def test_near_past_stays_this_year(self):
        assert dates.resolve_event_date("用户9月20号返校", datetime(2026, 10, 28)) == "2026-09-20"

    def test_threshold_boundary(self):
        # 300 天是阈值:299 天按当年(在回忆),301 天回绕到次年
        assert dates.resolve_event_date("用户1月1号体检", datetime(2026, 10, 27)) == "2026-01-01"
        assert dates.resolve_event_date("用户1月1号体检", datetime(2026, 10, 29)) == "2027-01-01"
