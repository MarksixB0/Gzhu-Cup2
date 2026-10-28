# -*- coding: utf-8 -*-
"""config·守护域(algo):状态机阈值 + 危机确认链。

仅供 ``config.py`` 聚合,业务代码不要直接 import 本模块
(测试钉值与 DEV_FAST_MODE 覆盖只作用在聚合命名空间上)。
"""

# ---------- 守护层(algo):早期风险识别,个人基线 + 分数 + 状态机 ----------
# 阈值全部相对用户自身;单信号永不单独定档。
GUARD_ENGAGEMENT_DROP = 0.4        # 近/远半窗日均轮次降幅 ≥ 此比例 = 参与度下滑(增益判定用)
GUARD_INACTIVE_DAYS = 3            # 距最近活跃 ≥ N 自然日 = 疏离(care 感知用)
# ---- 进入/退出双阈值(0-1 分数口径,刻度见 algo/components/score.py) ----
GUARD_ENTER_WATCH = 0.55           # 升到 watch 的进入线
GUARD_ENTER_ELEVATED = 0.78        # 升到 elevated 的进入线(严格高于 watch,杜绝档位倒挂)
GUARD_EXIT_WATCH = 0.38            # 已在 watch 时,跌破此值才考虑降档
GUARD_EXIT_ELEVATED = 0.58         # 已在 elevated 时,跌破此值先降到 watch
GUARD_MIN_DWELL_HOURS = 48.0       # 进入新档后的最短停留(防边界抖动);升档不受此限
# 危机后脆弱窗(小时):最近一次 crisis 轮之后禁止降档。
GUARD_CRISIS_PROTECT_HOURS = 72.0

# ---- 危机确认链(algo/components/crisis_chain) ----
# soft 警铃开窗后,窗内再表达/level≥2 证据才升级 hard 协议;全程不主动提问。
CRISIS_CONFIRM_TURNS = 5      # 窗内连续 N 轮无新证据 → 撤销警铃
CRISIS_CONFIRM_WINDOW_H = 24.0  # 确认窗最长时长(小时),超时自动撤销
