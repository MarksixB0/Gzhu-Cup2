# 心伴（XinBan）

以桌面常驻宠物为交互载体、在本地沉淀长期情绪量化并在状态相对自身基线持续恶化时完成早期风险识别的单机心理健康陪伴系统。交付形态为 Electron 桌面应用与 Python 本地服务，全部数据落盘于本机，不设远端账号与服务端。

| 属性 | 值 |
|---|---|
| 产品名 | 心伴（XinBan） |
| 仓库 / 包名 | `Gzhu-Cup2` / `gzhu_cup2` |
| 版本 | `1.3.2` |
| 运行形态 | Electron 桌面应用（Win x64，NSIS 安装包）+ 本地 FastAPI 服务 |
| 后端语言 / 框架 | Python ≥ 3.13，LangGraph 状态图 + FastAPI |
| 前端技术栈 | Vue 3 + TypeScript + Vite + Tailwind CSS v4 |
| 数据存储 | 本机 SQLite（长期记忆 / 会话 / FTS5 索引） |
| 模型依赖 | 任意 OpenAI 兼容接口（无内置默认，由配置提供） |
| 用途定位 | 竞赛 / 课程项目，非医疗器械 |

## 一、系统架构

### 1.1 部署拓扑

系统由三个执行单元构成，数据流为单向：渲染层 → 本地 HTTP → 后端图与存储。

```text
┌─────────────────────────────── Electron 主进程 ───────────────────────────────┐
│  electron/main.cjs                                                            │
│  单实例锁 · 空闲端口探测(8000 起 +1，最多 100 次) · 后端子进程托管与回收        │
│  系统托盘 · pet-asset:// 静态资源协议 · 首启免责声明                              │
└───────────────┬───────────────────────────────────────┬───────────────────────┘
                │ BrowserWindow                          │ BrowserWindow
        ┌───────▼────────┐                     ┌─────────▼──────────┐
        │  桌宠渲染层     │                     │  功能界面(主窗口)   │
        │ apps/desktop   │                     │ apps/web 构建产物   │
        │ 序列帧动画/气泡 │                     │ 对话/角色卡/设置/日历│
        └───────┬────────┘                     └─────────┬──────────┘
                └────────────── HTTP 127.0.0.1 ───────────┘
                                       │
                        ┌──────────────▼───────────────┐
                        │  Python 后端(FastAPI)         │
                        │  LangGraph 主图 + 后台通道     │
                        └──────────────┬───────────────┘
                                       │
                        ┌──────────────▼───────────────┐
                        │  本机 SQLite                  │
                        │  memory / sessions / fts      │
                        └──────────────────────────────┘
```

后端进程生命周期由主进程持有：启动时拉起，退出时终止后端子进程树；启动前扫描并清理本项目遗留的后端进程，避免端口占用与内存滞留。

### 1.2 对话主链路

单轮对话仅发生一次大模型调用，其余计算在后台通道内完成。

```text
START → receive_input → triage ─┬─ crisis ─→ handle_crisis ─┐
                                └─ 其他  ─→ retrieve_memory → generate_response ─┤
                                                                                ▼
                                                                        capture_memory → END
```

| 节点 | 职责 | 模型调用 |
|---|---|---|
| `receive_input` | 输入清洗与规范化 | 无 |
| `triage` | 规则快闸：粗情绪判定 + 风险档位 `low/medium/high/crisis` | 无 |
| `handle_crisis` | 固定危机协议，输出心理援助热线 | 无 |
| `retrieve_memory` | 长期记忆读取：档案层 → 近况层 → 事件层 | 无 |
| `generate_response` | 陪伴回复生成，附带对话基调 `analysis.tone` | 1 次 |
| `capture_memory` | 轮次写入会话库与记忆流水，唤醒固化 | 无 |

`triage` 仅承担粗判与路由，深度情绪趋势与维度证据由后台记忆窗口聚合多轮现象得出，两条通路互不阻塞。

### 1.3 后台通道

| 通道 | 实现位置 | 触发条件 | 产出 |
|---|---|---|---|
| 记忆固化 | `long_memory/consolidate.py` + 后台 worker | 段龄阈值与空闲时长 | 事实、日桶、片段、待固化队列 |
| 主动关怀 | `care/policy.py` + `care/runtime.py` | 定时评估 | outbox 消息，前端拉取后展示 |
| 早期守护 | `algo/skeleton.py` | 对话后与定时分析 | `risk_state` 档位与跃迁历史 |
| 日程提醒 | `agenda/reminder.py` | 定时扫描 | outbox 消息（同一自然日一次） |

### 1.4 模块边界

后端采用单一包内分域；前端采用壳层与业务模块分离，依赖方向为模块 → 壳层单向。

| 路径 | 职责 | 对外契约 |
|---|---|---|
| `agent/graphs/` | LangGraph 图装配与边定义 | `build_graph()` |
| `agent/nodes/` | 图节点实现 | 状态 TypedDict 进 / 出 |
| `agent/core/` | 图状态定义与数据模型 | `MentalHealthState` |
| `agent/algo/` | 早期守护判定：特征、打分、状态机 | `analyze()` / `tier_of()` / `MIN_RECENT_DAYS` |
| `agent/long_memory/` | 固化、检索、趋势、画像、遗忘、风险留痕 | `retrieve` / `store` / `risk_state` / `trend` |
| `agent/care/` | 主动关怀决策、文案、运行时、仪式 | `policy` 纯函数 / `mark_sent` / `mark_reminder` |
| `agent/pet/` | 打扰等级、角色卡加载与校验 | `mode` / `character` |
| `agent/agenda/` | 日程存储与到点提醒 | `store` / `reminder` / `brief` |
| `agent/web/` | HTTP 路由与会话持久化 | `routes_*` / `sessions` |
| `agent/config/` | 配置聚合出口，按域拆分 | `base` / `memory` / `guard` / `pet` / `care` |
| `apps/web/src/shell/` | 全局状态、主题、HTTP 封装、导航 | 供业务模块单向引用 |
| `apps/web/src/modules/` | `chat` / `cards` / `settings` / `calendar` 四个业务域 | 各自持有 View、组合式函数、API、类型 |
| `apps/desktop/electron/` | 主进程与预加载脚本 | `main.cjs` / `preload.cjs` |
| `apps/desktop/src/` | 桌宠渲染层：动画、气泡、输入框、悬停图标 | 仅消费后端 HTTP |

前端模块之间禁止相互引用；跨模块需求经 `shell/store.ts` 或 `shell/api/` 中转。设置页内嵌角色卡分页为受控例外。

## 二、关键技术特性

### 2.1 零 LLM 判定层

- `triage` 与 `algo` 全程纯函数，不发起模型请求，判定结果可离线复算。
- 守护判定由七条独立路径给出：`emotion`、`chronic`、`behavior`、`fast`、`dimension`、`precursor`、`floor`；取最大路径得分作为基础分，各路径带封顶系数，弱路径仅推至 `watch`。
- 档位由双阈值滞回状态机输出，默认进入阈值 `watch=0.55`、`elevated=0.78`，退出阈值 `watch=0.38`、`elevated=0.58`；阈值顺序异常时回退默认值并告警。
- 弱路径设封顶系数：`fast`、`precursor`、`floor` 最高推至 `watch`，避免单一弱信号直接把档位抬到 `elevated`。
- 有效记录天数低于 `MIN_RECENT_DAYS`（2 天）时输出 `insufficient`，档位保持 `stable`。
- 每轮判定产出逐段 trace，记录各路径得分、特征快照与决策依据，供调试页与审计使用。
- 判定档位写入 `risk_state`，仅内部留痕：不通知外部方、不向用户暴露内部标签、不改变危机协议走向。

### 2.2 危机与情绪解耦

- 危机四要素（意念 / 意图 / 计划 / 行为）独立存储、独立计数，不参与情绪聚合与纵向趋势计算。
- 显式危机表达当轮进入同步协议，输出固定话术与热线信息，不再经过陪伴生成。
- 弱危机信号开启确认窗（5 轮 / 24 小时），响应形态为共情与留门，不主动提问、不试探。
- 确认窗内再次表达或出现固化证据（`intent` / `plan` / `behavior`）时升级；否认则冻结，自然消退撤窗并留痕。
- 留痕 facet 复用受保护的 `risk_events`（`soft_alert` / `retracted` / `uncertain` / `review_support`）。

### 2.3 长期记忆

分层沉淀，读取同样分层。

| 层 | 内容 |
|---|---|
| 事实与切片 | `topics` / `dimension_evidence` / `triggers` / `coping` 及状态弧 |
| 日桶时序 | 按天的活动、情绪、危机分面记录 |
| 趋势 | 个人基线、恶化检测、长期窗口摘要 |
| 画像 | 近况、特质卡、触发模式 |
| 遗忘 | 基于衰减曲线的淘汰与归档 |
| 读侧分道 | 按时效性分为档案 / 状态 / 事件三条道，按用途分配配额 |
| 按问题取用 | 以当前用户语句对候选排序，主题词、字面词与内容 bigram 共同计分 |

- 固化采用双采样并集：同一对话段抽取两次，取并集并记录支持度；仅命中一轮的事实标记为 `weak` 并降权，不丢弃。两次均失败时抛 `ConsolidationFailed`，该段退避重试。
- 基线采用中位数与 `1.4826 × MAD` 构造稳健中心与尺度，配合绝对位移地板双闸，抑制基线平稳期尺度坍缩导致的持续恶化误判。
- 相对时间锚定：固化时将「明天」「下周三」「本月 15 号」等相对表达按对话发生时刻确定性地换算为 ISO 日期，随事实落库；原文不做改写，粒度（日 / 周 / 月）随表达形态保留。
- SQLite 后端维护可重建的 FTS5 索引，仅用于候选召回；用户隔离、归档与敏感内容过滤、去重、字符预算裁剪仍由检索层完成，索引不可用时自动回退逐条相关性检索。
- 近期状态同时给出 3 天窗与 21 天窗，后者以窗口之外更早的区段为参照系，避免长期低落未期被短窗掩盖。

### 2.4 主动关怀与打扰等级

- 决策与文案分离：`policy` 为纯函数并保序输出候选类型；每条消息必经模型生成，模型不可用或输出违规时静默丢弃并记冷却，不写模板文案。
- 未获回应时按级退避并封顶，不设永久停止条件。
- 打扰等级为有序格（L0 全开 / L1 安静 / L2 勿扰 / L3 休眠），高等级包含低等级全部静默行为；任何等级均不阻断用户主动发起对话。
- 档位来源优先级：手动临时设置 > 自动夜间规则 > 用户默认设置。手动档位仅暴露前三档，`asleep` 由夜间规则独占。
- 夜间休眠默认 23:00–08:00 自动进入，与关怀免扰时段相互独立；00:00–06:00 禁止任何主动消息为不可配置的安全约束。
- 定时仪式（当前仅「晚安」）复用主动消息全链路，不占当日额度、不参与退避、受打扰等级约束，当天幂等且跨午夜不补发。

### 2.5 隐私控制面

- 暂停记录、单条删除、一键清空、JSON 导出，以及数据落盘位置只读展示。
- 记忆原文类接口受 `MEMORY_INSIGHT_ENABLED` 开关控制，对外部署应置为关闭。
- 清空操作采用二次确认；导出与清空的范围限定为当前用户键命名空间。
- 仓库不包含任何运行期记忆数据、导出快照与本地连接配置，`memory_data/`、`runtime_connection.json`、记忆存档目录均在 `.gitignore` 内。

### 2.6 桌宠运行时

- 动画采用 PNG 序列帧，单帧显示时长编码于文件名末段，例如 `A_000_125.png` 表示该帧停留 125 ms。
- 打扰等级驱动动画选择：L1 降频、L2 仅呼吸帧、L3 进入睡眠序列并保持睡姿。
- 靠近行为可配置（`pet.approach_enabled` / `pet.approach_interval_min`，取值 1–120 分钟），随设置快照下发。
- 输入框由悬停唤起，10 秒无输入自动收起，已输入内容时保持展开；发送后需重新进入悬停态方可再次唤起。
- 桌宠窗口使用逐像素 alpha 采样实现点击穿透，静态资源经自定义 `pet-asset://` 协议提供并做目录穿越校验。
- 渲染层不复制主进程状态：输入框显隐、打扰等级、动画状态均由渲染层持有，主进程仅转发动作名。

## 三、环境依赖与技术栈

### 3.1 运行时要求

| 依赖 | 版本 | 用途 |
|---|---|---|
| Python | ≥ 3.13 | 后端服务与图运行时 |
| uv | 近期版本 | 依赖解析、虚拟环境、命令执行 |
| Node.js / npm | ≥ 18 | 前端构建与 Electron 打包 |
| 操作系统 | Windows 10/11 x64 | 桌面端目标平台；后端可独立运行于其他平台 |

### 3.2 依赖清单

后端（`pyproject.toml`）：

| 包 | 约束 | 用途 |
|---|---|---|
| `langgraph` | `>=1.2.0,<2.0` | 对话图编排 |
| `langgraph-checkpoint-sqlite` | `>=3.0.0,<4.0` | 图状态检查点 |
| `langchain-openai` | `>=1.6.0,<2.0` | OpenAI 兼容模型客户端 |
| `langchain-core` | `>=1.6.0,<2.0` | 消息与工具抽象 |
| `pydantic` | `>=2.13.0,<3.0` | 请求与配置校验 |
| `fastapi` | `>=0.141.1` | HTTP 接口 |
| `uvicorn[standard]` | `>=0.52.4` | ASGI 服务器 |
| `pytest` / `pytest-asyncio` / `httpx` | 开发组 | 测试与接口调用 |
| `pyinstaller` | `>=6.0` | 后端可执行文件打包 |

前端（`apps/web/package.json`）：

| 包 | 约束 | 用途 |
|---|---|---|
| `vue` | `^3.5.13` | 视图层 |
| `vite` | `^6.3.5` | 构建与开发服务器 |
| `tailwindcss` / `@tailwindcss/vite` | `^4.1.4` | 样式系统 |
| `typescript` / `vue-tsc` | `~5.8.3` / `^2.2.12` | 类型检查 |
| `marked` / `dompurify` | `^15` / `^3.2.4` | 消息 Markdown 渲染与净化 |

桌面端（`apps/desktop/package.json`）：

| 包 | 约束 | 用途 |
|---|---|---|
| `electron` | `^33.2.1` | 桌面运行时 |
| `electron-builder` | `^25.1.8` | NSIS 安装包构建 |

### 3.3 模型服务

后端通过 OpenAI 兼容协议访问模型，需配置三项参数：`base_url`、`api_key` 与模型 ID。三项均无内置默认值，配置层出厂为空串，不绑定任何服务商；未配置完整时对话接口返回 `503` 并在响应中列出缺失项，判定层与记忆检索不依赖模型，照常工作。模型 ID 由输入框自由填写并原样下发，不做厂商映射与白名单限制。

## 四、快速开始

### 4.1 安装

```bash
# 1) 后端依赖（uv 自动创建 .venv）
uv sync

# 2) 功能界面依赖与构建
cd apps/web && npm install && npm run build && cd ..

# 3) 桌面端依赖与构建
cd apps/desktop && npm install && npm run build && cd ..
```

桌宠动画素材与初始角色卡随仓库提供，无需额外下载：`pet/assets/`（序列帧，约 4 MB）、`pet/cards/sari.json`（首次启动装入可写数据目录）。

### 4.2 启动

```bash
cd apps/desktop
npm start
```

后端由 Electron 自动拉起，端口从 `8000` 起探测空闲值，地址注入渲染层。重复执行不会产生第二个实例，单实例锁会将已有窗口置前。

仓库根目录提供两个入口脚本：`启动心伴.cmd` 启动桌面端，`启动后端.cmd` 仅启动后端（要求 `.venv` 已存在）。

### 4.3 模型配置

| 方式 | 操作 | 适用范围 |
|---|---|---|
| 界面配置 | 主窗口 → 设置 → 模型与连接，填写 Base URL、API Key 与模型 ID，测试连接后保存，即时生效 | 桌面端与功能界面 |
| 环境变量 | 设置 `LLM_API_KEY`、`OPENAI_BASE_URL`、`LLM_MODEL` | 命令行、服务器部署 |

三项参数的有效值来源优先级为：界面连接 > 环境变量 > 配置层常量。配置层出厂值指向千问 DashScope 兼容端点与 `qwen3.8-flash`，安装后仅需补 API Key。

#### 思考模式

千问系列默认开启思考，推理 token 与正文共享输出上限。实测 `qwen3.8-flash` 关闭与开启思考的差异：

| 通道 | 关闭思考首字 | 开启思考首字 |
|---|---|---|
| 对话回复（`REPLY_MAX_TOKENS`） | 0.8–1.3 s | 1.1–2.3 s |
| 主动关怀（`CARE_MESSAGE_MAX_TOKENS`） | 0.4–0.9 s | 1.7–2.8 s |

输出上限偏窄的通道在开启思考时会被推理耗尽预算，正文为空。因此调用层恒定在请求体写入 `enable_thinking: false`，不提供配置项：该字段属 DashScope 扩展，作为可选项存在时一旦被误设为开启或省略，都会在延迟或正文完整性上退化。修改该行为需改 `core/models/LLM_base.py` 的 `build_llm`，调用方仍可在 `extra_body` 中显式覆盖。

### 4.4 打包分发

```bash
cd apps/desktop
npm run dist     # vite build + PyInstaller 后端 + electron-builder
```

产物为 `apps/desktop/release/XinBan-Setup-<version>.exe`（NSIS，x64）。安装包内置 Python 后端（PyInstaller onedir），目标机器无需安装 Python、uv 或 Node.js。三段构建可独立执行：

| 命令 | 产物 |
|---|---|
| `npm run build` | `apps/desktop/dist/` 桌宠渲染层 |
| `npm run backend` | `apps/desktop/backend-dist/gzhu-backend/` 后端可执行目录 |
| `npx electron-builder --win` | `apps/desktop/release/*.exe` 安装包 |

打包态数据目录为 `<程序目录>/data`，目录不可写时回落 `%APPDATA%/心伴/`。主进程注入 `GZHU_DATA_DIR`、`LONG_MEMORY_DIR`、`GZHU_CONNECTION_PATH`、`GZHU_CARD_DIR` 四个环境变量指路。

### 4.5 其他运行方式

```bash
# Web 开发模式：后端与前端分终端启动，前端 /api 代理至 8000
uv run uvicorn gzhu_cup2.entrypoints.api:app --reload
cd apps/web && npm run dev          # http://localhost:5173

# 纯后端
uv run uvicorn gzhu_cup2.entrypoints.api:app --host 127.0.0.1 --port 8000

# 命令行交互，逐轮打印风险档与情绪
uv run gzhu-cup2 --thread chat-1
```

## 五、项目结构

```text
Gzhu-Cup2/
├── src/gzhu_cup2/
│   ├── agent/
│   │   ├── agenda/         日程存储、到点提醒、日程摘要
│   │   ├── algo/           早期守护：特征、七路径打分、状态机、trace
│   │   │   └── components/ 取数、特征、打分、状态机、分诊、词表、结构
│   │   ├── care/           主动关怀：决策、文案、运行时、仪式
│   │   ├── common/         通用工具
│   │   ├── config/         配置聚合出口（base / memory / guard / pet / care）
│   │   ├── core/           图状态与数据模型
│   │   ├── graphs/         主图与记忆图装配
│   │   ├── long_memory/    固化、检索、趋势、画像、遗忘、风险留痕、相对时间
│   │   ├── nodes/          图节点
│   │   ├── pet/            打扰等级、角色卡加载与校验
│   │   ├── prompts/        提示词组装
│   │   ├── tools/          运维与评测脚本
│   │   └── web/            FastAPI 路由与会话持久化
│   └── entrypoints/        HTTP 服务与 CLI 入口
├── apps/
│   ├── web/src/            shell（状态/主题/HTTP）+ modules（chat/cards/settings/calendar）
│   └── desktop/            electron 主进程 + 桌宠渲染层 + 字体与图标
├── pet/                    序列帧素材与初始角色卡
├── tests/                  80 个测试模块、夹具与手动验收脚本
├── docs/                   设计文档
├── packaging/              PyInstaller 入口与构建脚本
└── pyproject.toml          依赖、版本与 pytest 配置
```

## 六、API 概览

所有接口挂载于本地 HTTP 服务，用户命名空间经 `X-User-Id` 头指定，缺省为 `local_user`。

| 分组 | 端点 |
|---|---|
| 对话与会话 | `POST /api/chat`、`POST /api/chat/stream`、`GET/POST /api/threads`、`GET/PATCH/DELETE /api/threads/{thread_id}` |
| 主动关怀 | `GET /api/care/status`、`POST /api/care/evaluate`、`GET /api/care/pending`、`POST /api/care/pending/{msg_id}/dismiss` |
| 桌宠 | `GET/PATCH /api/pet/mode`、`GET /api/pet/settings`、`GET/PATCH /api/pet/daily`、`POST /api/pet/daily/regenerate`、`GET/POST/DELETE /api/pet/cards`、`POST /api/pet/cards/active`、`GET /api/pet/greeting` |
| 日程 | `GET/POST /api/agenda/events`、`PATCH/DELETE /api/agenda/events/{event_id}` |
| 设置 | `GET/PATCH /api/settings` |
| 用户资料 | `GET /api/user/profile`、`POST /api/user/profile/onboarding` |
| 记忆与隐私 | `GET/PATCH /api/memory/privacy`、`GET /api/memory/export`、`DELETE /api/memory/all`、`GET /api/memory/debug`、`GET /api/memory/trace`、`GET /api/memory/insight` |
| 连接与健康 | `GET /api/health`、`GET /api/connection`、`POST /api/connection/test` |

## 七、测试

```bash
# 全量
uv run pytest

# 单模块
uv run pytest tests/test_guard.py -q
```

测试默认将临时目录重定向至仓库内（根 `conftest.py`），并使用独立数据目录隔离运行期状态，真实记忆库不受影响。进程内并发用例数过高时可能触发文件清理失败，按测试文件分批执行即可规避。

## 八、数据存储与隐私

| 文件 | 内容 |
|---|---|
| `memory.sqlite3` | 长期记忆 KV：事实、片段、日桶、风险留痕、待固化队列、桌宠日常 |
| `sessions.sqlite3` | 会话元数据与逐字轮次 |
| `memory_fts.sqlite3` | 可重建的 FTS5 候选索引 |
| `runtime_connection.json` | 模型连接配置（含明文 Key） |
| `cards/` | 用户自建角色卡 |

开发态目录为 `src/gzhu_cup2/agent/memory_data/`，打包态为 `<程序目录>/data/memory`。全部文件仅存于本机，不参与任何远端同步；上述路径均列入 `.gitignore`。

## 九、已知边界

- 系统不提供诊断、治疗或用药建议，输出结果不构成医学结论。
- 情绪与风险由模型依据对话内容隐式推断，非标准化量表施测，不具备心理测量学信效度。
- 实例为单用户本地部署，接口无鉴权机制；`X-User-Id` 仅用于记忆命名空间隔离，不作为安全边界。对外暴露服务前须自行加装访问控制。
- 判定层不依赖模型，但回复生成、记忆抽取与主动文案依赖模型可用性；模型不可用时对应通道静默降级。
- 记忆抽取、隐私意愿判定与复核结论通过工具调用（function calling）获取结构化输出，要求所接服务支持 `tools` 与带 JSON Schema 参数的函数调用；不支持时该链路失败并退避重试，对话与判定层不受影响。结构化调用不指定具体工具名，以兼容推理模式下的工具选择限制。
- 思考默认关闭且不可配置。接入推理开销不可关闭的模型时，其推理 token 仍与正文共享输出上限，需上调 `REPLY_MAX_TOKENS`、`MEMORY_CONSOLIDATE_MAX_TOKENS` 等通道上限，否则输出会在推理阶段耗尽预算。
- 早期守护档位仅内部留痕，不向外部机构或第三方传递。
- 全屏检测未实现：跨应用全屏状态需引入原生模块或轮询系统进程，涉及额外隐私面，当前由手动档位与夜间规则覆盖。

## 十、声明

本项目为竞赛与课程用途，不构成医疗器械，不替代专业诊断与治疗。出现自伤、伤人风险或持续心理困扰时，应优先联系精神卫生机构或拨打心理援助热线 12356、急救电话 120。

桌宠动画素材复刻自 [VPet](https://github.com/LorisYounger/VPet)。其源码以 Apache License 2.0 发布，动画素材另附独立版权声明，本仓库仅将复刻结果用于课程与演示目的，未作商业使用；再分发前需自行确认授权条款。

引用外部知识数据集（如 PsyQA、CPsyCounD）时，遵循其各自的许可条款。
