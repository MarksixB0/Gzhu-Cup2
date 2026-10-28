# -*- coding: utf-8 -*-
"""config·桌宠域:每日生活 / 打扰等级 / 角色卡。

仅供 ``config.py`` 聚合,业务代码不要直接 import 本模块
(测试钉值与 DEV_FAST_MODE 覆盖只作用在聚合命名空间上)。
"""
import os

from gzhu_cup2.agent.config.memory import MEMORY_DIR

# ---------- 桌宠域(pet):每日生活 / 分享日常 ----------
PET_STATE_PATH = os.path.join(MEMORY_DIR, "pet_runtime.json")
# 空闲时「主动走到鼠标旁」的行为:默认开,间隔分钟数用户可在设置里改。
PET_APPROACH_ENABLED = True
PET_APPROACH_INTERVAL_MIN = 10
PET_DAILY_DEFAULT_ENABLED = True   # 每日生活叙事默认生成开关(用户可关)
PET_DAILY_MAX_LEN = 220            # 叙事文本截断长度(字符);1-3 句太短会显得没生活,见 narrative 提示词
PET_DAILY_MAX_TOKENS = 1024        # 叙事生成的 LLM 输出上限(token);思考强制开启,原 240 易被推理占满致空白
# 今日叙事每日**显式刷新**上限;0 = 不限制。
# 上限只挡次数,不改变"每次刷新 = 一次 LLM 调用"的成本,连点仍会连着调模型。
PET_DAILY_MAX_REGENERATES_PER_DAY = 0
# 生成叙事时最多参考的用户近况条数(自然语言摘要,不带内部数字)
PET_DAILY_REFERENCE_LIMIT = 3

# ---------- 桌宠打扰等级(Interruption Level;见 pet/mode.py 与 PLAN_NEXT.md §2) ----------
# 决定「此刻能多主动地打扰用户」。四档**有序**(active < quiet < dnd < asleep),
# 保证「更安静」永远包含「较安静」的全部行为;所有门控都用序数比较,不罗列组合。
PET_INTERRUPTION_DEFAULT = "active"   # 新用户默认档;手动菜单只暴露 active/quiet/dnd
# 夜间自动休眠:仅自动进入,不可手动选择。时段与 care 免扰时段对齐,两者互相独立。
PET_NIGHT_SLEEP_ENABLED = True
PET_NIGHT_SLEEP_START_HOUR = 23
PET_NIGHT_SLEEP_END_HOUR = 8
# 手动临时覆盖的常用时长(分钟),供前端做「专注 1 小时」这类快捷项。
PET_MANUAL_PRESET_MINUTES = (30, 60, 120)

# ---------- 角色卡(pet.character):可选人格层 ----------
# 优先级:角色卡 > 本文件的默认值。没有角色卡时回落到内置的纯助手提示词。
# 角色卡不在仓库里、不随安装包分发 —— 打包后包内目录只读,用户既加不了也改不动。
# 目录优先级:GZHU_CARD_DIR(桌面端注入) > MEMORY_DIR/cards。
PET_CARD_DIR = os.getenv("GZHU_CARD_DIR") or os.path.join(MEMORY_DIR, "cards")
# 当前启用角色卡(不含 .json);空串 = 不搭载角色卡。运行时会被 active_card.json 覆盖。
PET_ACTIVE_CARD = ""
# 角色卡 {{user}} 宏的替换名;空串 = 先用卡内 user_default_name,再没有则用「你」。
PET_USER_DISPLAY_NAME = ""
