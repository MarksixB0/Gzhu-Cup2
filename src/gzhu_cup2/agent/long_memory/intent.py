# -*- coding: utf-8 -*-
"""问题意图识别:把用户这句话映射成 主题(topic) + 字面词(term)。

本模块复用 `tags.TOPICS_ALLOWED` 作为闭合主题空间,并给出命中的字面词,
供 `relevance.py` 做直接匹配。

约定:
  - 零 LLM、零 IO、纯函数,可离线穷举单测;
  - 只认显式词面,不做句法分析;
  - topics ⊆ tags.TOPICS_ALLOWED(由单测锁死)。
"""
from typing import Optional

from gzhu_cup2.agent.long_memory.tags import TOPICS_ALLOWED

# 主题 → 触发词(全部为多字词,避免单字"学/钱"这类误命中)
TOPIC_TERMS: dict[str, tuple] = {
    "school": ("学校", "大学", "学院", "专业", "课程", "上课", "课堂", "老师", "导师",
               "考试", "笔试", "面试", "成绩", "作业", "论文", "答辩", "开题",
               "毕业", "实习", "教资", "教招", "编制", "教材", "学分", "考研"),
    "work": ("工作", "上班", "兼职", "同事", "老板", "加班", "入职", "离职", "工资"),
    "family": ("家人", "家里", "家庭", "妈妈", "爸爸", "父母", "外婆", "外公",
               "奶奶", "爷爷", "弟弟", "妹妹", "哥哥", "姐姐", "亲戚", "老家"),
    "health": ("身体", "健康", "生病", "医院", "医生", "药", "体检", "复查", "复检",
               "手术", "住院", "出院", "失眠", "睡不着", "睡眠", "头疼", "胃", "过敏"),
    "sleep": ("睡", "失眠", "睡不着", "早醒", "熬夜", "作息", "梦"),
    "relationship": ("对象", "恋人", "男朋友", "女朋友", "伴侣", "亲密"),
    "social": ("朋友", "室友", "同学", "社交", "聚会", "社团", "人际"),
    "self": ("自己", "自我", "价值", "自信", "没用", "意义", "性格"),
    "finance": ("钱", "花销", "生活费", "经济", "存款", "穷", "开销"),
}

# 日期/时间类提问:命中后,带 event_date 的事实获得额外加权
DATE_QUESTION_HINTS = (
    "什么时候", "哪天", "哪一天", "几号", "多久", "几天后", "什么时候", "几点",
    "时间", "日期", "截止",
)


def detect(query: Optional[str]) -> dict:
    """识别问题意图。

    返回:
      topics        命中到的主题列表(⊆ tags.TOPICS_ALLOWED,按命中词数降序)
      terms         命中到的字面词(用于与事实文本直接比对)
      date_question 是否在问时间/日期
    无 query / 无命中时返回空结构(调用方据此退回静态排序)。
    """
    q = str(query or "").strip()
    if not q:
        return {"topics": [], "terms": [], "date_question": False}

    hits: dict[str, list] = {}
    for topic, words in TOPIC_TERMS.items():
        got = [w for w in words if w in q]
        if got:
            hits[topic] = got
    topics = [t for t, _ in sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    # 字面词:去重保序(长词在前,便于"外婆"优先于"婆"这类重叠)
    terms: list[str] = []
    for w in sorted({w for got in hits.values() for w in got}, key=len, reverse=True):
        if w not in terms:
            terms.append(w)
    return {
        "topics": [t for t in topics if t in TOPICS_ALLOWED],
        "terms": terms,
        "date_question": any(w in q for w in DATE_QUESTION_HINTS),
    }
