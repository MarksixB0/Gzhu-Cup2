# 长期记忆 × 情绪量化 重构设计(庆园杯 · 心理陪伴与早期风险识别)

> 本文档记录本轮设计共识、已落地改造(P0 + P1 + P2)与算法口径。
> 配套实现见 `src/gzhu_cup2/agent/long_memory/`、`src/gzhu_cup2/agent/pet/`、`src/gzhu_cup2/agent/care/`。
> 原则:结构简单、可靠性高、复杂度低;模糊数据靠"多方互证 + 个人基线"换可靠,不靠单一模型装聪明。

---

## 1. 已确认的设计共识(不要回退)

1. **取消"LLM 隐式推断量表条目"作为测量** —— 心理测量学不成立(CLPsych 2026 证据)。
   量表只当"可观察现象"参照字典,不作为评分单位。
2. **取消一切显式打分/问卷形态**(含桌宠请用户"0-10 打分")——破坏陪伴语境与信任。
3. **危机与情绪解耦**:危机四要素(意念/意图/计划/行为)独立存储、独立计数、独立升级,
   永不参与情绪聚合;系统**不主动施测、不切换角色问询**;后台只留痕,前台危机走既有 handle_crisis(热线)。
4. **早期风险 = 相对用户自己基线的"持续恶化"**,单轮波动永不触发。
5. **对话内校准用"镜像确认"**(反映式倾听 + 用户自然纠错),不用问卷。
6. **主动关怀 = 长期记忆驱动**:桌宠每日生活叙事(角色卡/世界书素材,可整体替换)+
   回忆式开放提问 + 互惠自披露 + 话题轮换;用户可关;透明的"在乎"框架,不做隐藏监控。
7. **读侧重用**:同一份记忆可被多个读侧(reply/care/debug)复用,按用途裁剪配额。

## 2. 总体结构(四层漏斗 + 两个后台域)

```
用户聊天文本
  │  L0 前台零 LLM 快闸(triage):危机短语 + 粗情绪/风险档(关键词启发式,高召回)
  │  L1 固化(memory_graph):LLM 只当"抄写员",强制原文引用;双采样投票(P2)
  │  L2 个人基线时序(trend/ticks):日桶 → 个人 μ/σ → z 漂移/连续恶化(纯算术)
  │  L3 关怀/升级(care):soothe→probe→recall→petlife→checkin;危机证据只留痕供人工
  │  L4 风险标记(risk_state):把 L2/L3 的纵向判定落成**用户当前标记 + 跃迁历史**
  ▼
long_memory(用户记忆)        pet(桌宠记忆:人格/世界观/每日生活)
```

### 命名空间一览(store)
| 命名空间 | 内容 | 生命周期 |
|---|---|---|
| `("memory", user, "pending")` | 待固化段(多轮累积) | 固化后推进/清空 |
| `("memory", user, "facts")` | 稳定事实(去重合并) | 遗忘(半衰期) |
| `("memory", user, "episodes")` | 固化摘要 + 标签卡 | 遗忘(风险段保护) |
| `("memory", user, "risk_events")` | 危机分面(意念/意图/计划/行为) | **永不遗忘/聚合** |
| `("memory", user, "risk_state")` | **风险标记**(当前档位 + 跃迁历史) | 只留最新态 + 最近 200 条历史 |
| `("memory", user, "ticks")` | 按天日桶(数值 + 情绪直方 + confirm/deny) | 90 天,保护桶长期 |
| `("pet", user, "daily")` | 桌宠每日生活叙事 | 按天滚动 |
| `("pet", user, "persona"/"world")` | 角色卡/世界书(文件加载,非 KV) | 静态可替换 |

## 3. 已落地改造

### P0(完成)
- **刻度语义**:`MemoryExtraction.dimension_evidence` = 可观察现象 + 强制原文引用;
  新增 `MemoryExtraction.crisis: CrisisFacets`(四要素,原话摘录,绝不猜测);`tags.clean_crisis_facets` 落库前收敛。
- **危机独立存储**:`store.ns_risk` / `add_risk_event` / `list_risk_events` / `risk_facet_counts`;
  `commit_segment` 固化时自动落分面;确定性键幂等(重复固化不重复落);`forget` 不扫描该命名空间。
- **危机留痕的两条写入路径(2026-09-13 加固,见 `docs/PLAN_MEMORY_HOTFIX.md` §4)**:
  - ① 四要素 `ideation/intent/plan/behavior` —— 来自 LLM 抽取,合并口径为**并集**
    (任一采样命中即留,多条取最长引文)。旧口径要求"两样本引文逐字一致",
    实测会把唯一一次明确危机意念整条丢弃(两次分别给出「活着没意思」与「最近总是觉得活着没意思」)。
  - ② `signal` —— **兜底留痕**,不依赖 LLM:段内出现 `high/crisis` 轮次,
    或分面为空而 LLM 只留了自由文本 `risk_note` 时,由 `commit_segment` 直接记用户原话。
    另加"风险段却无新留痕"的固化自检告警(把此前的静默丢失变成可见日志)。
- **个人基线恶化检测**:`trend.baseline_and_drift(user)`:
  - 基线 = 近 `MEMORY_BASELINE_WINDOW_DAYS=14` 天、刨去最近 `MEMORY_DRIFT_WINDOW_DAYS=3`
    个有记录日后的"较早部分"逐日负向均值 μ/σ;
  - 漂移日 = 最近有记录日 `(neg−μ)/σ ≥ MEMORY_DRIFT_Z_ALERT=1.0`(仅变差方向);
  - `连续漂移日 ≥ MEMORY_DRIFT_CONSECUTIVE_ALERT=3` → `sustained_worsening=True`;
  - 无记录日=缺失(≠0),不参与、也不算好转;数据不足返回 None(不冒险判定)。

### P1(核心完成)
- **桌宠域 `pet/`**:角色卡/世界书 JSON(可整体替换、缺省合并内置默认);`pet/runtime`
  每日生成开关(用户可关)+ 最近生成日;`pet/narrative.ensure_today` **懒生成、按天幂等、
  LLM 失败自动回落世界书事件池模板**(绝不阻塞);自然引用用户近况仅限白名单安全话题。
- **分享日常(petlife)**:care 新决策类型(纯模板 + 桌宠叙事,零内部标记),低频:
  受每日上限 `CARE_MAX_PER_DAY` / 冷却 `CARE_COOLDOWN_HOURS` / 间隔
  `CARE_PETLIFE_MIN_GAP_HOURS=36` 约束;outbox 记录 `last_petlife_ts`。
- **对话基调**:`care.mode.dialogue_mode(emotion, risk)` → serious/gentle/normal/playful,
  只改语气,绝不外泄内部标签。
- **镜像确认**:`care.mode.classify_affect_reply` 判别短回应;
  `capture_memory` 仅在"上轮负面 ≥0.55 + 本轮短确认/否认"时写 `affect_reply`,
  计数入 ticks 日桶(`confirm_count`/`deny_count`),供未来校准负向证据可信度。
- **读侧重用**:`retrieve.build_memory_context(..., purpose=reply|care|debug)`;
  `reply` 与旧行为一致。
- **服务层**:`/api/pet/settings`(开关)、`/api/pet/daily`(今日叙事);
  `/api/memory/debug` 增显 `risk_facets` + `baseline`;关怀调度线程每天懒触发叙事生成。

## 4. 关键阈值(config)
| 常量 | 默认 | 含义 |
|---|---|---|
| MEMORY_BASELINE_WINDOW_DAYS | 14 | 个人基线窗口 |
| MEMORY_DRIFT_WINDOW_DAYS | 3 | 恶化判定窗口(有记录日) |
| MEMORY_DRIFT_Z_ALERT | 1.0 | 漂移日阈值(σ) |
| MEMORY_DRIFT_CONSECUTIVE_ALERT | 3 | 连续漂移日 → 预警 |
| CARE_PETLIFE_MIN_GAP_HOURS | 36 | petlife 最小间隔 |
| PET_DAILY_DEFAULT_ENABLED | True | 每日叙事默认开(用户可关) |
| PET_DAILY_MAX_LEN | 140 | 叙事截断长度 |
| MEMORY_CONSOLIDATE_VOTE_SAMPLES | 2 | 固化自一致采样数(1=关闭投票) |
| MEMORY_LEXICON_CHANNEL_ENABLED | True | 词表旁路通道开关 |
| MEMORY_CUSUM_K_SIGMA / H_SIGMA | 0.5 / 4.0 | CUSUM 允许度 / 告警线(×σ) |

## 5. P2(已落地,待细测)

1. **危机级即时固化**:`store.pending_is_urgent` —— 段内出现 high/crisis 证据即固化,
   不等攒满 5 轮/空闲 300s(worker `_tick` 优先判 urgent);
2. **词表旁路通道**:`long_memory/lexicon.py` 确定性负面词库打分(零 LLM),与前台 triage
   启发式互证:`strong_negative`(双通道 ≥0.5)计数进日桶(`lex_neg_sum/n`、`strong_neg_count`);
   三通道 = 前台关键词 + 词表旁路 + 固化 LLM 抄写;
3. **固化双采样投票**:`consolidate.merge_extractions`(纯函数)+ `extract_memory_voted`
   —— 采样 `MEMORY_CONSOLIDATE_VOTE_SAMPLES=2` 次,只保留全部样本一致的
   事实/主题/维度证据/危机分面(原话不一致的分面不落),单次失败自动退避;
4. **CUSUM 累积偏离**:`trend.cusum_alarm` —— 允许度 k=0.5σ、告警线 h=4.0σ 的单侧累积和,
   与连续漂移互补;已在 `/api/memory/debug` 增显 `cusum`。

## 5. P3(已落地:判定阈值加固 + 档位影响回复)

### 5.1 绝对位移地板(修掉一个会误报的判定缺陷)

`baseline_and_drift` / `cusum_alarm` / `lex_baseline_and_drift` 原本只判 `z ≥ 阈值`。
z 的分母是**个人基线的 σ**,而基线点少且彼此接近时 σ 会坍缩到 0.01 量级 ——
于是**测量分辨率以内的噪声**也达标。8 人格 × 21 天带真值语料的实测后果:

| 现象 | 数据 |
|---|---|
| 一个 `neg_avg` 恒为 0.300、毫无变化的用户 | 基线 μ=0.2875、σ=0.0125 → 位移 0.0125 却 z=1.0,连续 3 日 → 被抬成 elevated |
| 冷启动期(基线 2~3 点) | σ≈0 使 `h = 4.0×σ` 小到 0.004,对照组第 5~10 天**集体误报** |
| 修复后 | 误报率 25% → **0%**;精确率 75% → **100%**;召回不变(75%) |

修法(z 回答"是否异常",地板回答"是否够大",两者同时成立才算漂移日):

| 常量 | 默认 | 作用点 |
|---|---|---|
| `MEMORY_DRIFT_MIN_ABS_DELTA` | 0.15 | 漂移日需 `(v − μ) ≥ 0.15` |
| `MEMORY_CUSUM_MIN_ABS_DELTA` | 0.15 | 越线需 **峰值** C ≥ 0.15(不按单日设闸,以免废掉"缓慢累积"的检出能力) |
| `GUARD_LEX_MIN_ABS_DELTA` | 0.12 | 词表通道同构 |

阈值取自实测分布的空档:恶化日位移 0.194~0.308,误报日位移 0.001~0.121。
三个常量置 0 = 完全回到旧口径(可回退、可做对照实验)。
回归用例见 `tests/test_min_abs_floor.py`,量化工具见 `src/gzhu_cup2/agent/tools/eval_guard.py`。

### 5.2 守护档位 → 对话分寸(care_intensity)

此前 `watch` 对系统**零影响**(三档制实际只有一档在工作)。现在档位通过
`care.mode.care_level` 映射成**场合词**(steady/gentle/caring),再经角色卡可覆盖的
`care_voice` 文案进入提示词。

**不可越过的三条边界**(`tests/test_care_intensity.py` 逐条钉死):
1. **不泄密**:档位名/评估字样永不进入任何给模型的文本 —— 模型只收到场合词渲染出的
   "我该怎么说话"。守卫方式是把三档提示词**逐行 diff**:差异行必须逐字等于某个分寸档的
   `care_voice` 渲染结果,且**只增不减**(升档不得撤掉任何保护性指令)。
2. **不跳戏**:分寸文案与边界原则一律用 `{{char}}` 第一人称写、且必须渲染宏后才进提示词。
3. **方向不翻转**:强度可随档位**上升**(陪伴型优先于打探型、更早递出纯陪伴问候),
   但**不新增追问型**消息、不突破每日上限与免扰时段。

口径一句话:**只调"关心它的方式",不翻转"关心它的方向"** ——
"我陪着你"是陪伴,"我在观察你"是监控;后者一旦被用户发现,整段陪伴体验就会垮掉。
因此边界原则(`care_principle`,写作 `{{char}}` 的自述)与 `safety_tail` 同级
**不可被角色卡关闭**,而措辞(`care_voice`)可覆盖。

### 5.3 档位迟滞(只锁降档)

`tier_of()` 是瞬时判断,而漂移窗口按"最近 N 个**有记录日**"取 —— 中间一天不越线就会让
窗口前滑,档位在边界反复进出(实测 `13:E 14:S 15:E 16:E`)。`guard.apply_hysteresis`
只给**降档**加保护期(`GUARD_TIER_EXIT_DWELL_HOURS=48`):升档永远立即生效,
降档需"原始判断已连续稳定 48h"。

代价不对称是这么定的:**晚报出档只是"又多温柔了几天";早报出档是在用户最需要的时候松手**。
`risk_state` 同时保存 `tier_raw`(瞬时)与 `tier`(对外),因此"为什么还报着"永远可解释。
回归用例见 `tests/test_tier_hysteresis.py`。

### 5.4 稳健个人基线(median + 1.4826×MAD)

`baseline_and_drift` / `cusum_alarm` / `lex_baseline_and_drift` 原本用**均值 + 总体 σ**
估计个人基线。均值型基线对**离群点**敏感:基线里混进一两个极端日,均值被拉高、σ 被放大,
后续真实恶化算出来的 z 反而变小 —— 异常被"稀释"。实测一例(基线 `[0.10,0.12,0.11,0.13,0.60]`,
恶化日 0.20):

| 估计量 | 中心 | 尺度 | 恶化日的 z |
|---|---|---|---|
| 均值 + σ(旧) | 0.212 | 0.194 | **−0.06**(被 0.60 稀释到判不出来) |
| median + 1.4826×MAD | 0.12 | 0.0148 | **+5.40** |

修法:改用 **中位数 + 1.4826×MAD**(中位数绝对偏差)。1.4826 是正态下 MAD→σ 的一致性系数,
使 z 阈值口径不变;置 False 可完全回到旧口径做 A/B 对照。

| 常量 | 默认 | 作用点 |
|---|---|---|
| `MEMORY_BASELINE_ROBUST` | True | 三条基线通道的估计量;返回带 `estimator` 标记(`median_mad` / `mean_sigma`) |

**A/B 实测**(`eval_guard.py`,**当时语料为 8 人格** × 21 天;现语料已扩到 26 人格):
分类指标(prec / recall / F1 / FPR / acc)
**完全不变**;差异只在提前量与过程路径 —— "恶化 + 疏离"由第 15 天提前到**第 13 天**(早 2 天),
代价是"稳态波动"对照出现**短暂 over-care**(最终档仍 stable)。按 §5 段"宁可多关心一点"的
代价不对称取舍,接受。把地板抬到 0.25 可消 over-care,但提前量也随之回到经典口径。

**两条必须记住的边界**(如实记录):

- 它抗的是**离群点**污染,**不治"长期低水位"** —— 用户连续数周低落时中心(中位数或 Q1 都一样)
  同样会跟着下沉。
  **2026-09-13 更新:这一点已覆盖,但实现方式与原先的设想不同。**当初设想的
  "**多时间尺度基线(7 / 30 / 90 天)**"**未采纳** —— 它要维护三套基线并处理其间冲突,
  对当前体量收益不抵复杂度。实际落地的是两条互补改动:
    ① 基线取数改**锚定 90 天窗**(`MEMORY_BASELINE_ANCHOR_WINDOW_DAYS`)+ 中心由中位数改
       **Q1**(`MEMORY_BASELINE_QUANTILE=0.25`)—— 健康史占窗口 ≥25% 时 Q1 就钉在健康水平;
    ② 另加**绝对口径**的低水位通道 D(`MEMORY_LOW_*`,见 §10.5),
       由 `guard.decide_watch` 单独触发 watch。
- **min-abs 地板是按经典口径校准的**,换估计量后地板不再严格对应;若日后误报增多应整体重标。
  (2026-09-13 又补了**绝对位移兜底闸** `MEMORY_DRIFT_ABS_ALERT=0.20`,治反向失效:
  长期恶化让 MAD 膨胀、真实大幅变差的 z 反而跌破阈值。)

回归用例见 `tests/test_robust_baseline.py`。

### 5.5 相对时间锚定(写入时换算成绝对日期)

固化落库的事实保留用户措辞,但相对时间词("明天""下周")是**相对说出那句话的时刻** ——
过一天再看,"明天"早已过去。此前没有任何一层记住"这句话是哪天说的":`format_pending_text`
不带时间戳(固化 LLM 根本看不到对话日期)、`MemoryFact` 无日期字段、检索把事实渲染成裸文本。
后果:day N 存下"用户明天要考试",day N+1 仍被读成"明天"。

**第一版**(保留原文 + 括号补日期 `（事件日期：…）（今天）`)实测**不够** —— 逐字的"明天"
会**压过**括号提示,小模型照样复述。现改为**写入时直接把文本换成绝对措辞**:

| 环节 | 落点 |
|---|---|
| 解析器 | `long_memory/dates.py::resolve_event(text, anchor_dt)` —— 纯函数、零 LLM;返回 `(命中原词, ISO 日期)`。解析 今天/明天/后天/大后天/昨天/前天、周X / 星期X / 礼拜X(取锚点之后最近一个,当天已是则 +7)、下周X、X月X日/号(用锚点年)、X天后/X周后;解析不出返回 None |
| 锚点 | 固化段的**段首轮次 ts**(最接近"记忆诞生的时刻") |
| 落库 | `dates.absolutize(text, anchor)` 把命中原词替换为 `在<ISO>`:`用户明天有考试` → `用户在2026-09-13有考试`。`store.merge_fact` 存**绝对措辞**,`event_date` 字段同时保留(供相对提示 / 排序) |
| 去重 | 绝对日期是"同一件事"的**稳定标识** —— "明天 / 今天 / 后天"跨天提及会收敛到同一措辞,配合规范哈希 + 近义合并**并成一条**(而非裂成多条);段内去重仍用原文 |
| 提示词 | 注入「本段对话发生的日期」+ 规则「时间词**原样保留**,勿换算」—— LLM 只当抄写员,换算全交给确定性 Python |
| 检索 | 渲染 `- {text}（事件日期：…）（今天 / 昨天 / N 天后 / 已过去）`;text 已无歧义,括号只作强化 |
| 存量回填 | `tools/backfill_event_dates.py`(幂等;默认 dry-run,`--apply` 落库;用 `first_seen` 作锚点补历史事实) |

设计边界:换算在**确定性 Python** 里做(LLM 只负责"原样保留时间词"),不引入模型算术的
不确定性。注:这是对最初方案(**不改写 text**)的修正 —— 实测证明保留相对词会误导模型。

回归用例见 `tests/test_dates.py`、`tests/test_fact_merge.py`、`tests/test_memory.py`。

## 6. 评估方案(答辩用,贯穿始终)
- **已建成的最小可用评估集**:`src/gzhu_cup2/agent/tools/eval_guard.py` —— **26 个人格** × 21 天
  **带真值标签**的语料(**12 正 / 14 负**;2026-09-13 由 8 人格扩至 26,补安全回归与信号专项),
  逐日**无前视偏差**回放,输出
  命中/误报/漏报、提前量、恢复回退、通道消融,并生成自包含 HTML 报告。
  语料见 `eval_personas.py`。**它是仿真语料**,证明"逻辑按设计工作、误报来源已定位",
  **不等于**真实人群准确率。
- 下一步(需先解决原文留存):人工标注集(真实对话 300-1000 条,分面标注)→
  报警 PPV、检测延迟(eRisk 口径)、校准(ECE)、消融(每通道边际贡献);
- 安全回归集:"累死了/笑死了"口语 vs 真意念、第三人称 vs 第一人称、玩笑 vs 真心;
- 每层报警可审计:引文 + 规则 + 统计(不依赖"模型感觉")。

---

## 7. 记忆数据目录(统一存放 + 开发期调试)

全部「记忆类」数据统一落在 **`src/gzhu_cup2/agent/memory_data/`**(可用环境变量 `LONG_MEMORY_DIR` 整体重定向):

| 文件 | 内容 |
|---|---|
| `memory.sqlite3` | 长期记忆 KV:facts / episodes / ticks / risk_events / pending + pet.daily |
| `pet_runtime.json` | 桌宠运行时(每日叙事开关、最近生成日) |
| `care_runtime.json` | 关怀运行时(outbox、冷却时间戳) |
| `sessions.sqlite3` | 逐字会话记录(会话 + 轮次;`web/sessions.py` 写穿,重启后自动恢复) |

- **整目录删除 = 记忆清零**;`LONG_MEMORY_DIR=<dir>` = 换一份数据集(多数据集切换 / 测试隔离);
- 首次启动把旧位置的 `long_memory/data/memory.sqlite3` 与 `data/*.json` 自动搬进新目录
  (见 `long_memory/paths.py`);**显式设置 `LONG_MEMORY_DIR` 时一律不搬运** ——
  否则"指向一个测试目录"会变成把真实记忆数据库搬走,等于劫持用户数据;
- 桌宠角色卡 `pet/cards/` 属于代码资产,不在该目录内;该目录由内部 `.gitignore` 全量排除(隐私)。

### 调试工具 `src/gzhu_cup2/agent/tools/memory_dev.py`

```bash
python src/gzhu_cup2/agent/tools/memory_dev.py dir                       # 目录 / 后端 / 文件状态
python src/gzhu_cup2/agent/tools/memory_dev.py list  --user local_user   # 列出全部记忆(可按 --ns 过滤)
python src/gzhu_cup2/agent/tools/memory_dev.py add-fact    --user local_user --text "…" --category life --importance 0.8
python src/gzhu_cup2/agent/tools/memory_dev.py add-episode --user local_user --digest "…" --topic school
python src/gzhu_cup2/agent/tools/memory_dev.py add-risk    --user local_user --facet ideation --quote "…"
python src/gzhu_cup2/agent/tools/memory_dev.py del   --user local_user --ns facts --text "…"
python src/gzhu_cup2/agent/tools/memory_dev.py clear --user local_user
python src/gzhu_cup2/agent/tools/memory_dev.py seed  --file src/gzhu_cup2/agent/tools/seed_example.json   # 幂等批量造数据
python src/gzhu_cup2/agent/tools/memory_dev.py dump  --user local_user --out dump.json
python src/gzhu_cup2/agent/tools/memory_dev.py context --user local_user   # 打印 LLM 实际读到的记忆块 + 字符数
```

`context` 是排查「记忆比重过大」最直接的入口:它渲染读侧真正注入 prompt 的记忆块并给出字符数,
便于与 `REPLY_LENGTH_RULE`(50–150 字)对照;`seed` 用确定性键,重复执行不会产生重复条目。

---

## 8. 可靠性加固(P0 / P1 / P2)

### P0

1. **摘要禁止记录助手推测**(`tags.clean_digest` + 固化 prompt + schema 描述)。
   背景:digest 会被下一轮重新注入 prompt,若含助手的推测/建议,会形成
   「推测 → 固化 → 再注入 → 再当成事实」的自我强化闭环。现在是**落库前确定性剔除**
   「以助手为主语」的句子(整段被剔除时退回原文,不丢记忆)。
   实测真实库 10 条 episode 中 4 条含助手推测,均已被清洗规则覆盖。
2. **测试隔离「API 连接」持久化**:`conftest` 把 `runtime_connection._CONN_FILE`
   指向不存在的文件。此前它会读到开发者本地保存的连接,导致
   「无 Key 应返回 503」的用例结果取决于**本机是否存过连接**(实测稳定失败)。
   同时把 `test_connection.py` 从 `tmp_path` 改为 `tests/` 下自建临时文件,
   整套测试因此在受限沙箱中也能跑通。

### P1

3. **消除重复实现与死代码**:`commit_segment` 的危机分面落库改为复用
   `store.add_risk_event`(此前两条路径各写一份,被测试覆盖的那条反而不是真正运行的);
   新增回归用例锁死两条路径的确定性键一致。删除零引用函数
   `consolidate._union_by`、`ledger.covered_dims`、`ledger.evidence_text`,
   以及 4 个遗留配置常量(`MODEL_API_KEY` / `DATA_DIR` / `RISK_TREND_WINDOW` /
   `SCALE_CONFIDENCE_FLOOR`)。
4. **接上关怀总开关与修正口径**:`CARE_ENABLED` 此前声明了却无人读取(等于没有总开关),
   现已在 `coordinator.evaluate` 最前面短路(后台调度与 API 一起生效);
   `/api/memory/debug` 的 `risk_facets` 此前固定传 `days=None`,使
   `MEMORY_RISK_EVENTS_RECENT_DAYS` 形同虚设,现遵循配置,并保留 `risk_facets_total` 全时段。
5. **事实近义合并**(`long_memory/similarity.py`)。落库时若新事实与已有事实
   「极为相近」则并入同一条(mentions+1、文本取更长、重要度取 max),
   固化时先做**段内去重**避免 mentions 虚增。
   阈值经真实语料 + 对抗样例校准:`双字 Jaccard ≥ 0.55 且 双字包含度 ≥ 0.90`,
   配以极性守卫(「好转/不再」与「加重/严重」一律不合并)。
   ⚠ 这是**高精度、低召回**的有意取舍:字符 n-gram 无法识别换词改写
   (真重复「名为小林/自称为小林」得分 0.22,假重复「爱吃辣/爱吃甜」反而 0.73),
   放低阈值会静默且不可逆地丢失事实。真实语料上 22 条事实合并为 20 条,
   8 个对抗样例零误合并。想进一步提高去重率,应靠 prompt 让 LLM 用固定句式
   (已在固化 prompt 中要求),而不是放宽阈值。

### P2

6. **画像信号接入观测面**:`profile.trait_card` / `trigger_mining` 此前
   「实现了、测试了、但没有任何生产调用方」。现暴露在 `/api/memory/debug`,
   作为可审计的排障/答辩素材;
   **刻意不接进 care 决策** —— trigger_mining 挖出的恰是伴随情绪下行的高压话题,
   主动提起会违背「只碰低压话题」的关怀边界。

### 测试

全套 **241 个用例通过**(新增 `test_memory_paths.py`、`test_fact_merge.py` 共 50 个);
`test_fact_merge.py` 用真实语料的正例与 8 个「只差一个实词」的对抗样例逐条钉死合并边界。

---

## 9. 读侧「记忆占比」收敛

问题(实测真实数据):注入 12 条 facts + 5 条 episodes 会渲染出 **896 字符**记忆块,
占 human prompt 的 **73.3%**,而 `REPLY_LENGTH_RULE` 只要求 50–150 字 ——
背景是目标回复的 **6 倍**,回复被记忆主导。

三处配合收敛(缺一不可,单改任何一处都不够):

| # | 措施 | 位置 |
|---|---|---|
| 1 | **系统提示词不再鼓励复述** —— 原文案「引用时要自然(可用"我记得…"并说出具体内容)」是在主动教模型播报记忆,改为「只用于理解背景、避免重复提问;除非与本轮输入直接相关,不要主动复述;即使相关最多提一条」 | `prompts/prompts.py` |
| 2 | **记忆块降位**到「最近对话」之后(中段末尾),不再占首因高注意位;表头改为「仅作参考,不要复述,不要逐条回应」 | `prompts/prompts.py` |
| 3 | **配额收紧** 12→6 facts、5→3 episodes;`[类别]` 内部标记只在 debug 视图保留(它会强化"资料清单"观感、诱导逐条播报);debug 视图不再受业务配额限制 | `config.py`、`long_memory/retrieve.py` |

**实测结果**(`memory_dev.py context --purpose reply`):

```
改前: 记忆块 896 字符 = human prompt 的 73.3% = 回复目标的 6.0 倍
改后: 记忆块 460 字符 = human prompt 的 58.4% = 回复目标的 3.1 倍    降幅 49%
```

### 存量数据清理:`remerge`

近义合并只在**新事实落库**时生效,历史重复项不会被自动清理,却照样占配额
(实测 6 条 facts 配额里 2 条被同一条「答辩」占掉)。故补一个显式的存量清理命令:

```bash
python src/gzhu_cup2/agent/tools/memory_dev.py remerge --user local_user            # 预演,只报告
python src/gzhu_cup2/agent/tools/memory_dev.py remerge --user local_user --apply    # 真正合并
```

幸存者按 `memory_score` 取最高者;合并后 `mentions` 求和、`importance` 取 max、
`text` 取最长、`first_seen` 取最早、`last_seen` 取最晚。

### 还可继续收紧的旋钮

若仍嫌重,按代价从低到高:
`MEMORY_RETRIEVE_MAX_EPISODES` 3→2(每条约 110 字,是当前最大单项开销)、
`MEMORY_RETRIEVE_MAX_FACTS` 6→4。
不建议对 digest 做截断 —— 摘要尾部常带关键信息(如"计划答辩后去南京旅行")。

---

## 10. L4 风险标记:边界与口径(约束性)

### 10.1 做什么

`long_memory/risk_state.py` 把守护层(`early_risk.guard`)的纵向判定结果,
落成**用户维度的当前标记 + 跃迁历史**:

| 字段 | 含义 |
|---|---|
| `tier` | `stable` / `watch` / `elevated`(与 guard 一致,无 crisis —— 危机走前台固定协议) |
| `since` | **该档位从什么时候开始**(档位变化时重置) |
| `evidence` | 标记依据的通道真值与数据规模(供内部核对) |
| 历史 | **只在档位发生变化时**追加一条;不是每日快照 |

刷新时机:后台调度线程每轮(`web/care_scheduler.py::tick_once`),
以及读取 `/api/memory/insight` 时(幂等、纯算术、零 LLM)。

### 10.2 明确不做什么(本版边界,不是"还没做")

> **识别到早期心理风险后,只在系统内部标记该用户;不接任何外部功能。**

具体地,本版**一律不做**下列事情:

- **不接外部功能**:不通知辅导员 / 家长 / 任何第三方,不推送 webhook,
  不生成对外报告,不自动拨打或弹出热线;
- **不对用户展示**:用户看不到 `watch` / `elevated` 这类内部标签,也看不到任何分数;
  标记只驱动桌宠**说话的方式**(更轻、更贴、更主动一点);
- **不切换危机协议**:对话内出现明确危险表达时,仍由前台
  `triage → handle_crisis` 独立处理(`guard` 从不产生 crisis 档);
- **不施测、不追问、不改变角色**:标记不会变成问卷、量表或问句。

**为什么划这条线**:把"标记"接给外部(如校内心理中心)必须先补三件东西 ——
**用户授权 + 数据治理 + 责任界定**,三者缺一不可。在它们就位之前接外部,
引入的风险大于解决的问题。故本版刻意止于"内部留痕",并把它写进代码
(`risk_state.py` 模块 docstring)、接口(`/api/memory/insight` 的 `risk_policy` 字段)
与测试(`tests/test_silent_errors.py::TestRiskMarking`)三处,防止后来者无意间越界。

### 10.3 两处口径决定(实测支撑,不要回退)

1. **通道 B(词表旁路)改用相对个人基线的 z 检验**
   (`config.GUARD_LEX_USE_BASELINE_Z`,默认 True)。
   旧口径是"全窗口话语加权均值 ≥ 0.5",但该量在真实轨迹与合成轨迹上**恒在
   0.25~0.35** —— 因为词表打分是离散步进(0.30 + 0.09×命中数,上限约 0.84),
   而"全窗口均值"会被窗口前段的平稳期牢牢压低,**无论用户如何恶化都无法达标**。
   新口径与情绪通道(`trend.baseline_and_drift`)同构、不新增阈值常量。
   判定取 `旧口径达标 or 新口径达标`,故**只可能更敏感、不可能漏报**。

2. **`strong_neg` 互证底线必须 ≤ 启发式单次命中值**。
   启发式单命中被降权到 **0.45**、未命中固定 **0.30**;
   旧底线 0.5 高于 0.45,导致"只命中一个情绪词 + 词表也判负面"这种最典型的
   互证场景**永远不成立**(实测 `strong_neg_count` 恒为 0~1)。
   现由 `config.MEMORY_STRONG_NEG_FLOOR = 0.45` 统一。

### 10.4 本轮修掉的静默错误(回归测试见 `tests/test_silent_errors.py`)

这一轮修的都是"**不报错、但结果变错或数据变少**"的问题;

| # | 问题 | 后果 | 修法 |
|---|---|---|---|
| 1 | 固化抽取全部失败时返回"空对象" | 空摘要入库 + pending 被删 + 退避被清 → **用户原话被静默丢弃** | 抛 `ConsolidationFailed`,worker 保留 pending 并退避重试 |
| 2 | `merge_fact` 保留 `archived=True` 却丢掉 `archived_at` | 事实既不可见、又**永远无法被物理清除** | 重新被提及即解除归档并清 `archived_at` |
| 3 | `strong_negative` 底线(0.5)高于单次命中值(0.45) | **两通道互证机制实际恒不成立** | 底线降到 0.45(`MEMORY_STRONG_NEG_FLOOR`) |
| 4 | `capture_turn` 是对话链路上唯一无保护的写 | DB 被锁时四处 502,**而回复已生成** | 捕获失败只告警,不影响对话 |
| 5 | `list_items` / `list_memory_users` 触顶后静默截断 | 检索/遗忘/统计**悄悄漏掉旧数据与更多用户** | 触顶时告警一次(把静默截断变成已知边界) |
| 6 | `pending` 段触 40 轮上限时静默丢弃最老轮次 | 固化持续失败时用户原话被静默丢弃 | 丢弃时告警,并提示通常是固化失败 |
| 7 | 点对点回放读到"未来"的日桶 | 历史回放全部失真(实测每档误判 elevated、idle 为负) | `list_day_buckets` 加时间上界;`last_activity_ts` 加 `now` 参数 |

### 10.5 已知边界(如实记录,不要粉饰)

- **容量上限是静默的**:每命名空间 1000 条(`list_items`)、200 个用户
  (`list_memory_users`)、500 条事实用于相似度扫描。当前数据量远低于上限,
  但本方案的立论是"用数据广度换准确度",**这些上限恰好否掉广度** ——
  动多用户/大数据量之前必须先解决容量,而不是继续采集(现已加告警)。
- **事实去重对"换词改写"无效**:字符 n-gram 只能识别字面相近的重复;
  实测 23 条事实只识别出 2 对可合并,而语义上约 7 件事。
  后果是读侧 6 条配额被同一件事的多说法占掉(洞察视图会直接标出重复条数)。
  想提高去重率应靠"落库前让 LLM 用固定句式重写",而不是放宽阈值。
- **持久化层与 worker 无测试**:`tests/conftest.py` 强制内存后端且不启动 worker,
  故 `SqliteStore` 落盘与后台固化线程**不在自动测试覆盖内**。
- **文本不留原始语料**:固化后 `pending` 即被删除,长期只保留
  LLM 转述(事实/摘要),**无法回溯用户原话** —— 这是评估集建设的**硬约束**。
- **单用户假设(已解耦硬编码,但仍无鉴权)**:Web 层的用户键已收敛到唯一出口
  `web/sessions.resolve_user()`——可用环境变量 `MEMORY_USER_KEY` 整体切换,
  或用请求头 `X-User-Id` 按请求区分(非法值回落默认用户并告警,绝不写进记忆库)。
  **但这不等于多用户就绪**:`X-User-Id` 没有任何鉴权,谁都能声明自己是别人;
  而且会话列表(`web/sessions._threads`)仍是全局的,没有按用户隔离。
  真正的多用户需要身份层(登录/鉴权)+ 会话归属,本版不做。
- **冷启动盲区(相对口径的固有边界)**:判定口径是"**相对用户自己基线的
  持续恶化**",因此**一个从一开始就处于低水位的新用户不会报警** —— 系统会把那当作
  他的正常状态。这是"变化 vs 绝对水位"的口径选择,不是实现错误。
  若要用绝对口径覆盖"一开始就很糟"的情形,必须新增一个**独立**的低频机制,
  并单独评估其误报代价 —— 不可通过放宽本阈值来凑,那会把稳态波动也一起报出来。

  **2026-09-13 更新:该机制已按上面这段的要求落地 —— 通道 D(绝对低水位)。**
  `trend.low_watermark` 走**绝对**口径:近 **21 天**逐日 `neg_avg` 的**中位数 ≥ 0.38**
  且 ≥14 天有记录 → `guard.decide_watch` 直接判 **watch**。D 自带长期性("3 周持续"的定义),
  故允许**单独触发**、不再叠第二通道;但它**不参与 elevated**(长期低位是"该开始关心",
  不是"已在持续恶化")。佐证已翻转:原先引用的 `eval_chronic_low`(持续低位)由恒为
  stable 变为 **watch**。剩余边界收窄为两条:
    · **单靠行为通道**的异常仍漏 —— 实测 `社交退缩` / `易怒`:通道 A 亮但被 AND 门挡;
    · 「**词表**绝对低水位」这条平行尝试**已证伪**,不要再加回通道 B(见 README 同日条目)。
- **仿真语料的边界**:`eval_guard.py` 的 **26 条轨迹(12 正 14 负)**由本项目作者人工编写,
  它证明的是"判定逻辑按设计工作、阈值可达、误报来源已定位并修复",
  **不能**当作真实人群上的准确率宣传。真实准确率的前置条件是原文留存。


