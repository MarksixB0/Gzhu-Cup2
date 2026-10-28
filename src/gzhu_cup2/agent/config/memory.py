# -*- coding: utf-8 -*-
"""config·长期记忆域:数据目录 / 固化 / 读侧 / 遗忘 / 基线 / 复核调查。

仅供 ``config.py`` 聚合,业务代码不要直接 import 本模块
(测试钉值与 DEV_FAST_MODE 覆盖只作用在聚合命名空间上)。
"""
import os


_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------- 长期记忆 ----------
# 统一记忆数据目录:记忆 KV 库 + 桌宠/关怀运行时 JSON。可整体重定向(多数据集/测试隔离)。
MEMORY_DIR = os.getenv("LONG_MEMORY_DIR") or os.path.join(_BASE_DIR, "memory_data")
# 记忆数据文件(langgraph SqliteStore 落盘;后端缺失时回落内存 Store 并告警)
LONG_MEMORY_DB_PATH = os.path.join(MEMORY_DIR, "memory.sqlite3")
# 可重建的本地全文检索索引。索引与主记忆库分开，损坏/删除不会丢失记忆。
MEMORY_FTS_DB_PATH = os.path.join(MEMORY_DIR, "memory_fts.sqlite3")
# 逐字会话记录库(重启后恢复全部会话;LONG_MEMORY_BACKEND=memory 时退回纯内存)
SESSIONS_DB_PATH = os.path.join(MEMORY_DIR, "sessions.sqlite3")
# 隐私控制状态(暂停记录开关)
MEMORY_PRIVACY_PATH = os.path.join(MEMORY_DIR, "memory_privacy.json")
# 用户自述资料(称呼 / 喜欢的事物 / 讨厌的事物;见 core/user_profile.py)。
# 与记忆同目录:这样「清空全部记忆」能连它的内容一起清,否则是假删除
# (但首启引导状态不清 —— 点过「跳过」的承诺不因清记忆而作废,见 uprofile.clear)。
# ⚠ 内容与风险判定完全解耦 —— ticks / trend / guard 都不读它。
USER_PROFILE_PATH = os.path.join(MEMORY_DIR, "user_profile.json")
# 旧路径:仅用于首次使用时把历史数据搬进 MEMORY_DIR。
# 路径锚定【仓库根】:旧路径是历史事实,不随代码移动。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_BASE_DIR)))
_LEGACY_AGENT_DIR = os.path.join(_REPO_ROOT, "app", "Agent")

# 每个目标可有多处历史落点(新 → 旧),迁移按序取第一个存在的源。
LEGACY_LONG_MEMORY_DB_PATHS = (
    os.path.join(_LEGACY_AGENT_DIR, "memory_data", "memory.sqlite3"),
    os.path.join(_LEGACY_AGENT_DIR, "long_memory", "data", "memory.sqlite3"),
)
LEGACY_PET_STATE_PATHS = (
    os.path.join(_LEGACY_AGENT_DIR, "memory_data", "pet_runtime.json"),
    os.path.join(_LEGACY_AGENT_DIR, "data", "pet_runtime.json"),
)
LEGACY_CARE_STATE_PATHS = (
    os.path.join(_LEGACY_AGENT_DIR, "memory_data", "care_runtime.json"),
    os.path.join(_LEGACY_AGENT_DIR, "data", "care_runtime.json"),
)
# 单数别名 = 最新候选(保持既有引用兼容;long_memory/paths.py 合并两者后去重)
LEGACY_LONG_MEMORY_DB_PATH = LEGACY_LONG_MEMORY_DB_PATHS[0]
LEGACY_PET_STATE_PATH = LEGACY_PET_STATE_PATHS[0]
LEGACY_CARE_STATE_PATH = LEGACY_CARE_STATE_PATHS[0]
# 「API 连接」旧落点(同样相对仓库根):见 config/runtime_connection.py 的惰性迁移
LEGACY_RUNTIME_CONNECTION_PATH = os.path.join(_LEGACY_AGENT_DIR, "config", "runtime_connection.json")
# 暂无账号体系前的默认用户。可被 MEMORY_USER_KEY 环境变量或 X-User-Id 请求头覆盖,
# 业务代码不应直接读它(唯一出口是 web.sessions.resolve_user)。
MEMORY_USER_KEY_DEFAULT = "local_user"
MEMORY_USER_KEY = os.getenv("MEMORY_USER_KEY") or MEMORY_USER_KEY_DEFAULT
# 固化触发:同一 pending 段累积轮数 ≥ N
MEMORY_CONSOLIDATE_MIN_TURNS = 5
# 固化触发:pending 空闲超过该秒数(用户可能已离开,后台补固化)
MEMORY_IDLE_SECONDS = 300
# 两次聊天间隔超过该值时，先封存旧 pending 段，再开启新段，避免跨天相对日期被错误锚定。
# ⚠ 不变式:MEMORY_CAPTURE_SESSION_GAP_SECONDS ≥ MEMORY_IDLE_SECONDS(当前 1800 ≥ 300)。
# 切段是"会话边界",不应早于"空闲即固化"的判定;若 GAP < IDLE,用户在 (GAP, IDLE) 内回来
# 会继续复用旧段,新轮次仍锚在旧段首 —— 相对日期会被悄悄算错。调任一常量都要回看
# capture_turn / pending_is_due 的组合(回归见 tests/test_memory_config_invariants.py)。
MEMORY_CAPTURE_SESSION_GAP_SECONDS = 1800
# 后台记忆 worker 轮询间隔(秒)
MEMORY_WORKER_POLL_SECONDS = 3
# 固化失败退避:连续失败按 base×2^n 递增等待,封顶 max(秒),避免每 3 秒狂重试
MEMORY_CONSOLIDATE_RETRY_BASE_SECONDS = 30
MEMORY_CONSOLIDATE_RETRY_MAX_SECONDS = 600
# 读取侧上限(注入生成节点的记忆配额)。
# 主闸门是字符预算:按预算从高到低填充,先保证事实层,再给会话回顾留固定预留;
# 条数上限为安全阀。
MEMORY_RETRIEVE_MAX_CHARS = 480           # reply 用途的记忆块总预算(字符)
MEMORY_RETRIEVE_EPISODE_RESERVE_CHARS = 140   # 其中预留给「过往对话回顾」的字符数
MEMORY_RETRIEVE_CARE_MAX_CHARS = 320      # care(主动关怀)用途的预算,比 reply 更紧
MEMORY_RETRIEVE_ARCHIVED_MAX = 1          # 冷记忆兜底:最多带 1 条归档条目
# 冷记忆的预留:不加预留时库稍大预算就被事实填满,"归档可召回"变成空话。
MEMORY_RETRIEVE_ARCHIVED_RESERVE_CHARS = 30
# 安全阀(不是主闸门):正常由字符预算决定条数
MEMORY_RETRIEVE_MAX_FACTS = 40
MEMORY_RETRIEVE_MAX_EPISODES = 12
# FTS 初筛门槛:候选条数不超过该值时完全不筛,直接走旧的相关性排序。
# 超过时才用「FTS 命中集 ∪ memory_score top-MEMORY_RETRIEVE_MAX_FACTS」兜底,
# 避免一条命中把同主题近义事实全部挡掉。
MEMORY_FTS_MIN_CANDIDATES = 50

# ---- 读侧:按问题取用 + 分道配额 ----
# 命中档内先按相关性分档、再按 memory_score 排序;分道配额防止单一主题吃干预算。
MEMORY_RETRIEVE_RELEVANCE_MIN = 0.25   # 达到该相关分即进入"命中档",优先于非命中档
MEMORY_RETRIEVE_RELEVANCE_STEP = 0.25  # 相关性分档步长(防噪声决定顺序)
# 最多允许几条命中事实"插队"到静态序之前(0 = 关闭按问题取用)。
MEMORY_RETRIEVE_RELEVANCE_PROMOTE_MAX = 2
MEMORY_RETRIEVE_STATE_MAX = 4          # reply 用途:状态类事实最多占几条(风险内容全道豁免)
# 近义去冗:与已入选条目近义的事实不再入选。
MEMORY_FACT_READ_DEDUP = True
# 长期趋势摘要窗口:近期状态只有 3 天窗,遇到"长期低落 + 末几天好转"会误导。
MEMORY_TREND_LONG_DAYS = 21
MEMORY_TREND_LONG_MIN_DAYS = 5         # 有记录日不足该值 → 不输出长期摘要(防冷启动噪声)

# 事实近义合并:是否把「只差虚词/语序」的两条视为同一件事。
# 只认虚词差异,实词取值不同一律不合并;置 False 可完全回退。
MEMORY_FACT_FUNCTION_WORD_MERGE = True
MEMORY_PENDING_MAX_TURNS = 40   # 单段待固化轮次上限(防总结 prompt 膨胀)

# ---- 读侧检索留痕(审计/排障;独立命名空间,绝不进记忆本体,见 long_memory/trace.py) ----
MEMORY_TRACE_ENABLED = True     # reply/care 读侧组装落一条检索决策记录
MEMORY_TRACE_MAX_ITEMS = 100    # 滚动上限(条);超限删最旧
MEMORY_TRACE_TTL_DAYS = 14      # 留痕保留天数(过期清理)

# 遗忘策略(见 long_memory/forget.py):半衰期、归档与删除门槛;物理删除默认关闭。
MEMORY_FORGET_HALF_LIFE_DAYS = 90    # 新鲜度半衰期(天),与日桶保留期同为 90 天口径
MEMORY_IMPORTANCE_FLOOR = 0.15       # 归档分阈值:低于该分且超龄 → 可归档
MEMORY_FORGET_MIN_AGE_DAYS = 30      # 最早可归档年龄(天):先积累再判死
MEMORY_ARCHIVE_GRACE_DAYS = 180      # 归档观察期(天),仅在 purge 开启时生效
# 物理删除总开关。默认 False = 只归档、不删除。
MEMORY_FORGET_PURGE_ENABLED = False
MEMORY_FORGET_INTERVAL_SECONDS = 3600  # worker 周期执行遗忘的间隔(秒)
# 受保护类别:永不自动归档/删除(危机、照护轨迹等)
MEMORY_PROTECTED_CATEGORIES = ("risk", "care")
# ---- 免衰减(pinned):永不自动归档,不影响"能否被主动提起" ----
MEMORY_FORGET_PINNED_CATEGORIES = ("risk", "care")
MEMORY_FORGET_PINNED_IMPORTANCE = 0.7   # importance ≥ 该值 ⇒ 永不自动归档
# ---- 时序层(ticks 日桶)与近况摘要 ----
MEMORY_TICK_KEEP_DAYS = 90         # 非保护日桶的最长保留天数(出现 high/crisis 的桶长期保留)
MEMORY_TREND_RECENT_DAYS = 3       # "近期状态"窗口(天)
MEMORY_TRAIT_WINDOW_DAYS = 14      # 特质卡/触发挖掘的观察窗口(天)
# ---- 个人基线 / 恶化检测(全部相对用户自身;单轮波动永不触发) ----
MEMORY_BASELINE_WINDOW_DAYS = 14    # 【已被取代,仅作兜底】实际生效的是锚定窗口
# 基线取数锚定窗口(天):建立基线时回看多远的历史,与日桶保留期一致。
MEMORY_BASELINE_ANCHOR_WINDOW_DAYS = 90
MEMORY_DRIFT_WINDOW_DAYS = 3        # 恶化判定窗口(天):取最近 N 个"有记录日"与基线比较
MEMORY_DRIFT_Z_ALERT = 1.0          # 单日偏离基线 ≥ N×σ 记为 1 个漂移日(仅负向)
MEMORY_DRIFT_CONSECUTIVE_ALERT = 3  # 连续漂移日 ≥ N 才报"持续恶化"
# 基线估计量:True = Q1 + 1.4826×MAD,False = 均值 + 总体 σ。
MEMORY_BASELINE_ROBUST = True
# 稳健基线中心的分位数:0.25 = 下四分位(默认),0.5 = 中位数。
MEMORY_BASELINE_QUANTILE = 0.25
# ---- 绝对位移地板(与 z 检验并列的第二道闸) ----
# z 回答"是否异常",本值回答"是否够大";两者同时成立才认定一个漂移日。
MEMORY_DRIFT_MIN_ABS_DELTA = 0.15   # 漂移日还需 (当日均值 − 基线均值) ≥ 该值
# 漂移日的绝对位移兜底:delta ≥ MIN_ABS_DELTA 且 (z ≥ Z_ALERT 或 delta ≥ ABS_ALERT)。
# 置 0 = 关闭兜底闸。
MEMORY_DRIFT_ABS_ALERT = 0.20
# ---- 危机分面(risk_events)留痕 ----
# 命名空间受保护、永不遗忘。写入两条路径:四要素结构化抽取(并集口径)与 signal 兜底
# (段内出现 high/crisis 或 LLM 只留了 risk_note 时直接记原话)。
MEMORY_RISK_EVENTS_RECENT_DAYS = 30  # 危机分面计数口径:近 N 天内独立事件

# ---- 复核调查通道(long_memory/review_channel,PULSE 式) ----
# 算术层边界信号触发 → LLM 按固定 schema 调查确定性案卷 → 结论写回账本(不直接改档位)。
REVIEW_ENABLED = True          # 总开关;关闭时只留触发器不跑调查
REVIEW_COOLDOWN_H = 24.0       # 同一用户两次复核的最小间隔(小时),控成本
REVIEW_MAX_RETRY = 3           # LLM 调查单任务最多重试次数,超限丢弃等下次触发
REVIEW_MAX_TOKENS = 1200       # 复核调查的输出上限(token);思考型模型需连推理一起计入

# ---------- P2:证据累积 / 投票 / BOCPD ----------
# 固化自一致投票:采样次数 ≥2 时多份抽取按"并集 + 支持度"合并(成本≈×N;可关=1)
MEMORY_CONSOLIDATE_VOTE_SAMPLES = 2
# 固化抽取温度:结构化抽取用 0,保证多份采样可对齐。
MEMORY_CONSOLIDATE_TEMPERATURE = 0.0
# 固化抽取输出上限。思考型模型的思考 token 与 JSON 共用同一份额度,给不足会在
# JSON 落地前截断。2000 为拐点值。
MEMORY_CONSOLIDATE_MAX_TOKENS = 2000
# 弱证据(只被部分采样抽到)在检索排序中的折扣。置 1.0 = 不降权。
MEMORY_VOTE_WEAK_SCORE_FACTOR = 0.6
# 词表旁路通道开关(捕获阶段给逐轮文本跑一次确定性负面词库打分,并入日桶;
# 仅作开发者面板的观察口径,不参与任何判定)
MEMORY_LEXICON_CHANNEL_ENABLED = True
# ---- 可主动提及白名单(宁可少说不可说错) ----
MEMORY_INITIATE_CATEGORIES = ("preference", "life")  # 仅这些低压类别可被主动提起
MEMORY_INITIATE_MAX_AGE_DAYS = 3   # 距最近一次提及不超过该天数才可主动提
MEMORY_INITIATE_MIN_IMPORTANCE = 0.5
# ---- 事实近义合并(确定性;算法与能力边界见 long_memory/similarity.py) ----
# 把极相近的同一件事合并为一条,避免读侧 facts 配额被重复项占满。
MEMORY_FACT_SIMILARITY_ENABLED = True
MEMORY_FACT_BI_JACCARD = 0.55         # 双字 Jaccard 阈值
MEMORY_FACT_BI_CONTAINMENT = 0.90     # 双字包含度阈值,也是防"只差一个实词"被误合并的守卫
MEMORY_FACT_SIMILARITY_SCAN_LIMIT = 500  # 近义扫描上限,防大用户下退化

# ---- 抽取端「已记下的事」清单(治近义重复的根) ----
# 同一件事补充细节后,与原句的文本相似度可能低于合并阈值,靠落库侧合并判不出来;
# 真正的缺口在抽取端 —— `build_memory_extraction_prompt` 看不到库里已有什么,
# 模型每段只能"新起一条"。把未归档事实的紧凑清单注入提示,要求"是同一件事
# 就沿用原句再补细节";落库侧再由 `store._is_wording_extension` 用确定性前缀
# 判据兜住(零新阈值、纯字符串)。
# 成本:每次固化多几百 token;固化在后台线程,不影响用户等待。
# 关闭方式:MEMORY_PROMPT_EXISTING_FACTS=False(退回"抽取端完全无历史"的老行为)。
MEMORY_PROMPT_EXISTING_FACTS = True
MEMORY_PROMPT_EXISTING_FACTS_MAX = 24    # 清单最多注入几条(按 mentions/importance 取前 N)
MEMORY_PROMPT_EXISTING_FACT_CHARS = 48   # 每条截断长度,防提示词随库增长膨胀

# ---- 措辞扩展合并(确定性,零阈值调参) ----
# 命中条件:两条文本去掉空白/标点后,短的是长的**严格前缀**,且短文本 ≥6 字、
# 长度比 ≥0.5。用于吸收"沿用原句 + 逗号补细节"这种形态(抽取提示会引导模型这么写)。
# 与近义阈值的关系:这是精确结构判据,不参与 similarity 的模糊打分(不需要校准阈值),
# 但它仍属"合并"通道,受总开关 MEMORY_FACT_SIMILARITY_ENABLED 控制 ——
# 把总开关关掉等于退回"仅完全相同文本才合并",这里再挂一个二级开关便于单独对照实验。
MEMORY_FACT_EXTENSION_ENABLED = True
MEMORY_FACT_EXTENSION_MIN_CHARS = 6
MEMORY_FACT_EXTENSION_MIN_RATIO = 0.5

# ---------- 相对日期:一周从周几起算 ----------
# "sunday"(默认,与国内日历一致)或 "monday"。见 long_memory/dates.py 的
# _week_start_index —— 只影响「下周X / 下星期X」;周末锚点上两种约定相差 7 天,
# 不固定会导致跨周日期解析漂移。
MEMORY_WEEK_START = os.environ.get("MEMORY_WEEK_START", "sunday")

# ---------- 开发用:长期记忆洞察接口 ----------
# 该接口返回记忆原文 + 情绪时序 + 判定通道真值,正式部署应置 False 或收紧 CORS。
MEMORY_INSIGHT_ENABLED = True
