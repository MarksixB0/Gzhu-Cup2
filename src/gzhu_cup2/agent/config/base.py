# -*- coding: utf-8 -*-
"""config·基础域:模型调用 / CORS / 危机热线 / 对话分寸。

仅供 ``config.py`` 聚合,业务代码不要直接 import 本模块
(测试钉值与 DEV_FAST_MODE 覆盖只作用在聚合命名空间上)。
"""
import os

# 给 LLM 调用。API Key 不在此处快照,统一由 runtime_connection.resolve_api_key() 解析。
# 模型名与端点给出厂默认值,设置面板「模型与连接」与环境变量
# (LLM_API_KEY / OPENAI_BASE_URL / LLM_MODEL)均可覆盖;三项齐备前对话接口返回 503
# 并点名缺失项,判定层与记忆检索不依赖模型,照常工作。
MODEL_NAME = "qwen3.8-flash"
MODEL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL_TEMPERATURE = 0.7
MODEL_IS_STREAMING = True

# 思考模式不开放配置:调用层恒定在请求体写入 enable_thinking=false。
# 千问系列默认开启思考,推理 token 与正文共享输出预算,不声明关闭时首字延迟
# 与总耗时接近翻倍,窄预算通道还会被推理占满而导致正文为空。

# 模型单点开关:True = 推理一律使用本文件的 MODEL_NAME(设置里的 default_model 仅作展示);
# False = 设置里保存的模型优先生效,未配置时退回 MODEL_NAME(默认为空 → 视为未配置)。
MODEL_SINGLE_SOURCE = False

# 心理援助热线
CRISIS_HOTLINES = (
    "全国统一心理援助热线：12356（拨打即转您所在地区的心理援助座席）\n"
    "备用热线：400-161-9995（希望24热线，全国）\n"
    "紧急情况请拨打 120 或前往最近医院急诊"
)


# ---------- CORS(浏览器跨域白名单) ----------
# 显式白名单,默认放行本机前端 dev server 与桌面端 file:// 源(其 Origin 为字符串 "null")。
# 凭据默认 False(本应用不使用 Cookie/HTTP 鉴权)。
# 对外暴露服务时用环境变量 CORS_ALLOW_ORIGINS 覆盖。
CORS_ALLOW_ORIGINS = tuple(
    o.strip() for o in (
        os.getenv("CORS_ALLOW_ORIGINS")
        or "http://localhost:5173,http://127.0.0.1:5173,"
           "http://localhost:5180,http://127.0.0.1:5180,"
           "null"          # Electron loadFile() = file:// → Origin: null
    ).split(",")
    if o.strip()
)
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "").strip().lower() in ("1", "true", "yes")


# ---------- 长度控制 ----------
# 上限按「一句完整回复 + 提示词余量」设定:思考恒定关闭后推理不再占用额度,
# 但接入不可关闭推理的模型时仍会共享该预算,故留有余量。
# 三档保持 steady>gentle>caring 的分寸梯度。
REPLY_MAX_TOKENS = 1536            # 平常档(思考+正文合计预算)

# ---------- 守护档位 → 对话「分寸」(care_intensity) ----------
# 用途:把纵向守护判定转成**对话分寸**,走既有的 care.mode / 角色卡提示词链路即可,
# **不把档位本身写进任何提示词**。
# 边界:1. 绝不把档位/风险类字样写进提示词;2. 不改变主动消息的条数与时机上限;
#       3. 危机路径完全不经由此处。
# 关于 reply 预算:分寸越靠近,回复越短 —— 目的是"多听少说"。
#   注意它**不会**让桌宠显得冷淡:角色的 post_history(如 Sayori 的 20 字以内)
#   本来就短,这里只是收紧上限,不改变人格与话量基调。
PET_CARE_TONE_ENABLED = True        # 总开关:False = 完全回到加入本机制之前的行为
REPLY_MAX_TOKENS_GENTLE = 1280      # 留意:略收(仍须留足思考预算,原 240 在 gentle 档会空回复)
REPLY_MAX_TOKENS_CARING = 1024      # 持续关切:再收一点(仍须留足思考预算,原 200 在 caring 档会空回复)
