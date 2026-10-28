"""长期记忆固化阶段的提示词构造(与记忆子域强相关,故收在本包而非 prompts/)。"""
from langchain_core.messages import SystemMessage, HumanMessage

from gzhu_cup2.agent.long_memory.tags import DIMENSIONS, TOPICS_ALLOWED

_TOPICS_TEXT = "、".join(sorted(TOPICS_ALLOWED))


def _dims_text() -> str:
    lines = []
    for d in DIMENSIONS:
        group = "抑郁(PHQ)" if d["group"] == "phq" else "焦虑(GAD)"
        lines.append(f"- {d['key']}: {d['name']} [归属:{group}]")
    return "\n".join(lines)


def build_memory_extraction_prompt(user_input_text: str,
                                   anchor_date_text: str | None = None,
                                   existing_facts: list | None = None) -> list:
    """把一段待固化的对话流水整理成提示,要求 LLM 结构化输出 MemoryExtraction。

    anchor_date_text:本段对话发生日期的人类可读描述(调用方用段首轮次 ts 渲染)。
    注入后提取器才知道事实里的「明天/下周」是相对哪一天;同时要求它**原样保留
    时间词** —— 绝对日期的换算由 Python 侧的 dates.resolve_event_date 完成
    (确定性),不能让 LLM 自己换算(算术不可靠,且会破坏两样本投票的一致性)。

    existing_facts:库里**已记下**的事实文本(未归档,紧凑清单,由调用方限条数)。
    为什么必须给:没有它,模型每段只能"新起一条",同一件事的详略两版会并存,
    任何字符相似度阈值都判不出来。给了清单之后,配合下面规则 2c 要求
    "沿用原句再补细节",落库侧 `store._is_wording_extension` 的前缀判据就能
    把两版收成一条(保留更长的那版)。
    """
    _existing = [str(x).strip() for x in (existing_facts or []) if str(x or "").strip()]
    system_prompt = (
        "你是记忆整理助手。任务:把用户与 AI 的一段真实对话整理成可长期复用的记忆。\n"
        "规则:\n"
        "1. 摘要(digest):客观概括这段对话,重点是【用户】的状态、遭遇、说过的重要事情。\n"
        "   摘要【只写用户】:严禁描述助手说了什么、做了什么、给了什么建议;"
        "更严禁把助手的猜测、推断、判断当成事实写进去"
        "(例如『助手推测用户因压力而恍惚』『助手建议抚摸宠物』这类内容一律不写)。"
        "只写用户自己表达过的内容,2-4 句;\n"
        "2. 事实(facts):只抽取【关于用户本人的稳定信息】(如他的生活、家庭、学业/工作、"
        "身体状况、情绪困扰、人际关系、爱好、计划),一句话一条;不要抽取助手自己的话。\n"
        "2b. 措辞要【稳定可复用】:同一件事在不同对话里请用尽量一致的句式"
        "(\"用户+谓语+宾语\",如\"用户养了一只叫年糕的猫\"),不要加修饰语、不要换同义词、"
        "不要合并两件事;系统靠措辞一致来识别\"这是同一条记忆\",措辞漂移会产生重复条目;\n"
        "2c. 若本段信息是在【补充 / 细化】下方『已记下的事』里的某一条,"
        "必须以那一条的**原句开头、一字不改**,只在末尾用逗号接上新增细节"
        "(例:已记『用户有一个室友叫阿杰』,本段得知他天天打游戏很吵,"
        "就写『用户有一个室友叫阿杰，天天打游戏很吵』);"
        "严禁换同义词、严禁调整语序、严禁改写成两种说法。"
        "若本段对那条**没有新增细节**,就不要在 facts 里再输出它;\n"
        "3. importance:0-1,表示该内容对以后继续陪伴/理解这个用户的价值;\n"
        "4. risk_note:若用户表达过自伤/伤人/极端绝望等危机信号,请如实简短记录(如"
        "'提到想结束生命'),没有则留空。\n"
        "4b. crisis(危机四要素,独立于标签卡):仅当用户【明确】表达自杀/自伤倾向时,"
        "对确实出现的分面分别填写【用户原话摘录】:ideation=想到结束生命/伤害自己,"
        "intent=想付诸行动的决心,plan=提到方式/时间/地点等具体计划,"
        "behavior=已发生的自伤或自杀尝试。没出现的分面一律留空;"
        "严禁凭情绪低落/绝望感推测(那属于 risk_note,不进 crisis)。\n"
        "4c. crisis 的【判定清单】(命中任意一条就必须填对应分面,不要因为语气平淡就跳过):\n"
        "    · ideation  —— 活着没意思 / 不想活 / 活不下去 / 想死 / 想结束生命 / 想伤害自己 /\n"
        "                   觉得没有活下去的意义 / 希望自己消失\n"
        "    · intent    —— 说想去做、下定决心、已经在考虑怎么做的说法\n"
        "    · plan      —— 提到具体方式 / 时间 / 地点 / 准备物品\n"
        "    · behavior  —— 已经发生的自伤或尝试\n"
        "    四个分面同时是严重度等级:ideation=1(轻)→ intent=2 → plan=3 → behavior=4(重)。\n"
        "4b-2. crisis 的两个段级补充字段(与四分面独立):\n"
        "    · denied_quote    —— 用户明确否认危机的原话摘录(如'我没有想不开,别担心');\n"
        "    · uncertain_quote —— 疑似危机表达但语境不足以确认的原话摘录(只留痕,\n"
        "      不参与任何升级判断);拿不准时写这里而不是硬塞进四分面。\n"
        "4d. 引文写法(必须遵守):**照抄用户说的那一整句**,不要改写、不要换词、"
        "不要只截取其中几个字、不要把两句合并成一句、不要补标点或主语。"
        "系统会对同一段对话抽取多次,引文越接近原文越不容易被丢弃。"
        "宁可多填也不要漏(系统会二次人工复核)。\n"
        "\n"
        "标签卡规则(用户自然聊到才标注,没聊到一律不填,严禁猜测):\n"
        f"5. topics:本段主题,只能从以下词表选(最多3个):{_TOPICS_TEXT};\n"
        "6. dimension_evidence:对照下面【可观察现象字典】(仅作现象参照,不是问卷,无需凑齐),"
        "只记录用户【明确提到/自然流露】的现象;每条给出 level(0=提到但无明显困扰,1=轻度,"
        "2=明显,3=严重)和【一句用户原文引用 quote——找不到贴切原话就宁可不记录该维度】。"
        "没提到的维度绝对不要填,宁缺毋滥;\n"
        "7. triggers:用户提到过、引起情绪变化的具体事情(简短短语,最多3条);\n"
        "8. coping:用户提到过的应对/求助方式(简短短语,最多3条)。\n"
        "\n"
        "时间词规则(务必遵守):\n"
        "9. 若事实内容包含时间词(如「明天」「下周」「9月20号」「3天后」),"
        "必须【原样保留】用户的时间词:严禁自行把它换算成绝对日期"
        "(如把「明天」写成具体日期),也严禁省略时间词。"
        "绝对日期由系统另行计算,你只需照抄时间词。\n"
        "\n"
        "【维度字典】\n" + _dims_text()
    )
    human_prompt = (
        (f"本段对话发生的日期：{anchor_date_text}\n\n" if anchor_date_text else "")
        + (
            "---- 已记下的事（规则 2c：要补充就以原句开头，一字不改；没有新细节就不要重复输出） ----\n"
            + "\n".join(f"- {t}" for t in _existing)
            + "\n---- 结束 ----\n\n"
            if _existing else ""
        )
        + "以下是一段对话记录(用户:… / 助手:…)。请按结构化要求输出记忆整理结果。\n\n"
        + "---- 对话记录 ----\n" + user_input_text + "\n---- 结束 ----"
    )
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ]


def _fmt(v) -> str:
    """案卷值渲染:None → '无数据',其余紧凑 JSON。"""
    import json
    if v is None:
        return "无数据"
    return json.dumps(v, ensure_ascii=False)


def build_review_investigation_prompt(case: dict) -> list:
    """复核调查提示:把确定性案卷交给 LLM,要求结构化输出 ReviewVerdict。

    规则核心:LLM 只做判定不做取数 —— 证据必须逐条引用案卷里的数据;
    案卷没有的,要么不写要么进 conflicts;宁可 watch 不要无据 support。
    """
    system_prompt = (
        "你是心理健康守护系统的复核调查员。确定性算术层给出了一个需要复核的边界信号,"
        "下面是全部调查材料(案卷)——所有数据都由确定性工具算出,可信且完备。\n"
        "你的任务:基于案卷判断这个信号是否值得继续关注。输出结构化结论:\n"
        "  conclusion: support=案卷证据支持继续关注(升级关注级别) /\n"
        "              refute=证据不足以支持(信号更像噪声或正常波动) /\n"
        "              watch=证据存疑,保持观察;\n"
        "  confidence: low/mid/high;\n"
        "  evidence: 证据表,每条 {fact, quote, source} —— fact 是一句可核查的描述,\n"
        "    quote 必须照抄案卷里的数据或数值,source 标注来自哪部分案卷"
        "(score/signals/slot_baseline/similar_days/risk_counts);\n"
        "  conflicts: 案卷中互相矛盾的证据(如参与度大降但负面水平平稳);\n"
        "  note: 一句话结论理由。\n"
        "硬性规则:\n"
        "1. 只依据案卷判定,严禁编造案卷外的『事实』或回忆;\n"
        "2. 每条证据必须带案卷摘录,无据不得开条;\n"
        "3. 单一弱信号(如只有参与度下降、负面水平平稳、无相似日佐证)不足以 support;\n"
        "4. 个人基线(slot_baseline)显示『该时段本来就如此』的,倾向 refute;\n"
        "5. 相似日显示『同样状态此前多次出现且未恶化』的,倾向 refute 或 watch;\n"
        "6. 宁可 watch 不要无据 support;这是审计场景,错报与漏报都会被复盘。\n"
    )
    human_prompt = (
        "---- 调查案卷(全部为确定性计算结果) ----\n"
        f"触发原因: {_fmt(case.get('trigger'))}\n"
        f"分数分解: {_fmt(case.get('score'))}\n"
        f"行为信号: {_fmt(case.get('signals'))}\n"
        f"时段基线偏差: {_fmt(case.get('slot_baseline'))}\n"
        f"历史相似日: {_fmt(case.get('similar_days'))}\n"
        f"近14天风险证据计数: {_fmt(case.get('risk_counts_14d'))}\n"
        f"危机确认窗状态: {_fmt(case.get('confirm_state'))}\n"
        f"上次复核结论: {_fmt(case.get('last_review'))}\n"
        "---- 案卷结束 ----\n"
        "请输出结构化复核结论。"
    )
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ]
