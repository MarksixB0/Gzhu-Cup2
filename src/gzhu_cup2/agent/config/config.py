# -*- coding: utf-8 -*-
"""config 聚合出口(对外唯一入口;常量按域拆在同目录子模块)。

分域:base(模型/CORS/热线/对话分寸) · memory(长期记忆) · guard(守护阈值) ·
pet(桌宠) · care(主动关怀)。

⚠ 消费方一律 ``from gzhu_cup2.agent.config import config as cfg`` 后按属性读,
**不要直接 import 子模块**:测试钉值(conftest 的 setattr)与 DEV_FAST_MODE 覆盖
都只作用在本模块命名空间上,绕开它读到的是未覆盖的生产值。
"""
from gzhu_cup2.agent.config.base import *    # noqa: F401,F403
from gzhu_cup2.agent.config.memory import *  # noqa: F401,F403
from gzhu_cup2.agent.config.guard import *   # noqa: F401,F403
from gzhu_cup2.agent.config.pet import *     # noqa: F401,F403
from gzhu_cup2.agent.config.care import *    # noqa: F401,F403

# ============================================================================
# 开发调试开关(**必须留在文件末尾**:它要覆盖上面所有默认值)
# ============================================================================
# 把所有等待时间压到秒级、取消免扰时段、放宽每日上限,便于开发联调。
# 仅供开发/测试,正式部署改回 False。
# 覆盖:关怀节拍 → 5 秒 / 每日 50 条 / 免扰关闭 / 新用户也搭话;
#      固化门槛 → 2 轮、空闲 20 秒。
DEV_FAST_MODE = False

_DEV_FAST_PRESET = {
    # ---- 主动关怀:调度节拍 ----
    "CARE_EVAL_INTERVAL_SECONDS": 5,      # 每 5 秒评估一次
    "CARE_COOLDOWN_HOURS": 0.01,          # 两条主动消息最短间隔 ≈ 36 秒
    "CARE_MAX_PER_DAY": 50,               # 每日上限放宽
    # ---- 免扰时段:关闭(start == end 即全天无免扰) ----
    "CARE_QUIET_START_HOUR": 0,
    "CARE_QUIET_END_HOUR": 0,
    # ---- 各类型触发间隔:全部压到秒级 ----
    "CARE_RECALL_MIN_GAP_HOURS": 0.01,
    "CARE_CHECKIN_GAP_HOURS": 0.01,
    "CARE_SOOTHE_MIN_GAP_HOURS": 0.005,
    "CARE_SOOTHE_RECENT_HOURS": 1,
    "CARE_PETLIFE_MIN_GAP_HOURS": 0.01,
    "CARE_PROBE_MIN_GAP_HOURS": 0.005,
    "CARE_PROBE_MAX_GAP_HOURS": 999,
    "CARE_PROBE_MIN_GAP_DAYS": 0,
    # ---- 允许给"从未活跃过"的新用户主动搭话(便于全新环境立刻验证) ----
    "CARE_REQUIRE_ACTIVITY": False,
    # ---- 未获回应退避:同样压到秒级,否则第一条之后要等 24 小时,没法测试 ----
    # 36 秒 → 72 秒 → 108 秒(封顶),几分钟内就能观察到"间隔逐次拉长"
    "CARE_MIN_USER_SILENCE_MINUTES": 0,   # 开发联调:不等静默,立刻可发
    "CARE_FAREWELL_QUIET_HOURS": 0.005,    # 开发联调:18 秒后即解封
    "CARE_UNANSWERED_BACKOFF_HOURS": (0.01, 0.02, 0.03),
    # ---- 记忆固化门槛(让可主动提及的话题尽快出现) ----
    "MEMORY_CONSOLIDATE_MIN_TURNS": 2,
    "MEMORY_IDLE_SECONDS": 20,
    "MEMORY_WORKER_POLL_SECONDS": 2,
}

# 生产值快照:应用 dev 预设之前记录的键原值。
# tests/conftest.py 读本表把键钉回生产值。
PROD_DEFAULTS = {k: globals().get(k) for k in _DEV_FAST_PRESET}

if DEV_FAST_MODE:
    globals().update(_DEV_FAST_PRESET)
