# -*- coding: utf-8 -*-
"""主动关怀与桌宠路由:/api/care/*、/api/pet/*。

这些接口是**通用能力** —— 网页端与未来的桌宠前端复用同一套(见 PET_POLICY.md)。
本模块是「web 层唯一认识 care/pet 的地方」;业务逻辑仍在对应域内。
"""
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from gzhu_cup2.agent.care import coordinator as ccoord
from gzhu_cup2.agent.care import runtime as crt
from gzhu_cup2.agent.config import config as cfg
from gzhu_cup2.agent.pet import narrative as pn
from gzhu_cup2.agent.pet import runtime as prt
from gzhu_cup2.agent.pet.character import loader as cloader
from gzhu_cup2.agent.web import sessions

router = APIRouter()

# 用户标识请求头(与 /api/chat 同一口径,见 web/sessions.resolve_user)
_USER_HEADER = Header(
    default=None, alias="X-User-Id",
    description="长期记忆归属的用户键(可选;非法值回落默认用户)")


class CareEvaluateRequest(BaseModel):
    force: bool = Field(
        default=False,
        description="手动强制:绕过时机闸(免扰时段/沉默门槛/冷却/每日上限/未回应退避);"
                    "总开关、用户开关与打扰等级不被绕过")


class PetDailyUpdateRequest(BaseModel):
    text: str = Field(description="用户编辑后的今日叙事文本;后端截断到 PET_DAILY_MAX_LEN")


# ============================ 主动关怀 ============================
@router.get("/api/care/status")
def care_status(x_user_id: Optional[str] = _USER_HEADER):
    """关怀运行状态:开关 / 今日已发 / 最近主动时间 / 待读条数。"""
    return crt.status(sessions.resolve_user(x_user_id))


@router.post("/api/care/evaluate")
def care_evaluate(req: Optional[CareEvaluateRequest] = None,
                  x_user_id: Optional[str] = _USER_HEADER):
    """手动触发一次关怀评估(演示/联调用;调度线程也会自动跑)。

    force=True(用户点了「让桌宠现在说点什么」):绕过**时机闸** ——
    免扰时段/沉默门槛/冷却/每日上限/未回应退避;**不绕过**总开关、用户开关与
    打扰等级(那是知情权本身),消息生成与禁词过滤照常。
    勿扰等级下返回 ``blocked="quiet_mode"``,前端据此置灰并说明,而不是静默无反应。
    """
    user = sessions.resolve_user(x_user_id)
    force = bool(req.force) if req is not None else False
    if force:
        from gzhu_cup2.agent.pet import mode as pmode  # 局部导入:避免 care 域反向依赖 web 时的加载顺序问题
        if pmode.at_least(prt.current_mode(user)["level"], "quiet"):
            return {"messages": [], "blocked": "quiet_mode"}
        # 连点保护:与 policy 里的 force_too_soon 同一把尺子。提前在这里返回原因,
        # 否则前端只能显示"模型不可用/被安全过滤",把"刚说过一句"误报成故障。
        last = crt.counters(user).get("last_proactive_ts")
        force_gap = float(getattr(cfg, "CARE_FORCE_MIN_GAP_SECONDS", 0) or 0)
        if last is not None and force_gap > 0 and \
                (time.time() - float(last)) < force_gap:
            return {"messages": [], "blocked": "force_too_soon"}
        # 只读当日叙事(不现生成):生成是调度线程的事,手动触发必须快
        life = pn.today_text(user) or {}
        msg = ccoord.evaluate(user, petlife_text=life.get("text"), force=True)
    else:
        msg = ccoord.evaluate(user)
    if msg:
        sessions.deliver_proactive(msg)   # 与调度线程行为一致:同样落进会话历史
    return {"messages": [msg] if msg else []}


@router.get("/api/care/pending")
def care_pending(x_user_id: Optional[str] = _USER_HEADER):
    """拉取待读的主动关怀消息(前端展示后可逐条确认)。"""
    return {"messages": crt.pending(sessions.resolve_user(x_user_id))}


@router.post("/api/care/pending/{msg_id}/dismiss")
def care_dismiss(msg_id: str):
    """确认已读并移除一条主动消息。"""
    if not crt.dismiss(msg_id):
        raise HTTPException(status_code=404, detail="主动消息不存在或已读")
    return {"ok": True, "id": msg_id}


# ============================ 桌宠 ============================
@router.get("/api/pet/settings")
def pet_settings(x_user_id: Optional[str] = _USER_HEADER):
    """桌宠设置(每日生活叙事生成开关)。"""
    return prt.status(sessions.resolve_user(x_user_id))


# ---------- 打扰等级 ----------
class PetModeRequest(BaseModel):
    """部分更新:未提供的字段一律不动。"""

    default_level: Optional[str] = Field(
        default=None,
        description="用户默认档:active / quiet / dnd(不含 asleep —— 休眠只由夜间规则进入)",
    )
    night_sleep: Optional[bool] = Field(
        default=None, description="夜间是否自动进入休眠(默认开)")
    manual_level: Optional[str] = Field(
        default=None,
        description="临时覆盖档位;显式传空串 \"\" 表示**清除**覆盖(回到 自动+默认)",
    )
    manual_minutes: Optional[int] = Field(
        default=None, description="临时覆盖时长(分钟);不传 = 长期有效直到被改")


@router.get("/api/pet/mode")
def pet_mode():
    """当前打扰等级(含来源与到期)与可选档位。

    与 ``/api/pet/settings`` 分开的理由:那个是**长期设置**,这个是**此刻的状态**。
    前端靠它把降级显示出来 —— 静默降级会被误解成「桌宠坏了」(PLAN_NEXT.md §2.1)。
    """
    return prt.mode_status(sessions.default_user())


@router.patch("/api/pet/mode")
def pet_set_mode(req: PetModeRequest):
    """改默认档 / 夜间开关 / 临时覆盖。

    「临时覆盖」与「默认档」是两个语义(PLAN_NEXT.md §2.4):前者到期自动失效,
    后者是长期意愿。用 ``manual_level=""`` 显式清除覆盖;``None``(不传)= 不动。
    """
    user = sessions.default_user()
    if req.default_level is not None:
        prt.set_level(user, req.default_level)
    if req.night_sleep is not None:
        prt.set_night_sleep(user, req.night_sleep)
    if req.manual_level is not None:
        if not str(req.manual_level).strip():
            prt.clear_manual(user)
        else:
            until = None
            if req.manual_minutes:
                until = time.time() + float(req.manual_minutes) * 60.0
            prt.set_manual(user, req.manual_level, until)
    return prt.mode_status(user)


@router.get("/api/pet/daily")
def pet_daily_today(x_user_id: Optional[str] = _USER_HEADER):
    """今日桌宠生活叙事(可能尚未生成:返回 text=None;生成交给调度线程)。"""
    life = pn.today_text(sessions.resolve_user(x_user_id))
    return life or {"day": None, "text": None, "source": None}


@router.post("/api/pet/daily/regenerate")
def pet_daily_regenerate(x_user_id: Optional[str] = _USER_HEADER):
    """显式刷新今日叙事:丢弃当日条目重新生成。

    默认不限次数(``PET_DAILY_MAX_REGENERATES_PER_DAY`` <= 0);显式设成正数才节流。
    停用生成开关 → 409;配置了上限且到顶 → 429(原文本不会被覆盖)。
    LLM 失败同自动生成口径:落 unavailable 条目,前端显示「生成失败」可再点。
    """
    user = sessions.resolve_user(x_user_id)
    out = pn.regenerate_today(user)
    if out.get("disabled"):
        raise HTTPException(status_code=409, detail="每日叙事生成已停用,请先在下方开关中打开")
    if out.get("capped"):
        raise HTTPException(status_code=429, detail="今日刷新次数已达上限,明天再来")
    return out.get("item") or {"day": None, "text": None, "source": None}


@router.patch("/api/pet/daily")
def pet_daily_update(req: PetDailyUpdateRequest, x_user_id: Optional[str] = _USER_HEADER):
    """用户直接编辑今日叙事文本(source → user_edited;不调 LLM、不占刷新额度)。

    当日还没有条目时返回 404 —— 先刷新一次或等调度线程生成。
    """
    user = sessions.resolve_user(x_user_id)
    item = pn.update_today(user, req.text)
    if item is None:
        raise HTTPException(status_code=404, detail="今日还没有可编辑的桌宠记忆")
    return item


def _cards_payload() -> dict:
    """角色卡列表 + 当前启用项(列表与增删改后的回包共用同一形状)。

    **连人格文本一起返回**(description / greeting),界面要能直接编辑这两项。
    """
    active = cloader.active_card_name()
    cards = []
    for n in cloader.list_cards():
        item: dict = {"name": n, "active": n == active, "description": "", "greeting": ""}
        try:
            card = cloader.load_card(n, strict=True)
        except cloader.CardError as e:
            # 坏卡不该让整个列表 500 —— 标出来,让用户能在界面上看到并删掉它
            item["error"] = str(e)
        else:
            item["description"] = card.data.description or ""
            item["greeting"] = card.data.first_mes or ""
        cards.append(item)
    return {"cards": cards, "active": active or None}


@router.get("/api/pet/cards")
def pet_cards():
    """可用角色卡列表(含提示词正文与开场白)+ 当前启用的那张。"""
    return _cards_payload()


class PetCardRequest(BaseModel):
    """新建一张卡,三个字段一一对应界面上的三个输入框。"""

    name: str = Field(description="角色名(必填;同时是这张卡的标识与文件名)")
    description: str = Field(description="提示词正文(必填;进提示词的那部分人格描述)")
    first_mes: str = Field(default="", description="开场白(可空)")


class PetCardUpdateRequest(BaseModel):
    """改一张已有卡:未提供的字段保持原样(局部更新)。"""

    description: Optional[str] = Field(default=None, description="新的提示词正文")
    first_mes: Optional[str] = Field(default=None, description="新的开场白")


class PetActiveCardRequest(BaseModel):
    name: str = Field(description="要启用的卡名;空串 = 不搭载任何角色卡")


@router.post("/api/pet/cards", status_code=201)
def pet_create_card(req: PetCardRequest):
    """新建角色卡。重名报 400,不覆盖(覆盖走编辑)。"""
    try:
        cloader.create_card(
            name=req.name, description=req.description, first_mes=req.first_mes
        )
    except cloader.CardError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _cards_payload()


@router.put("/api/pet/cards/{name}")
def pet_update_card(name: str, req: PetCardUpdateRequest):
    """改一张已有卡的提示词正文 / 开场白。"""
    try:
        cloader.update_card(
            name=name, description=req.description, first_mes=req.first_mes
        )
    except cloader.CardError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _cards_payload()


@router.delete("/api/pet/cards/{name}")
def pet_delete_card(name: str):
    """删掉一张卡。删的是当前启用的那张时,启用选择一并清空(不留悬空引用)。"""
    try:
        cloader.delete_card(name)
    except cloader.CardError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _cards_payload()


@router.post("/api/pet/cards/active")
def pet_set_active_card(req: PetActiveCardRequest):
    """启用某张卡(传空串 = 不搭载任何角色卡)。

    选择落在用户数据目录(见 loader.set_active_card),跨重启保留;
    选一张不存在的卡直接报 400。
    """
    try:
        cloader.set_active_card(req.name)
    except cloader.CardError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _cards_payload()


@router.get("/api/pet/greeting")
def pet_greeting():
    """角色卡开场白(first_mes)。未搭载角色卡时 text=None。

    开场白是**通用能力**而非桌宠专有:网页端与桌宠端共用同一句,
    文案的唯一来源是角色卡(见 pet/character/loader.py::greeting)。
    """
    text = cloader.greeting()
    return {"text": text, "source": "card" if text else None}
