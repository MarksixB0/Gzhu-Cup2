# -*- coding: utf-8 -*-
"""用户自述资料:称呼 / 喜欢的事物 / 讨厌的事物(附首启引导状态)。

为什么单独一个模块
------------------
这三项是**用户自己说的、关于自己的稳定信息**,与从对话里推断的记忆不是一回事:

  - 它们不经过固化(``consolidate``),也不参与趋势 / 风险判定 —— 用户填一次
    "最近睡不好"不应被系统当成一次风险信号(否则违反「单信号不触发」);
  - 它们要被两处直接消费:``{{user}}`` 宏(称呼)与生成提示词的背景段;
  - 它们必须能被用户随时改(设置 → 用户)、随时删(清空记忆)。

**与风险判定的红线**:本模块只提供"怎么称呼、聊什么更贴"的信息,
``ticks`` / ``trend`` / ``guard`` 一律不读它。

存储
----
``<数据目录>/user_profile.json`` —— 与记忆同目录,因此「清空全部记忆」能连它的
**内容**一起清(见 ``routes_admin.memory_wipe``);留一份就是假删除。
首启引导状态刻意**不**跟着清 —— 那是"问过几次"的防骚扰计数,不是用户填的东西,见 ``clear``。
锁 / 原子替换复用 ``common.json_state.JsonStateFile``(与 care / pet 两个域同一套)。

**单实例单份**:这份资料描述"这台机器的主人",不按 ``X-User-Id`` 分区
(记忆按用户键分区、会话是全局单流,这一层沿用同一取舍)。多用户部署需要另加分区。

首启引导
--------
``onboarding`` 只有三个事实:问过几次(``seen``)、完成(``completed_at``)、
跳过(``dismissed_at``)。最多主动问 ``MAX_PROMPT_TIMES`` 次 —— 用户不回答也没关系,
但**不能每次启动都问一遍**(那是骚扰,不是引导)。
"""
import re
import time
from typing import Any, Dict

from gzhu_cup2.agent.common.json_state import JsonStateFile
from gzhu_cup2.agent.config import config as cfg

# ---------------------------------------------------------------- 字段与限额
FIELDS = ("display_name", "likes", "dislikes")

# 超长**拒绝而不是截断**:静默截断会让用户以为自己填的那一大段被记住了。
MAX_NAME_LEN = 20
MAX_LIST_LEN = 300
# 注入提示词时再收一次:提示词预算比存储预算紧,这两块不该挤掉记忆与本轮输入。
MAX_PROMPT_LIST_CHARS = 120

# 首启引导最多主动问几次(达到即视为跳过,避免每次启动都问一遍)。
MAX_PROMPT_TIMES = 3

# 控制字符(保留 \t \n):它们会破坏气泡渲染与提示词分段。
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _empty() -> dict:
    return {
        "display_name": "",
        "likes": "",
        "dislikes": "",
        "onboarding": {"seen": 0, "completed_at": None, "dismissed_at": None},
    }


def _ensure_memory_dir() -> None:
    """使用默认路径时确保记忆目录存在(重定向到自定义路径时不会触发)。"""
    from gzhu_cup2.agent.long_memory import paths
    paths.ensure_memory_dir()


_STATE = JsonStateFile(
    cfg.USER_PROFILE_PATH, _empty, on_default_path=_ensure_memory_dir)


# ---------------------------------------------------------------- 测试与部署钩子
def set_state_path(path: str) -> None:
    """重定向状态文件(测试隔离 / 部署);空串回到默认路径。"""
    _STATE.set_path(path)


def reset() -> None:
    """清空为"空状态"(测试隔离用;清用户资料请用 ``clear``)。"""
    _STATE.reset()


# ---------------------------------------------------------------- 清洗
def _clean_name(value: Any) -> str:
    """称呼:去掉控制字符与换行、折叠空白、限长。"""
    s = _CTRL_RE.sub("", str(value or "")).replace("\r", " ").replace("\n", " ")
    s = " ".join(s.split())
    if len(s) > MAX_NAME_LEN:
        raise ValueError(f"称呼最多 {MAX_NAME_LEN} 个字符(当前 {len(s)} 个)")
    return s


def _clean_list(value: Any) -> str:
    """喜欢 / 讨厌:保留换行(用户可能分条写),去控制字符、限长。"""
    s = _CTRL_RE.sub("", str(value or "").replace("\r\n", "\n").replace("\r", "\n"))
    s = s.strip()
    if len(s) > MAX_LIST_LEN:
        raise ValueError(f"最多 {MAX_LIST_LEN} 个字符(当前 {len(s)} 个)")
    return s


def _clean(field: str, value: Any) -> str:
    if field == "display_name":
        return _clean_name(value)
    if field in ("likes", "dislikes"):
        return _clean_list(value)
    raise ValueError(f"未知字段: {field}")


# ---------------------------------------------------------------- 读写
def get_field(field: str) -> str:
    if field not in FIELDS:
        raise ValueError(f"未知字段: {field}")
    return str(_STATE.read().get(field) or "")


def set_field(field: str, value: Any) -> str:
    """写一个字段并返回清洗后的值;非法值抛 ``ValueError``(由路由转 400)。"""
    cleaned = _clean(field, value)
    with _STATE.locked() as data:
        data[field] = cleaned
    return cleaned


def display_name() -> str:
    """用户希望被怎么称呼;没填返回空串。"""
    return get_field("display_name")


# ---------------------------------------------------------------- 首启引导
def _public_onboarding(data: Dict[str, Any]) -> dict:
    ob = dict(_empty()["onboarding"])
    raw = data.get("onboarding")
    if isinstance(raw, dict):
        for key in ob:
            if key in raw:
                ob[key] = raw[key]
    try:
        seen = int(ob.get("seen") or 0)
    except (TypeError, ValueError):
        seen = 0
    completed = bool(ob.get("completed_at"))
    dismissed = bool(ob.get("dismissed_at"))
    return {
        "seen": seen,
        "completed": completed,
        "dismissed": dismissed,
        # needed=False 之后**永不重复询问**;用户想改就去「设置 → 用户」改。
        "needed": (not completed) and (not dismissed) and seen < MAX_PROMPT_TIMES,
    }


def onboarding_state() -> dict:
    return _public_onboarding(_STATE.read())


def mark_onboarding(action: str) -> dict:
    """记录首启引导的进展。

    action:
      - ``seen``     本次启动已展示过一问(计入次数上限);
      - ``complete`` 三问都答完了;
      - ``skip``     用户点了跳过 —— **永久不再主动问**。
    """
    act = str(action or "").strip().lower()
    if act not in ("seen", "complete", "skip"):
        raise ValueError("action 只接受 seen / complete / skip")
    now = time.time()
    with _STATE.locked() as data:
        ob = data.get("onboarding")
        if not isinstance(ob, dict):
            ob = dict(_empty()["onboarding"])
            data["onboarding"] = ob
        if act == "seen":
            try:
                ob["seen"] = int(ob.get("seen") or 0) + 1
            except (TypeError, ValueError):
                ob["seen"] = 1
        elif act == "complete":
            ob["completed_at"] = now
            ob["dismissed_at"] = None
        else:
            ob["dismissed_at"] = now
    return onboarding_state()


# ---------------------------------------------------------------- 对外视图
def view() -> dict:
    """设置面 / API 用的快照(不含路径等内部信息)。"""
    data = _STATE.read()
    out: Dict[str, Any] = {field: str(data.get(field) or "") for field in FIELDS}
    out["onboarding"] = _public_onboarding(data)
    return out


def prompt_block() -> str:
    """拼进生成提示词的【关于用户 · 用户自己说的】段;三项全空时返回空串。

    刻意带一句"不要主动复述":这些是用户一次填完的稳定信息,
    若每轮都提"我记得你喜欢猫",陪伴就退化成念台账。
    """
    data = _STATE.read()
    name = str(data.get("display_name") or "").strip()
    likes = str(data.get("likes") or "").strip()
    dislikes = str(data.get("dislikes") or "").strip()
    if not (name or likes or dislikes):
        return ""

    def _clip(text: str) -> str:
        return " ".join(text.split())[:MAX_PROMPT_LIST_CHARS]

    lines = ["【关于用户 · 用户自己说的】"]
    if name:
        lines.append("称呼：" + _clip(name))
    if likes:
        lines.append("喜欢：" + _clip(likes))
    if dislikes:
        lines.append("讨厌：" + _clip(dislikes))
    lines.append("（这些只用于称呼与话题选择；不要主动复述，也不必每轮都提起。）")
    return "\n".join(lines)


def clear() -> bool:
    """清空三项内容;返回**清空前是否真的有内容**(供清空记忆的回执)。

    **刻意不重置 ``onboarding``**:用户在首启引导里点过「跳过」,就是对"别再问了"
    给了明确答复;清空记忆是另一码事,不能顺手把那个承诺一起作废 —— 否则
    「跳过之后永不重复询问」(README / PET_POLICY §五)只是句空话:用户每清一次
    记忆,就要再被问一遍"我该怎么称呼你"。

    同理保留 ``seen``:那是"问过几次"的防骚扰计数,不是用户填的内容;
    清掉它等于把「最多问 3 次」的上限一并归零。
    """
    data = _STATE.read()
    had = any(str(data.get(f) or "").strip() for f in FIELDS)
    with _STATE.locked() as cur:
        for field in FIELDS:
            cur[field] = ""
    return had
