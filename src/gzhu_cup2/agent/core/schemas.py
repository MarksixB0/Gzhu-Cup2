from pydantic import BaseModel, Field
from typing import Optional, Literal, List


class AnalyzeEmotionResult(BaseModel):
    """情绪分析结构化输出模型。"""
    emotion: Literal["happy", "sad", "anxious", "angry", "frustrated", "hopeful", "fearful", "neutral"] = Field(description="识别出用户当前核心情绪标签")

    intensity: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="用户当前情绪激烈程度，基于用词，标点符号和语气综合判断，0为情绪很淡，1为情绪激烈"
    )

    description: str = Field(description="对用户情绪客观简洁的文字描述，不超过30字")

    negative_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="负面情绪倾向评分，0为完全正面，1为极度负面"
    )


"""以下为长期记忆:结构化 LLM 输出模型(供固化阶段 with_structured_output 使用)。"""
# 事实分类的合法取值。字段本身放宽为字符串:模型偶尔给出词表外的近义词
# (sleep、学业之类),严格枚举会让整段抽取校验失败、该段记忆反复重试直至丢弃;
# 落库前由 long_memory.tags.clean_fact_category 收敛到这里列出的取值。
FACT_CATEGORIES = (
    "life", "family", "school", "work", "health",
    "relationship", "emotion", "preference", "plan", "other",
)


class MemoryFact(BaseModel):
    """一条关于用户的稳定事实(只记录用户本人的信息,不记录助手说过的话)。"""

    text: str = Field(description="事实内容,一句话")
    category: str = Field(
        default="other",
        description="事实类别,取以下之一:" + "、".join(FACT_CATEGORIES),
    )
    importance: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="该事实对长期了解/陪伴用户的价值(0-1);越高越应长期保留",
    )


class CrisisFacets(BaseModel):
    """危机四要素(仅在用户【明确】表达时填写;每条必须是用户原话摘录,空=未出现该面)。

    独立于通用标签卡存储:永不参与情绪/趋势聚合,单独计数、单独保护、单独升级。
    四要素(由轻到重):意念(想到)→ 意图(想去做)→ 计划(方式/时间/地点)→ 行为(已发生)。
    注意:字段故意宽松(字符串),合法性由 long_memory.tags 落库前清洗收敛。
    """

    ideation: str = Field(default="", max_length=200,
                          description="想伤害自己/结束生命的想法(用户原话摘录;无则留空)")
    intent: str = Field(default="", max_length=200,
                        description="付诸行动的意图/决心(用户原话摘录;无则留空)")
    plan: str = Field(default="", max_length=200,
                      description="具体计划:方式/时间/地点/准备(用户原话摘录;无则留空)")
    behavior: str = Field(default="", max_length=200,
                          description="已发生的自伤/自杀尝试(用户原话摘录;无则留空)")
    # ---- 段级补充(非分面;供危机确认链的 denied/留痕分支使用) ----
    denied_quote: str = Field(default="", max_length=200,
                              description="用户明确否认危机的原话摘录(如'我没有想不开,别担心';无则留空)")
    uncertain_quote: str = Field(default="", max_length=200,
                                 description="疑似但不确定是否为危机表达的原话摘录(只留痕,不参与升级;无则留空)")


class MemoryExtraction(BaseModel):
    """固化阶段的组合输出:整段摘要 + 抽取事实 + 风险备注 + 标签卡 + 危机分面。

    topics / dimension_evidence / triggers / coping 均为可选的标签卡内容;
    字段故意宽松(字符串而非严格枚举),避免个别非法输出导致整段固化失败,
    合法性由 long_memory.tags 的清洗函数在落库前收敛(可靠性优先)。
    """

    digest: str = Field(description="对这段对话的客观摘要(2-4 句,中文):用户当时的状态、聊了哪些值得跨会话记住的事。"
                                    "只描述用户本人:不记录助手说过/做过什么,也不把助手的推测当成事实")
    facts: List[MemoryFact] = Field(default_factory=list, description="从对话中抽取的稳定用户事实(可为空)")
    importance: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="这段对话对长期记忆的总体价值(0-1)",
    )
    risk_note: str = Field(
        default="",
        description="若对话中出现自伤/伤人/危机信号,简短记录;没有则留空",
    )
    # ---- 危机四要素(与通用标签卡解耦;未出现时为空对象) ----
    crisis: Optional["CrisisFacets"] = Field(
        default=None,
        description="危机四要素:仅在用户明确表达自杀/自伤意念、意图、计划或行为时填写原话摘录;没有则留空,严禁猜测",
    )
    # ---- 标签卡(以下全部可选;词表收敛见 long_memory/tags.py) ----
    topics: List[str] = Field(
        default_factory=list,
        description="本段对话主题,从固定词表中选,最多3个:school/work/family/relationship/social/health/sleep/self/finance/other",
    )
    dimension_evidence: dict = Field(
        default_factory=dict,
        description="本段对话中『自然出现』的量表维度证据(稀疏):{维度key: {level: 0-3, quote: 原文}};没提到的维度不要填",
    )
    triggers: List[str] = Field(
        default_factory=list,
        description="用户提到过、引起情绪变化的具体事情(简短短语,最多3条)",
    )
    coping: List[str] = Field(
        default_factory=list,
        description="用户提到过的应对/求助方式(简短短语,最多3条)",
    )


class ReviewEvidence(BaseModel):
    """复核调查的证据条目:一句可核查的事实 + 数据摘录 + 来源。"""
    fact: str = Field(default="", max_length=200,
                      description="一句可核查的事实描述(必须来自案卷数据,严禁编造)")
    quote: str = Field(default="", max_length=300,
                       description="支撑该事实的数据/原话摘录,照抄案卷")
    source: str = Field(default="", max_length=40,
                        description="来源:score/signals/slot_baseline/similar_days/risk_counts")


class ReviewVerdict(BaseModel):
    """复核调查通道的结论(结论写回账本,不直接改档位)。"""
    conclusion: Literal["support", "refute", "watch"] = Field(
        description="support=支持关注升级 / refute=证据不足以支持关注 / watch=证据不足但需继续观察")
    confidence: Literal["low", "mid", "high"] = Field(
        default="low", description="结论置信档")
    evidence: List[ReviewEvidence] = Field(
        default_factory=list,
        description="证据表:每条必须引用案卷里的确定性数据,无据不得开条")
    conflicts: List[str] = Field(
        default_factory=list,
        description="案卷中互相矛盾的证据描述(最多5条)")
    note: str = Field(default="", max_length=400,
                      description="一句话复核说明(结论理由)")
