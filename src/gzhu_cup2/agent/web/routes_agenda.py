# -*- coding: utf-8 -*-
"""日程路由:/api/agenda/*。

本模块是「web 层唯一认识 agenda 的地方」;校验与存储都在 ``agenda/store.py``。
日程不进检索、不参与遗忘,但会随「清空记忆」一并删除(见 routes_admin.memory_wipe)。
"""
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from gzhu_cup2.agent.agenda import store as astore
from gzhu_cup2.agent.web import sessions

router = APIRouter()

# 用户标识请求头(与 /api/chat 同一口径,见 web/sessions.resolve_user)
_USER_HEADER = Header(
    default=None, alias="X-User-Id",
    description="日程归属的用户键(可选;非法值回落默认用户)")


class AgendaEventRequest(BaseModel):
    kind: str = Field(default="todo", description="todo=待办;anni=纪念日")
    title: str = Field(description="内容,最长 40 字")
    date: str = Field(description="YYYY-MM-DD;纪念日只用「月-日」跨年命中")
    time: Optional[str] = Field(
        default=None, description="HH:MM;**留空 = 当天待办,不提醒**")
    yearly: Optional[bool] = Field(
        default=None, description="是否每年重复;缺省按 kind 推断(纪念日=true)")


class AgendaEventPatch(BaseModel):
    """部分更新:未提供的字段一律不动。"""

    kind: Optional[str] = None
    title: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    yearly: Optional[bool] = None


@router.get("/api/agenda/events")
def agenda_list(x_user_id: Optional[str] = _USER_HEADER):
    """该用户全部日程(按日期、时刻排序;事件量小,不分页)。"""
    return {"events": astore.list_events(sessions.resolve_user(x_user_id))}


@router.post("/api/agenda/events")
def agenda_create(req: AgendaEventRequest, x_user_id: Optional[str] = _USER_HEADER):
    """新建一条日程。"""
    try:
        ev = astore.create_event(sessions.resolve_user(x_user_id), req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"event": ev}


@router.patch("/api/agenda/events/{event_id}")
def agenda_update(event_id: str, req: AgendaEventPatch,
                  x_user_id: Optional[str] = _USER_HEADER):
    """改一条日程;改了日期或时刻会重置「已提醒」,让它重新提醒。"""
    patch = req.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="没有要改的字段")
    try:
        ev = astore.update_event(sessions.resolve_user(x_user_id), event_id, patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    if ev is None:
        raise HTTPException(status_code=404, detail="日程不存在")
    return {"event": ev}


@router.delete("/api/agenda/events/{event_id}")
def agenda_delete(event_id: str, x_user_id: Optional[str] = _USER_HEADER):
    """删一条日程。"""
    if not astore.delete_event(sessions.resolve_user(x_user_id), event_id):
        raise HTTPException(status_code=404, detail="日程不存在")
    return {"ok": True, "removed": 1}
