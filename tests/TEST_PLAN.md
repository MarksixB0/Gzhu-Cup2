# 心伴 功能测试方案(test/ 使用说明)

目标:让本项目**所有可验证的功能点都能离线、可重复地被覆盖**,_不依赖真实模型 API Key、不联网、不下载模型_。
LLM 一律通过 **`FakeLLM` / 注入假 `get_llm`** 替换,聚焦每个模块自己的行为与 API 契约。

> ⚠ **本文件已按当前架构重写。** 旧版引用的 `test_scale.py` / `test_risk.py` /
> PHQ-9 / GAD-7 / C-SSRS 随"**去量表化**"一起删除(见 `MEMORY_REDESIGN.md` §1),
> 那批文档早已与仓库不符 —— 此处不再保留它们的痕迹。

---

## 0) 运行方式

后端在仓库根执行:

```bash
uv run python -m pytest                 # 跑全部(testpaths=test)
uv run python -m pytest -q              # 精简输出
uv run python -m pytest -k risk         # 按关键字筛
uv run python -m pytest test/test_guard.py -v
```

前端 / 桌面端(编译期冒烟,兼作类型检查):

```bash
cd frontend && npm run build
cd desktop  && npm run build
```

> 说明:首次执行会 import `server` / `app.Agent`,冷启动较慢(十几秒),之后走缓存。
> 仓库根 `conftest.py` 会把两个本机环境坑(系统临时目录不可写、`0o700` 目录建完即不可访问)
> 自动修好;受限沙箱下也能跑通。

**另外一个不依赖 pytest 的量化验收工具**(答辩/回归两用):

```bash
# 26 人格(12 正 14 负)× 21 天带真值语料的注入 + 逐日回放 + 指标;并生成自包含 HTML 报告
.venv\Scripts\python.exe src/gzhu_cup2/agent/tools/eval_guard.py --dir demo_eval
# 对照实验:给判定加/不加"绝对位移地板"
.venv\Scripts\python.exe src/gzhu_cup2/agent/tools/eval_guard.py --dir demo_eval --min-abs 0.15
# 打印某人格的四通道原始数值(定位阈值问题)
.venv\Scripts\python.exe src/gzhu_cup2/agent/tools/eval_debug.py --users eval_low_engagement
```

---

## 1) 覆盖矩阵(功能点 → 自动测试)

### 1.1 前台快闸(零 LLM,确定性)
- `test_triage.py`:粗情绪标签、风险四档、显式危机短语命中;
  **"累死了 / 笑死了"等口语不误触**;
  **否定守卫**——"我不想死 / 我不会自杀的"不得被判危机(子串匹配陷阱),
  同时真危机("不想活""我想死")与第三人称求助**仍须命中**。

### 1.2 危机安全线
- `test_api.py` / 节点级:危机短语 → `handle_crisis` 固定协议;
- `crisis_fallback` 兜底(LLM 失效时仍必须给出热线)。

### 1.3 守护层:单点判定
- `test_guard.py`:多通道融合;**单信号永不触发**;数据不足一律 stable;
  `_lex_channel_on` 新旧口径的"或"关系。

### 1.4 守护层:档位随时间的可达性与回放
- `test_tier_transition.py`:档位随数据积累单调升级;**点对点回放不得读到未来日桶**
  (这是回归锁:`idle` 曾出现负数、每档都被误判 elevated)。

### 1.5 判定阈值:绝对位移地板
- `test_min_abs_floor.py`:z 检验之外的第二道闸 ——
  零方差基线上的微小位移**不得**判为漂移;CUSUM 按**峰值累积量**设闸(不削弱缓慢恶化);
  阈值置 0 时行为回到旧口径(可回退)。

### 1.6 档位迟滞(降档保护期)
- `test_tier_hysteresis.py`:升档**永远立即**;短暂回落被吸收;
  真正走出风险后仍能降档;`since` 语义不被污染。

### 1.7 对话分寸(care_intensity):档位如何影响回复
- `test_mode_layering.py`:对话基调纯函数 + 镜像确认短回应判别;
- `test_care_intensity.py`:
  - 档位**真的生效**(`watch` 曾完全空转);
  - 单向性 —— 分寸只能让语气更克制,永不变活泼;
  - **不泄密** —— 三档提示词全文不含档位名;且逐行 **diff 只增不减**,
    差异行必须**逐字等于**某个分寸档的 `care_voice` 渲染结果(白名单,非关键词);
  - **不跳戏** —— 边界原则以 `{{char}}` 第一人称渲染、宏必须已替换;
  - 边界原则与 `safety_tail` 同级**不可被角色卡覆盖**,但 `care_voice` 可覆盖;
  - 关心强度可上升(陪伴型优先于打探型),但**不新增追问型**、不突破每日上限。

### 1.8 长期记忆
- `test_memory.py` / `test_memory_layer.py`:事实/片段/待固化段的分层与配额;
- `test_fact_merge.py`:近义合并的阈值边界(含对抗样例);
- `test_memory_paths.py`:记忆目录解析与旧路径迁移;
- `test_json_state.py`:域无关 JSON 状态的线程锁与原子替换;
- `test_risk_baseline.py`:个人基线/恶化检测 + 危机分面独立存储与幂等。

### 1.9 P2 通道
- `test_p2_channels.py`:固化双采样投票、词表旁路通道、CUSUM 累积偏离。

### 1.10 主动关怀
- `test_care.py` / `test_care_pkg.py`:纯决策 policy 的排序与门槛;
- `test_care_backoff.py`:未获回应的退避(单调变长但**封顶**,不永久静默);
- `test_care_llm_content.py`:文案必须走 LLM,**无模板兜底**;违规即静默丢弃;
- `test_care_soothe.py`:低落回访;
- `test_reply_budget.py`:回复长度预算;
- `test_proactive_history.py`:已投递/未回应计数。

### 1.11 人格层(角色卡)
- `test_character_card.py` / `test_character_prompts.py` / `test_character_integration.py`:
  Character Card V2 解析与校验;卡可覆盖提示词;**代码默认无角色专名**;
  `safety_tail` / `care_principle` 不可覆盖;渲染与开场白。

### 1.12 HTTP API 与配置
- `test_llm_cache.py`:`get_llm` 键控缓存(同参复用、换温新建、默认温度解析);
- `test_api.py`:health、`/api/chat`(结构 / 同 thread 连续 / 参数覆盖 / 无 Key → 503)、
  `/api/chat/stream` 的 SSE 阶段、thread CRUD;
- `test_connection.py`:API 连接读写与测试探测(测试隔离,不读本机已存连接);
- `test_pet_api.py` / `test_pet_pkg.py`:桌宠设置 / 叙事 / 角色卡列表 / 开场白,
  以及 `/api/memory/debug` 的字段契约。

### 1.12b 用户身份与 CORS
- `test_identity.py`:用户键**合法性**边界(长度 64、非法字符一律拒)、
  非法输入**回落且不抛异常**、不同用户落在**不同记忆命名空间**、
  `engine.run_turn` 真的把 resolved user 写进 `state.user_id`(解掉硬编码)、
  `/api/chat` 带 `X-User-Id` 时记忆确实分流到该用户;
  CORS **绝不允许"通配源 + 凭据"**同时出现,且默认配置不含通配源。

### 1.13 静默错误守卫
- `test_silent_errors.py`:专门锁"**不报错但结果变错或数据变少**"的问题
  (空抽取丢原文、归档标志泄漏、互证底线不可达、捕获失败拖垮对话、
  触顶静默截断、回放读到未来、**风险标记不接外部**)。

### 1.14 桌宠端到端
- `test_petlife_e2e.py`:分享日常的完整链路;
- `test_ticks.py`:日桶聚合。

### 1.15 前端 / 桌面端(非自动化)
- 编译即冒烟:`frontend && npm run build`、`desktop && npm run build`;
- 手动关键场景:流式显示、主题切换并保持、会话新建/搜索/改名/删除、
  Markdown 渲染、设置持久化、桌宠动画与点击穿透、气泡生命周期、主动靠近。

---

## 2) 假引擎策略(遵守)

- `FakeLLM` 提供节点需要的两种调用:`.invoke(...)` 与 `.with_structured_output(cls).invoke(...)`;
- 断言全部基于假引擎的**确定输出**,避免任何随机/网络波动;
- 保持离线:不设真实 API Key、不下载 embedding、不触真实模型。

## 3) 加新测试的准入

1. 放到 `tests/`,导路径由 `tests/conftest.py` 统一处理,**不要自行向 `sys.path` 塞路径**;
2. 需要"重 server"时直接依赖 `api_client` fixture(内部惰性构图 + FakeLLM);
3. 改变某节点行为时注入假返回(构造 `FakeLLM` 或改帮助函数);
4. 保持在毫秒~秒级;
5. **修 bug 必须同时补一条会失败的回归用例**,否则同类问题会复发
   (本项目已多次出现"实现了、测试了、但生产调用方没接上"的情况)。

## 4) 已知边界(如实记录,不要粉饰)

- **真实模型未纳入自动套件**:提示词在真实模型下的表现(尤其危机话术合规性、
  是否越界成"观察结论")只能靠离线抽查,不在 pytest 覆盖内;
- **持久化层与 worker**:`tests/conftest.py` 强制内存后端且不启动 worker,
  故 `SqliteStore` 落盘与后台固化线程**不在自动覆盖内**;
- **评估语料是仿真的**:`eval_guard.py` 的 26 人格语料(12 正 14 负)由本项目作者编写,
  它证明"判定逻辑按设计工作、误报来源已定位",**不能**当作真实人群准确率;
- **无法回溯用户原话**:固化后 `pending` 即删除,原文不留存 ——
  这是评估集建设的硬约束(见 `MEMORY_REDESIGN.md` §10.5)。
