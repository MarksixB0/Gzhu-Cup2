# -*- coding: utf-8 -*-
"""相对时间锚定:把记忆文本里的中文相对时间表达解析成绝对日期(纯函数、零 IO)。

以对话发生时刻为锚点,提供两个入口:
  1. resolve_event_date:换算成 ISO 日期(YYYY-MM-DD),换算不出返回 None;
  2. absolutize:把文本里的相对时间词原地改写为绝对说法(「在YYYY-MM-DD」,
     整周/整月保留粒度:「在YYYY-MM-DD那一周」「在YYYY-MM月」),
     使同一事件跨天被提及时措辞稳定,便于去重。

不调 LLM、不触 IO;时间词之外的文本原样保留。
"""
import calendar
import re
from datetime import datetime, timedelta

# 星期几 → datetime.weekday()(周一=0 … 周日=6);「天」与「7」都指周日
_WEEKDAY_CN = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
               "日": 6, "天": 6}
_WEEKDAY_DIGIT = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6}

_CN_DIGITS = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 裸月日离锚点超过这么多天时判为跨年(两侧对称):早于锚点 → 年底说年初,
# 推到下一年;晚于锚点 → 年初在回忆去年,退到上一年。间隔更短的仍按当年理解。
_CROSS_YEAR_MIN_GAP_DAYS = 300

_NUM_TOKEN = r"[0-9一二两三四五六七八九十]+"
# 月份/日份:阿拉伯数字或 1-99 的中文数字(「十」「三十一」这类写法)
_MD_TOKEN = r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})"
_WEEKDAY_TOKEN = r"[一二三四五六日天1-7]"

_RE_MONTH_DAY = re.compile(rf"({_MD_TOKEN})月({_MD_TOKEN})[日号]")
# 「本月15号 / 这个月3号 / 下个月5号」:月前是相对前缀而不是数字,故单独一条。
# 必须排在「整月区间」之前,否则「本月」会先被吃成月首,留下「15号」在残段里。
_RE_MONTH_PREFIX_DAY = re.compile(
    rf"(?P<which>上个?|这个?|本|下个?)月(?P<day>{_MD_TOKEN})[日号]")
_RE_WEEKS_LATER = re.compile(rf"({_NUM_TOKEN})(?:周|星期|礼拜)后")
_RE_DAYS_LATER = re.compile(rf"({_NUM_TOKEN})天后")
# 「下下周X / 下下个星期X / 下下礼拜X」:先于「下周X」匹配(后者是前者的前缀)
_RE_WEEK_AFTER_NEXT = re.compile(rf"下下(?:个)?(?:周|星期|礼拜)(?P<wd>{_WEEKDAY_TOKEN})")
_RE_LAST_WEEK = re.compile(rf"上个?(?:周|星期|礼拜)(?P<wd>{_WEEKDAY_TOKEN})")
_RE_NEXT_WEEK = re.compile(rf"下(?:个)?(?:周|星期|礼拜)(?P<wd>{_WEEKDAY_TOKEN})")
# 「这/本/下周末」:周末落在周六,不跟随周起始约定(周六在两种约定里都是周六)
_RE_WEEKEND = re.compile(r"(?P<which>这|本|下)个?(?:周|星期|礼拜)末")
# 「十月底 / 12月末」:带明确月份的月末
_RE_MONTH_END = re.compile(rf"({_MD_TOKEN})月(?:底|末)")
# 「月底 / 这个月底 / 上个月底 / 下个月底」:只有相对前缀或干脆没有
_RE_MONTH_END_REL = re.compile(r"(?P<which>上个?|这个?|本|下个?)?月(?:底|末)")
# 周区间:后面不接星期几/末,才表示「整周」而不是「某一天」
_RE_WEEK_RANGE = re.compile(r"(?P<which>下下|上个?|这个?|本|下个?)(?:周|星期|礼拜)"
                            r"(?![一二三四五六日天1-7末])")
_RE_MONTH_RANGE = re.compile(r"(?P<which>上个?|这个?|本|下个?)(?:个)?月")
# 「这个/本个」也是口语前缀;漏掉它会让「这个周五」只命中「周五」,改写后留下孤字
_RE_WEEKDAY = re.compile(
    rf"(?:本|这)?(?:个)?(?:星期|礼拜|周)(?P<wd>{_WEEKDAY_TOKEN})")

# 命中的片段左侧若紧邻这些字,说明它只是更长短语的尾巴(如「上上个周三」里
# 的「上个周三」),规则没吃全 —— 见 _is_partial_match。
_PARTIAL_PREFIX_CHARS = "上下这本个"

# 绝对化时的粒度:整周/整月不能写成某一天,否则读回来会被当成"那天的事"
_RE_WEEK_GRANULAR = re.compile(r"^(下下|上个?|这个?|本|下个?)(?:周|星期|礼拜)$")
_RE_MONTH_GRANULAR = re.compile(r"^(上个?|这个?|本|下个?)(?:个)?月$")
# 读侧回读粒度:识别 absolutize 写下的整周/整月形态(见 event_span)
_RE_SPAN_WEEK = re.compile(r"在\d{4}-\d{1,2}-\d{1,2}那一周")
_RE_SPAN_MONTH = re.compile(r"在\d{4}-\d{1,2}月")


def _parse_num(token: str) -> int | None:
    """把数字片段解析成整数(支持阿拉伯数字与 1-99 的中文数字);失败返回 None。"""
    token = str(token or "")
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if set(token) - set("零一二两三四五六七八九十"):
        return None
    if token == "十":
        return 10
    if len(token) == 1:
        return _CN_DIGITS.get(token)
    if token.startswith("十"):
        return 10 + _CN_DIGITS.get(token[1:], 0)
    if token.endswith("十"):
        return _CN_DIGITS.get(token[0], 0) * 10
    tens, ones = token.split("十", 1)
    return _CN_DIGITS.get(tens, 0) * 10 + _CN_DIGITS.get(ones, 0)


def _weekday_num(token: str) -> int | None:
    """把「一…日/天/1-7」映射到 datetime.weekday()(0-6);非法返回 None。"""
    token = str(token or "")
    if token.isdigit():
        return _WEEKDAY_DIGIT.get(token)
    return _WEEKDAY_CN.get(token)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _week_start_index(week_start: "str | None" = None) -> int:
    """一周从周几起算 → datetime.weekday() 口径的序号(周一=0 … 周日=6)。

    为什么需要它:周末(六/日)锚点上,「下周X」在"周日起算"与"周一起算"
    两种约定下整整差 7 天。同一句「下周三」若两次落进不同的周,库内会并存
    两条矛盾的日期 —— 这不是解析错,而是**周起始约定必须写死**,不能替用户猜。

    默认取 "sunday"(国内日历普遍把周日排在第一列,跨日复述可复现到同一周);
    需要周一起算的场景改成 "monday" 即可,不必动业务代码。
    """
    ws = week_start
    if ws is None:
        try:
            from gzhu_cup2.agent.config import config as cfg
            ws = getattr(cfg, "MEMORY_WEEK_START", "sunday")
        except Exception:  # noqa: BLE001 - 配置不可读时退回周一起算(保守)
            ws = "monday"
    return 6 if str(ws or "monday").strip().lower().startswith("sun") else 0


def _as_date(anchor_dt):
    """锚点统一取 date 部分(锚点允许是 datetime 或 date)。"""
    return anchor_dt.date() if isinstance(anchor_dt, datetime) else anchor_dt


def _week_start_date(anchor_dt, week_start: "str | None" = None):
    """锚点所在周的首日(按 _week_start_index 的约定)。"""
    start = _week_start_index(week_start)
    return _as_date(anchor_dt) - timedelta(days=(anchor_dt.weekday() - start) % 7)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """年月前后滚动 delta 个月(自动进位/借位)。"""
    idx = year * 12 + (month - 1) + int(delta)
    return idx // 12, idx % 12 + 1


def _month_end_iso(year: int, month: int) -> str:
    """该年月最后一天的 ISO 日期(2 月自动区分平闰)。"""
    year, month = _shift_month(year, month, 0)
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def _roll_year_if_far(iso: str, anchor_dt) -> str:
    """裸月日离锚点太远时按跨年回绕到相邻年份。

    过去方向:2026-12-28 说「1月5号」,按锚点年份算出来是 2026-01-05(差了近
    一年),实际说的是 2027-01-05 → 推到下一年。
    未来方向:2027-01-05 说「12月31号很难过」,按锚点年份算出来是 2027-12-31
    (往后近一年),实际是回忆去年 → 退到上一年。
    两侧都用同一个阈值:间隔不足 _CROSS_YEAR_MIN_GAP_DAYS 的一律不动。
    """
    try:
        cand = datetime.strptime(iso, "%Y-%m-%d").date()
        gap = (_as_date(anchor_dt) - cand).days
        if gap > _CROSS_YEAR_MIN_GAP_DAYS:
            return cand.replace(year=cand.year + 1).strftime("%Y-%m-%d")
        if gap < -_CROSS_YEAR_MIN_GAP_DAYS:
            return cand.replace(year=cand.year - 1).strftime("%Y-%m-%d")
        return iso
    except (ValueError, TypeError):
        return iso                      # 非法日期(如闰日回绕失败)保持原样


def _weekday_offset(anchor_dt, wd: int, weeks: int = 1,
                    week_start: "str | None" = None) -> int:
    """从锚点到「以本周为 0、偏移 weeks 周的那一周里的星期 wd」的天数偏移。

    weeks: 0 本周、1 下周、2 下下周、-1 上周;结果可为负(落在锚点之前)。
    """
    start = _week_start_index(week_start)
    return (weeks * 7 - (anchor_dt.weekday() - start) % 7 + (wd - start) % 7)


def _is_partial_match(raw: str, matched: str) -> bool:
    """命中的片段是否只是更长时间短语的一段(规则没吃全)。

    例:「上上个周三」只命中「上个周三」、「这个周五」只命中「周五」、
    「本月15号」只命中「本月」、「3个月底」只命中「月底」。这类片段一旦交给
    ``text.replace(matched, ...)`` 改写,会留下孤字或吃掉数字,把落库文本写坏。
    判据只看命中片段的两侧相邻字符,不依赖具体规则。
    """
    if not matched:
        return True
    pos = raw.find(matched)
    if pos < 0:                       # 理论上不会发生;发生了说明片段不可信
        return True
    if pos > 0 and raw[pos - 1] in _PARTIAL_PREFIX_CHARS:
        return True                   # 左侧还有「上下这本个」→ 更长短语的尾巴
    tail = raw[pos + len(matched):]
    if matched.endswith("月") and re.match(rf"{_MD_TOKEN}[日号]", tail):
        return True                   # 「本月」后面紧跟日期 → 更长短语的开头
    return False


def resolve_event(text: str, anchor_dt: datetime,
                  week_start: "str | None" = None) -> tuple[str, str] | None:
    """把 text 里的中文相对时间表达按 anchor_dt 解析成 (原文片段, ISO 日期)。

    返回的**原文片段是 text 里精确出现的那一段字面量**(如「明天」「下周四」,
    正则命中的取 group(0)),保证调用方可以直接 ``text.replace(片段, ...)``
    而不会因字形差异替换失败;没有可解析的时间词时返回 None。

    anchor_dt 是「对话发生的时刻」(固化段第一轮的 ts)—— 相对时间只有锚定到
    「用户说出这句话的那一天」才有意义;锚点缺失/非法、或文本里没有任何
    可解析的时间词时返回 None。

    规则匹配见 ``_resolve_raw``;此处只多做一步**完整性守卫**:命中的片段若
    只是更长短语的一截(见 ``_is_partial_match``),就当解析不出来返回 None。
    宁可少记一个日期,也不让改写把库里的原话写残。

    不可唯一确定的表达(「过段时间」「以后」)仍返回 None。
    本函数保证不抛异常:文本与锚点一律不信任,任何非法输入都落到 None。
    """
    hit = _resolve_raw(text, anchor_dt, week_start=week_start)
    if hit is None:
        return None
    try:
        if _is_partial_match(str(text or ""), hit[0]):
            return None
    except Exception:  # noqa: BLE001 - 守卫失败按"不可信"处理(宁少不脏)
        return None
    return hit


def _resolve_raw(text: str, anchor_dt: datetime,
                 week_start: "str | None" = None) -> tuple[str, str] | None:
    """按规则表逐条匹配(优先级自上而下,多个时间词时取更具体的那条)。

    解析规则:
      9月20号/九月二十号 → 锚点年份(离锚点超过 300 天则跨年回绕);
      本月15号/这个月3号/下个月5号 → 该月的那一天;
      3周后/3天后 → 周/天偏移;下下周X/下周X/上周X → 第 N 周的星期 X;
      大后天/后天/明天/明日/昨天/前天/今天(最具体的单日,先于区间);
      这周末/下周末 → 该周周六;
      十月底/下个月底/月底 → 该月最后一天;
      下周/这周/上周/下个月(不带具体哪天)→ **区间首日**(整周/整月的粒度);
      周X/星期X/礼拜X → 锚点之后最近的那个星期 X(当天已是则 +7)。
    """
    try:
        text = str(text or "")
        if not text.strip():
            return None

        # 1) 绝对月日(锚点年份):「9月20号 / 九月二十号」;非法日期(如 2月30日)→ None
        m = _RE_MONTH_DAY.search(text)
        if m:
            month, day = _parse_num(m.group(1)), _parse_num(m.group(2))
            if month is None or day is None:
                return None
            if not (1 <= month <= 12 and 1 <= day <= 31):
                return None
            try:
                hit = _iso(anchor_dt.replace(month=month, day=day))
            except ValueError:
                return None
            return (m.group(0), _roll_year_if_far(hit, anchor_dt))

        # 1b) 带相对前缀的月日:「本月15号 / 这个月3号 / 下个月5号」。
        #     必须早于整月区间,否则「本月」会先被吃成月首。
        m = _RE_MONTH_PREFIX_DAY.search(text)
        if m:
            day = _parse_num(m.group("day"))
            if day is None or not 1 <= day <= 31:
                return None
            which = str(m.group("which") or "")
            delta = -1 if which.startswith("上") else (1 if which.startswith("下") else 0)
            year, month = _shift_month(anchor_dt.year, anchor_dt.month, delta)
            try:
                return (m.group(0), _iso(datetime(year, month, day)))
            except ValueError:        # 该月没有这一天(如 2 月 30 号)
                return None

        # 2) 「X周后」
        m = _RE_WEEKS_LATER.search(text)
        if m:
            n = _parse_num(m.group(1))
            if n is not None and 0 <= n <= 999:
                return (m.group(0), _iso(anchor_dt + timedelta(weeks=n)))

        # 3) 「X天后」
        m = _RE_DAYS_LATER.search(text)
        if m:
            n = _parse_num(m.group(1))
            if n is not None and 0 <= n <= 9999:
                return (m.group(0), _iso(anchor_dt + timedelta(days=n)))

        # 4) 「下下周X / 下下个星期X」
        m = _RE_WEEK_AFTER_NEXT.search(text)
        if m:
            wd = _weekday_num(m.group("wd"))
            if wd is not None:
                days = _weekday_offset(anchor_dt, wd, 2, week_start)
                return (m.group(0), _iso(anchor_dt + timedelta(days=days)))

        # 5) 「下周X / 下个星期X」:先到下一周起点(按 _week_start_index 的约定),再加星期偏移
        m = _RE_NEXT_WEEK.search(text)
        if m:
            wd = _weekday_num(m.group("wd"))
            if wd is not None:
                days = _weekday_offset(anchor_dt, wd, 1, week_start)
                return (m.group(0), _iso(anchor_dt + timedelta(days=days)))

        # 6) 「上周X / 上个星期X」:上一周的同一天,可能落在锚点之前
        m = _RE_LAST_WEEK.search(text)
        if m:
            wd = _weekday_num(m.group("wd"))
            if wd is not None:
                days = _weekday_offset(anchor_dt, wd, -1, week_start)
                return (m.group(0), _iso(anchor_dt + timedelta(days=days)))

        # 7) 日偏移:单日、无歧义,最具体,故先于周末/月末/周区间/月区间。
        #    否则「这周很焦虑,明天要考试」会被「这周」抢走,把明天的事记到周首。
        #    (注意「大后天」必须先于「后天」匹配)
        if "大后天" in text:
            return ("大后天", _iso(anchor_dt + timedelta(days=3)))
        if "后天" in text:
            return ("后天", _iso(anchor_dt + timedelta(days=2)))
        if "明天" in text:
            return ("明天", _iso(anchor_dt + timedelta(days=1)))
        if "明日" in text:
            return ("明日", _iso(anchor_dt + timedelta(days=1)))
        if "昨天" in text:
            return ("昨天", _iso(anchor_dt - timedelta(days=1)))
        if "前天" in text:
            return ("前天", _iso(anchor_dt - timedelta(days=2)))
        if "今天" in text:
            return ("今天", _iso(anchor_dt))

        # 8) 「这周末 / 本周末 / 下周末」:锚定到该周周六(周六不随周起始约定改变)
        m = _RE_WEEKEND.search(text)
        if m:
            days = (5 - anchor_dt.weekday()) % 7          # 到最近的那个周六
            if str(m.group("which")) == "下":
                days += 7
            return (m.group(0), _iso(anchor_dt + timedelta(days=days)))

        # 9) 月末:带明确月份优先(「十月底」),其次相对月份(「月底」「下个月底」)
        m = _RE_MONTH_END.search(text)
        if m:
            month = _parse_num(m.group(1))
            if month is not None and 1 <= month <= 12:
                hit = _month_end_iso(anchor_dt.year, month)
                return (m.group(0), _roll_year_if_far(hit, anchor_dt))
        m = _RE_MONTH_END_REL.search(text)
        if m:
            which = str(m.group("which") or "")
            delta = -1 if which.startswith("上") else (1 if which.startswith("下") else 0)
            year, month = _shift_month(anchor_dt.year, anchor_dt.month, delta)
            return (m.group(0), _month_end_iso(year, month))

        # 10) 周区间(不带星期几):「下周有考试」锚到该周首日,粒度按周而非某一天
        m = _RE_WEEK_RANGE.search(text)
        if m:
            which = str(m.group("which") or "")
            if which == "下下":
                weeks = 2
            elif which.startswith("下"):
                weeks = 1
            elif which.startswith("上"):
                weeks = -1
            else:                                          # 这周 / 本周
                weeks = 0
            base = _week_start_date(anchor_dt, week_start)
            return (m.group(0), _iso(base + timedelta(weeks=weeks)))

        # 11) 月区间(不带具体哪天):「下个月要搬家」锚到该月首日
        m = _RE_MONTH_RANGE.search(text)
        if m:
            which = str(m.group("which") or "")
            delta = -1 if which.startswith("上") else (1 if which.startswith("下") else 0)
            year, month = _shift_month(anchor_dt.year, anchor_dt.month, delta)
            return (m.group(0), f"{year:04d}-{month:02d}-01")

        # 12) 「周X / 星期X / 礼拜X」:锚点之后最近的那个星期 X(当天已是则 +7)
        m = _RE_WEEKDAY.search(text)
        if m:
            wd = _weekday_num(m.group("wd"))
            if wd is not None:
                days = (wd - anchor_dt.weekday()) % 7
                return (m.group(0), _iso(anchor_dt + timedelta(days=days or 7)))
        return None
    except Exception:  # noqa: BLE001 - 纯解析函数,输入不信任,任何异常都落 None
        return None


def resolve_event_date(text: str, anchor_dt: datetime,
                       week_start: "str | None" = None) -> str | None:
    """把 text 里的中文相对时间表达按 anchor_dt 解析成 ISO 日期(YYYY-MM-DD)。

    是 resolve_event 的薄包装:只取解析结果里的日期部分;解析不出返回 None,
    调用方应照常落库事实,只是不带事件日期。行为与签名保持向后兼容
    (``week_start`` 缺省读 config.MEMORY_WEEK_START)。
    """
    hit = resolve_event(text, anchor_dt, week_start=week_start)
    return hit[1] if hit is not None else None


def _render_absolute(matched: str, iso: str) -> str:
    """把命中的时间词改写成绝对说法,按粒度分三档。

    整周/整月(「下周」「下个月」)本就没有具体哪一天,写成某一天会让读侧
    把整段时间读成"那天的事",故保留粒度:在YYYY-MM-DD那一周 / 在YYYY-MM月。
    """
    if _RE_WEEK_GRANULAR.match(matched):
        return f"在{iso}那一周"
    if _RE_MONTH_GRANULAR.match(matched):
        return f"在{iso[:7]}月"
    return f"在{iso}"


def absolutize(text: str, anchor_dt: datetime,
               week_start: "str | None" = None) -> str:
    """把 text 里的相对时间词原地改写为绝对说法,落库 text 自带日期。

    例:「用户明天有考试」@ 2026-09-09 → 「用户在2026-09-10有考试」;
        「用户下周有考试」       → 「用户在2026-09-13那一周有考试」(整周粒度)。
    解析不出任何时间词时**原样返回 text(不改写)**;任何异常(含非法锚点)
    都原样返回 text,绝不抛异常 —— 绝对化失败只是少一层加固,事实照常入库。
    只替换**第一次**命中的时间词,其余文本一律不动。
    """
    try:
        raw = str(text or "")
        hit = resolve_event(raw, anchor_dt, week_start=week_start)
        if hit is None:
            return raw
        matched, iso = hit
        return raw.replace(matched, _render_absolute(matched, iso), 1)
    except Exception:  # noqa: BLE001 - 纯文本改写,任何异常都原样返回
        try:
            return str(text or "")
        except Exception:  # noqa: BLE001 - str() 都失败(极端非法输入)只能给空串
            return ""


def event_span(event_date: str, text: str = "") -> tuple[str, str, str]:
    """事件的覆盖区间:(起始日, 结束日, 粒度),日期均为 ISO 字符串。

    整周/整月的表达落库时 event_date 只存了区间首日;读侧要判断"这事过没过"
    必须看区间末端,否则一周还没过完就会被读成「已过去」。粒度按 absolutize
    写下的文本形态识别(「在YYYY-MM-DD那一周」「在YYYY-MM月」);
    识别不出或输入非法时,区间退化为单日(day)。
    """
    iso = str(event_date or "")
    try:
        start = datetime.strptime(iso, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return iso, iso, "day"
    try:
        raw = str(text or "")
        if _RE_SPAN_WEEK.search(raw):
            return iso, _iso(start + timedelta(days=6)), "week"
        if _RE_SPAN_MONTH.search(raw):
            return iso, _month_end_iso(start.year, start.month), "month"
    except Exception:  # noqa: BLE001 - 读侧提示是增强,失败退化为单日
        pass
    return iso, iso, "day"


def anchor_datetime(pending: dict | None) -> datetime:
    """固化段的锚定时刻:取第一轮的 ts;缺失/非法时回落当前时刻。"""
    try:
        turns = (pending or {}).get("turns") or []
        ts = turns[0].get("ts") if turns else None
        return datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now()
