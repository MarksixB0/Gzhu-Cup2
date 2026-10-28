# -*- coding: utf-8 -*-
"""记忆「道」(lane):按**时效性**把事实分三类,供读侧分区配额使用。

  | 道 | 语义 | 判据 | 读侧处理 |
  |---|---|---|---|
  | `event`   | 具体事件,有日期 | 带 `event_date` 或含事件词 | 由"问题相关"决定,不受限流 |
  | `state`   | 当前状态,会变 | 含状态词且不含实体词 | reply 用途限量(防情绪碎片挤占档案) |
  | `archive` | 稳定档案,不变 | 其余 | 兜底,按分排序 |

风险内容命中即全道豁免(不参与任何限流)。
"""
from typing import Optional

LANE_ARCHIVE = "archive"
LANE_STATE = "state"
LANE_EVENT = "event"
ALL_LANES = (LANE_ARCHIVE, LANE_STATE, LANE_EVENT)
LANE_ZH = {LANE_ARCHIVE: "档案", LANE_STATE: "状态", LANE_EVENT: "事件"}

# ---- 事件词:出现即视为"某件具体的事"(通常还会带 event_date) ----
EVENT_HINTS = (
    "手术", "住院", "出院", "复查", "复检", "体检", "挂号", "开药", "吃药",
    "考试", "笔试", "面试", "答辩", "开题", "报名", "交材料", "截止",
    "公开课", "家长会", "演出", "比赛", "搬家", "入职", "离职", "出差",
    "生日", "结婚", "旅行", "看电影",
)

# ---- 状态词:情绪与躯体症状(含风险表述) ----
STATE_WORDS = (
    "累", "疲惫", "乏力", "没劲", "提不起劲", "没力气", "没精神", "不想动",
    "不想说话", "不想见人", "发呆", "空", "孤独", "难受", "想哭", "委屈",
    "难过", "低落", "沮丧", "崩溃", "撑不住", "撑不下去", "焦虑", "紧张",
    "担心", "不安", "心慌", "压力", "失眠", "睡不着", "睡不好", "早醒",
    "熬夜", "没胃口", "自责", "迷茫", "麻木", "烦躁", "烦",
    "没意思", "没意义", "不想活", "想死", "绝望", "无助", "被抽空",
)

# ---- 实体词:出现则不判为 state ----
ENTITY_HINTS = (
    "芝麻", "外婆", "外公", "奶奶", "爷爷", "妈妈", "爸爸", "弟弟", "妹妹",
    "哥哥", "姐姐", "室友", "同学", "朋友", "老师", "导师", "辅导员", "咨询师",
    "学校", "大学", "学院", "幼儿园", "公司", "单位",
)

# ---- 风险内容:命中即**全道豁免**(不参与任何限流,安全优先) ----
RISK_CONTENT_MARKERS = (
    "活着没意思", "活着没意义", "不想活", "想死", "想不开", "自杀", "自伤",
    "伤害自己", "结束生命", "结束自己", "了结", "跳楼", "割腕", "一了百了",
)


def _text(value) -> str:
    if isinstance(value, str):
        return value
    return str((value or {}).get("text") or "")


def is_risk_content(value) -> bool:
    """该条是否包含风险原话(命中即不受任何读侧限流)。"""
    t = _text(value)
    return any(w in t for w in RISK_CONTENT_MARKERS)


def has_event_date(value) -> bool:
    if isinstance(value, dict):
        return bool(value.get("event_date"))
    return False


def classify(value) -> str:
    """返回该条事实所属的「道」。纯函数:同一输入永远同一输出。

    判定顺序(有序,勿调换):
      1. 有 event_date 或含事件词 → event(时间是它最强的特征);
      2. 含状态词且**不含实体词** → state;
      3. 其余 → archive。
    """
    t = _text(value)
    if not t:
        return LANE_ARCHIVE
    if has_event_date(value) or any(w in t for w in EVENT_HINTS):
        return LANE_EVENT
    if any(w in t for w in STATE_WORDS) and not any(w in t for w in ENTITY_HINTS):
        return LANE_STATE
    return LANE_ARCHIVE


def counts(values) -> dict:
    """各道的条数统计(排障 / 洞察面板用)。"""
    out = {lane: 0 for lane in ALL_LANES}
    for v in values or []:
        out[classify(v)] = out.get(classify(v), 0) + 1
    return out


def describe(value) -> str:
    """人类可读的一道标签(洞察面板用,不进提示词)。"""
    return LANE_ZH.get(classify(value), "档案")
