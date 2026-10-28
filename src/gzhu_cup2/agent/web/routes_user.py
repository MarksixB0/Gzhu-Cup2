# -*- coding: utf-8 -*-
"""用户自述资料接口:读快照 + 首启引导动作。

写那三项内容**不在这里** —— 它走统一设置面 ``PATCH /api/settings``
(``profile.display_name`` / ``profile.likes`` / ``profile.dislikes``)。
设置项只留一个写入口,才不会出现"设置页改了、另一处还留一份"的双源真相
(见 ``routes_settings.py`` 的白名单)。

本文件只补设置面表达不了的两件事::

    GET  /api/user/profile              资料快照 + 首启引导状态
    POST /api/user/profile/onboarding   记录 已展示 / 完成 / 跳过

**为什么首启引导要有服务端状态**:前端只存内存的话,重装/换机器/清数据都能重来,
但"每次启动都问一遍"也会跟着发生。次数上限与"跳过后永不重复"必须落在盘上
(见 ``core/user_profile.py`` 的 ``MAX_PROMPT_TIMES``)。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gzhu_cup2.agent.core import user_profile as uprofile

router = APIRouter()


@router.get("/api/user/profile")
def get_user_profile():
    """称呼 / 喜欢 / 讨厌 + 首启引导状态(``onboarding.needed`` 决定要不要问)。"""
    return uprofile.view()


class OnboardingAction(BaseModel):
    """首启引导的三种进展。"""

    action: str = Field(description="seen(已展示一问) / complete(答完) / skip(跳过)")


@router.post("/api/user/profile/onboarding")
def post_onboarding(req: OnboardingAction):
    """记录首启引导进展;``skip`` 之后不再主动询问(用户仍可在设置里改)。"""
    try:
        state = uprofile.mark_onboarding(req.action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "onboarding": state}
