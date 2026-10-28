# -*- coding: utf-8 -*-
"""config·主动关怀域:频率门槛 / 免扰 / 仪式 / probe / 退避。

仅供 ``config.py`` 聚合,业务代码不要直接 import 本模块
(测试钉值与 DEV_FAST_MODE 覆盖只作用在聚合命名空间上)。
"""
import os

from gzhu_cup2.agent.config.memory import MEMORY_DIR

# ---------- 主动关怀(care):频率与门槛(开发/联调只需改这一段) ----------
# 本段就是"桌宠什么时候主动说话"的全部开关;调频不必动任何业务代码。
CARE_ENABLED = True                # 总开关(正式产品建议默认 False,用户同意后再开)
CARE_DEFAULT_ENABLED = True        # 单个用户的默认开关(用户可在界面关闭)

# ---- 免扰与每日上限(打扰红线) ----
CARE_QUIET_START_HOUR = 23         # 免扰时段起点(24 小时制);start == end 视为全天无免扰
CARE_QUIET_END_HOUR = 8            # 免扰时段终点(即 23:00 ~ 08:00 不说话)
CARE_MAX_PER_DAY = 2               # 每日主动消息上限
CARE_COOLDOWN_HOURS = 4            # 距上一条主动消息的最短间隔(小时)

# ---- 用户在场判定 ----
# 两条**与 kind 无关**的在场门槛(在决策最前面统一判定):
CARE_MIN_USER_SILENCE_MINUTES = 30   # 用户最后一次说话后至少静默 N 分钟才允许主动开口
# 用户明确表示要去睡/离开(「睡了」「晚安」「我先睡」)后的静默时长(小时)。
# 为什么单列一条:30 分钟静默挡不住"说完晚安又躺着刷手机"的场景 —— 那时用户
# 已经**宣告离场**,再开口就是把人从被窝里拽出来(petlife 那种"中午的云"尤其突兀)。
CARE_FAREWELL_QUIET_HOURS = 8
# 投递时二次判活:一条主动消息生成后若用户已经先说话了,它的前提就过期了
# (outbox 会攒到客户端轮询才弹),此时**静默丢弃**,不补发。
CARE_DROP_STALE_ON_DELIVERY = True

# ---- 定时仪式(ritual;见 care/ritual.py) ----
# 首批只做「晚安」。几点说晚安是**用户自己设的**时刻,所以它刻意
# **不走**上面的免扰时段判断。它仍然尊重:用户总开关、打扰等级(安静及以上不发)、
# 当天幂等、以及「窗口不跨午夜」(避免凌晨补发一句晚安)。
CARE_RITUAL_ENABLED = True
CARE_RITUAL_HOUR = 22              # 晚安时刻(24 小时制)
CARE_RITUAL_MINUTE = 30

# ---- 各决策类型的触发间隔(距"用户最近一次活跃"多久才算够久) ----
CARE_RECALL_MIN_GAP_HOURS = 24     # recall:有近期可主动话题时的回访
# 用户自述 likes 作为"好奇钩子"的最短间隔(天)。它比 recall 更严:
# 稳定偏好不是近况,不能天天问成打卡;只在没有可回访的近期事实时兜底。
CARE_PROFILE_LIKE_MIN_GAP_DAYS = 7
CARE_CHECKIN_GAP_HOURS = 48        # checkin:纯问候
CARE_SOOTHE_MIN_GAP_HOURS = 1      # soothe:低落回访(对话刚结束不发,防打扰)
CARE_SOOTHE_RECENT_HOURS = 6       # soothe:判定"刚离开低落"的观察窗口
CARE_SOOTHE_NEG_THRESHOLD = 0.55   # soothe:判定"低落收尾"的负向分阈值(0-1)
CARE_PETLIFE_MIN_GAP_HOURS = 36    # petlife:分享日常的最低间隔(低频不打扰)

# ---- 缺口探询(probe) ----
CARE_PROBE_ENABLED = False         # 总开关:False = 永不发探询,缺口窗口由 checkin/recall 兜底
CARE_PROBE_MIN_GAP_HOURS = 6       # 用户离开 ≥ 此值(小时)且 ≤ MAX 才有机会收到探询
CARE_PROBE_MAX_GAP_HOURS = 72      # 超过该时长没回来,不再发探询(改用 recall/checkin)
CARE_PROBE_MIN_GAP_DAYS = 3        # 同一维度两次探询的最短间隔(天),避免围着同一问题转
CARE_PROBE_MAX_CANDIDATES = 2      # 单次评估最多取几个缺口候选(实际只用第一个当话题)

# ---- 未获回应时的退避 ----
# 按已发出且未回应的条数取间隔:24h → 72h → 168h,用尽后保持最后一个值。
CARE_UNANSWERED_BACKOFF_HOURS = (24.0, 72.0, 168.0)
# 未获回应时只发存在感型(petlife/checkin),不发提问型(probe/recall/soothe);用户一说话即解封。
CARE_QUIET_ASK_WHEN_UNANSWERED = True

# ---- 其它门槛与产物控制 ----
CARE_REQUIRE_ACTIVITY = True       # 从未活跃过的用户不主动搭话(避免给陌生人打招呼)
CARE_EVAL_INTERVAL_SECONDS = 60    # 关怀调度线程的评估间隔(秒)
CARE_MIN_EVAL_INTERVAL_SECONDS = 5 # 评估间隔下限(防止误配 0 造成忙轮询)
CARE_MESSAGE_MAX_TOKENS = 1024     # 单条主动消息的 LLM 输出上限(token);思考强制开启,原 200 易被推理占满致主动消息空白
CARE_MESSAGE_MAX_LEN = 320         # 单条主动消息的字符上限(超出截断);petlife 要装下今日叙事+邀约

# ---- 主动消息"接话"素材 ----
# 模型看不到用户最近说了什么时,主动问候只能落到"这么晚还没睡"这类通用句;
# 把**用户最后一句**作为接话素材喂进去,只作素材、不复述。
CARE_REFER_LAST_USER = True        # 关闭则主动消息不读用户原话
CARE_LAST_USER_MAX_CHARS = 60      # 原话进提示词前截断(防长段粘贴塞满上下文)
CARE_LAST_USER_MAX_AGE_MINUTES = 120  # 超过这个时长就不是"接话"了,不再引用
CARE_LAST_USER_MAX_NEG = 0.6       # 该轮负向分超过此值不引用(主动消息不主动揭沉重话题)
# 用户在这段时间内说过话 → 手动触发的消息**不摆「主动消息」卡片**,当作对话里的
# 一句接着说。0 = 永远按主动消息投递。
CARE_INLINE_CHAT_MINUTES = 10
# 手动触发("让桌宠现在说点什么")的最小间隔:force 会跳过冷却,但不允许连点连发。
# 0 = 不限制。
CARE_FORCE_MIN_GAP_SECONDS = 60
CARE_PENDING_MAX_AGE_HOURS = 168   # 未读主动消息在 outbox 的保留时长(小时)
CARE_STATE_PATH = os.path.join(MEMORY_DIR, "care_runtime.json")
