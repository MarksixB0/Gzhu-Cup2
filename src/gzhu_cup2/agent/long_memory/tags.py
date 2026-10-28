# -*- coding: utf-8 -*-
"""记忆「标签词表 + 纯函数」:闭合词表、状态弧计算、落库前清洗。

设计约定(配合 core/schemas.MemoryExtraction 的标签卡):
  - 本模块零 IO、零 LLM、不 import store —— 只有词表与确定性纯函数,可离线单测;
  - 一切进入长期记忆的标签都在这里收敛为「闭合词表 + 粗粒度档」,
    LLM 输出的杂音(未知主题/未知维度/超长引用)在落库前被清洗掉;
  - 维度账本只记「自然出现」的稀疏证据,第 9 条(自杀意念/C-SSRS)不走维度账本,
    由前台风险快闸 / 危机子系统直接负责。
"""
import re
from typing import Iterable, List, Optional

from gzhu_cup2.agent.core.schemas import FACT_CATEGORIES


# ============================== 情绪词表 ==============================
NEG_EMOTIONS = frozenset({"sad", "anxious", "angry", "frustrated", "fearful"})
POS_EMOTIONS = frozenset({"happy", "hopeful"})
KNOWN_EMOTIONS = NEG_EMOTIONS | POS_EMOTIONS | {"neutral"}

EMOTION_ZH = {
    "happy": "开心", "sad": "悲伤", "anxious": "焦虑", "angry": "愤怒",
    "frustrated": "沮丧", "hopeful": "抱有希望", "fearful": "恐惧", "neutral": "平静",
}

# ============================== 主题词表 ==============================
TOPICS_ALLOWED = frozenset({
    "school", "work", "family", "relationship", "social",
    "health", "sleep", "self", "finance", "other",
})
TOPIC_ZH = {
    "school": "学业", "work": "工作", "family": "家庭", "relationship": "亲密关系",
    "social": "人际社交", "health": "健康", "sleep": "睡眠", "self": "自我",
    "finance": "经济", "other": "其他",
}
# 与社会性/支持网络相关的主题(画像「社交倾向」用)
SOCIAL_TOPICS = frozenset({"social", "family", "relationship"})

MAX_TOPICS = 3
MAX_SHORTLIST = 3
MAX_SHORT_LEN = 40
MAX_QUOTE_LEN = 80

# ============================== 维度注册表 ==============================
# 由 PHQ-9 / GAD-7 条目降维成的可观察维度(量表只作字典用)。
# 自杀意念不在本表,由风险/危机子系统处理。
DIMENSIONS = (
    {"key": "phq_anhedonia", "group": "phq", "name": "兴趣缺失(对喜欢的事提不起劲)"},
    {"key": "phq_mood", "group": "phq", "name": "情绪低落/沮丧/绝望感"},
    {"key": "phq_sleep", "group": "phq", "name": "睡眠变化(难入睡/早醒/睡太多)"},
    {"key": "phq_fatigue", "group": "phq", "name": "精力不足/易疲劳/做什么都费劲"},
    {"key": "phq_appetite", "group": "phq", "name": "食欲变化(下降/暴食)"},
    {"key": "phq_worth", "group": "phq", "name": "自我价值感低/自责内疚"},
    {"key": "phq_focus", "group": "phq", "name": "注意力难集中/脑子转不动"},
    {"key": "phq_motor", "group": "phq", "name": "行动/言语变慢或坐立不安"},
    {"key": "gad_nervous", "group": "gad", "name": "紧张不安/心慌"},
    {"key": "gad_relax", "group": "gad", "name": "难以放松/脑子停不下来"},
    {"key": "gad_worry", "group": "gad", "name": "过度担忧各种事情"},
    {"key": "gad_irritable", "group": "gad", "name": "易怒/没耐心"},
    {"key": "gad_restless", "group": "gad", "name": "坐立难安/静不下来"},
    {"key": "gad_fear", "group": "gad", "name": "害怕坏事发生/不祥预感"},
    {"key": "gad_somatic", "group": "gad", "name": "身体紧张反应(心跳快/手抖/出汗/胸闷/胃不适)"},
)
DIM_KEYS = tuple(d["key"] for d in DIMENSIONS)
DIM_NAMES = {d["key"]: d["name"] for d in DIMENSIONS}


# ============================== 清洗:落库前收敛 ==============================
# 事实分类的近义词表。顺序即优先级,先命中者胜:健康与情绪存在交集
# (如「焦虑」既可算生理也可算情绪),按此顺序归入情绪一侧。
_CATEGORY_KEYWORDS = (
    ("health", (
        "健康", "身体", "睡眠", "失眠", "睡不着", "熬夜", "体检", "医院", "看病",
        "吃药", "生病", "发烧", "疼痛", "疼", "胃", "心脏", "过敏", "生理期", "体重",
        "health", "sleep", "medical", "illness", "symptom",
    )),
    ("emotion", (
        "情绪", "心情", "感受", "难过", "开心", "难受", "压力", "焦虑", "孤独", "委屈",
        "烦", "害怕", "抑郁", "崩溃", "自卑", "内疚", "自责",
        "emotion", "mood", "feeling", "anxious", "stress", "depress",
    )),
    ("school", (
        "学校", "学业", "成绩", "绩点", "考试", "答辩", "论文", "导师", "老师", "同学",
        "考研", "读研", "保研", "毕业", "专业", "课程", "作业", "宿舍", "社团",
        "school", "study", "exam", "thesis", "academic", "course",
    )),
    ("work", (
        "工作", "上班", "公司", "同事", "老板", "领导", "加班", "实习", "项目", "求职",
        "面试", "升职", "离职", "工资", "职场", "客户",
        "work", "job", "career", "office", "colleague",
    )),
    ("family", (
        "家人", "家庭", "父母", "妈妈", "爸爸", "爸妈", "母亲", "父亲", "爷爷", "奶奶",
        "外婆", "外公", "哥哥", "姐姐", "弟弟", "妹妹", "女儿", "儿子", "孩子",
        "family", "parent", "mother", "father",
    )),
    ("relationship", (
        "朋友", "室友", "恋人", "男朋友", "女朋友", "男友", "女友", "对象", "恋爱",
        "分手", "吵架", "社交", "闺蜜", "人际",
        "relationship", "friend", "partner", "social",
    )),
    ("preference", (
        "喜欢", "讨厌", "偏好", "爱吃", "不吃", "最爱", "口味", "习惯用",
        "prefer", "like", "dislike", "favorite", "hobby",
    )),
    ("plan", (
        "计划", "打算", "安排", "报名", "目标", "将要", "预计", "准备去",
        "plan", "intend", "schedule", "goal",
    )),
    ("life", (
        "生活", "日常", "习惯", "居住", "搬家", "饮食", "租房", "家务", "宠物", "作息",
        "life", "daily", "habit", "living",
    )),
)


def clean_fact_category(raw: Optional[str], text: Optional[str] = None) -> str:
    """把事实分类收敛到 ``FACT_CATEGORIES`` 之一。

    先按原值匹配(合法值原样返回),再按关键词映射词表外的说法;原值无信息时
    用事实文本兜底。全部未命中归 ``other`` —— 分类只影响检索加权,归错远轻于
    因校验失败导致整段记忆被反复重试直至丢弃。
    """
    for candidate in (raw, text):
        s = str(candidate or "").strip().lower()
        if not s:
            continue
        if s in FACT_CATEGORIES:
            return s
        for cat, words in _CATEGORY_KEYWORDS:
            if any(w in s for w in words):
                return cat
    return "other"


def clamp_topics(topics: Optional[Iterable]) -> List[str]:
    """主题收敛:只留允许词表内的值,去重保序,最多 MAX_TOPICS 个。"""
    if not topics:
        return []
    out: list[str] = []
    for t in topics:
        t = str(t or "").strip()
        if t in TOPICS_ALLOWED and t not in out:
            out.append(t)
        if len(out) >= MAX_TOPICS:
            break
    return out


def clamp_short_list(items: Optional[Iterable], limit: int = MAX_SHORTLIST,
                     max_len: int = MAX_SHORT_LEN) -> List[str]:
    """简短列表收敛:每条截断 max_len、去重保序、最多 limit 条。"""
    if not items:
        return []
    out: list[str] = []
    for it in items:
        s = " ".join(str(it or "").split())
        if not s:
            continue
        if len(s) > max_len:
            s = s[:max_len]
        if s not in out:
            out.append(s)
        if len(out) >= max(0, int(limit)):
            break
    return out


def clean_evidence(evidence: Optional[dict]) -> dict:
    """维度证据收敛:只留注册表内维度;level 收敛到 0-3(0 视为无证据丢弃);引用截断。

    返回 {维度key: {"level": int, "quote": str}} —— 均为「有效证据」条目。
    """
    if not evidence:
        return {}
    out: dict[str, dict] = {}
    for key, item in evidence.items():
        if key not in DIM_NAMES:
            continue
        if isinstance(item, dict):
            level = item.get("level")
            quote = item.get("quote") or ""
        else:  # pydantic DimensionEvidence 对象
            level = getattr(item, "level", None)
            quote = getattr(item, "quote", None) or ""
        try:
            level = int(level)
        except (TypeError, ValueError):
            continue
        if level <= 0:  # 0 = 提及但无困扰,不入账
            continue
        if level > 3:  # 非法超高 → 钳到 3(大概率是「严重」意图),不让单个脏值炸掉整段
            level = 3
        quote = " ".join(str(quote).split())[:MAX_QUOTE_LEN]
        out[key] = {"level": level, "quote": quote}
    return out


_MAX_FACET_LEN = 200


def clean_crisis_facets(facets) -> dict:
    """危机四要素收敛:只保留非空、压缩空白、截断的分面 → {facet: quote}。

    返回空 dict 表示该段**没有 LLM 抽出的危机分面**。
    ⚠ 注意"分面为空"≠"不留痕":``store.commit_segment`` 还有一条不依赖 LLM 的兜底路径 ——
    段内出现 high/crisis 轮次、或只留了自由文本 risk_note 时,会以 ``facet="signal"``
    直接记用户原话(见 store 2.6)。四分面与 signal 是**并行的两条写入路径**,不要在这里合并。
    纯函数、零 IO,可离线单测。
    """
    if facets is None:
        return {}
    out: dict[str, str] = {}
    for facet in ("ideation", "intent", "plan", "behavior"):
        raw = " ".join(str(getattr(facets, facet, "") or "").split())
        if not raw:
            continue
        out[facet] = raw[:_MAX_FACET_LEN]
    return out


# ============================== 摘要清洗(防自我强化闭环) ==============================
# 以「助手」为陈述主体的句子:会被下一轮读回 prompt,若其中含助手的推测/建议,
# 就会形成「推测 → 固化 → 再注入 → 再被当成事实」的闭环,必须落库前剔除。
_ASSISTANT_SUBJECTS = ("助手", "系统", "AI", "ai", "模型", "机器人")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")
_DIGEST_MAX_LEN = 300


def clean_digest(text, max_len: int = _DIGEST_MAX_LEN) -> str:
    """摘要落库前收敛:剔除「以助手为陈述主体」的句子,压缩空白并截断长度。

    逐句判断,只丢句子开头即助手/系统/AI 的句子;若整段被丢掉则退回原文。
    纯函数、零 IO。
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    kept: list[str] = []
    for sent in _SENT_SPLIT.split(raw):
        s = sent.strip()
        if not s:
            continue
        if any(s.startswith(w) for w in _ASSISTANT_SUBJECTS):
            continue
        kept.append(s)
    out = "".join(kept).strip() or raw
    return out[:max_len]


# ============================== 状态弧(确定性计算) ==============================
def emotion_valence(emotion: Optional[str]) -> int:
    """情绪→价效:负=-1,正=+1,其余(平静/未知)=0。"""
    e = str(emotion or "").strip()
    if e in NEG_EMOTIONS:
        return -1
    if e in POS_EMOTIONS:
        return 1
    return 0


def meta_valence(meta: Optional[dict]) -> int:
    """单轮 meta → 价效:优先情绪标签,缺失时退回 negative_score 粗判。"""
    meta = meta or {}
    emo = meta.get("emotion")
    if emo:
        v = emotion_valence(emo)
        if v != 0:
            return v
    try:
        neg = float(meta.get("negative_score") or 0.0)
    except (TypeError, ValueError):
        neg = 0.0
    if neg >= 0.6:
        return -1
    if neg <= 0.33:
        return 1
    return 0


def _band(v: Optional[float], lo: float, hi: float) -> str:
    if v is None:
        return "unknown"
    if v < lo:
        return "low"
    if v < hi:
        return "mid"
    return "high"


def state_arc(turns: Optional[list]) -> dict:
    """由一段对话的逐轮 meta 计算「状态弧」(确定性,非 LLM)。

    返回:
      dominant_emotion  段内主导情绪标签(无有效标签时 neutral)
      valence_arc       段内情绪走向: rising / falling / stable
      emotion_shift     {"from": 起始标签, "to": 结束标签}
      arousal           段内平均强度档: low / mid / high / unknown
      sample_n          参与计算的轮数
    """
    turns = turns or []
    metas = [dict(t.get("meta") or {}) for t in turns]
    metas = [m for m in metas if m]
    n = len(metas)

    labels: list[str] = []
    for m in metas:
        e = str(m.get("emotion") or "").strip()
        if e in KNOWN_EMOTIONS:
            labels.append(e)
    dominant = "neutral"
    if labels:
        from collections import Counter
        dominant = Counter(labels).most_common(1)[0][0]

    valences = [meta_valence(m) for m in metas]
    v_arc = "stable"
    if len(valences) >= 2:
        half = max(1, (len(valences) + 1) // 2)
        early = sum(valences[:half]) / len(valences[:half])
        late = sum(valences[half:]) / len(valences[half:])
        diff = late - early
        if diff > 0.2:
            v_arc = "rising"
        elif diff < -0.2:
            v_arc = "falling"

    shift_from = labels[0] if labels else "neutral"
    shift_to = labels[-1] if labels else "neutral"

    intensities = []
    for m in metas:
        try:
            v = float(m.get("intensity"))
            if v == v:  # 排除 NaN
                intensities.append(max(0.0, min(1.0, v)))
        except (TypeError, ValueError):
            continue
    arousal = _band((sum(intensities) / len(intensities)) if intensities else None,
                    0.33, 0.66)

    return {
        "dominant_emotion": dominant,
        "valence_arc": v_arc,
        "emotion_shift": {"from": shift_from, "to": shift_to},
        "arousal": arousal,
        "sample_n": n,
    }


def episode_is_negative(ep: Optional[dict]) -> bool:
    """一段 episode 是否「负向/有风险」(供触发挖掘、画像与关怀判断)。

    任一满足即 True:受保护/risk 类别、主导情绪为负、段内走向 falling。
    """
    ep = ep or {}
    if ep.get("protected") or ep.get("category") == "risk":
        return True
    dom = str(ep.get("dominant_emotion") or "").strip()
    if dom in NEG_EMOTIONS:
        return True
    return ep.get("valence_arc") == "falling"
