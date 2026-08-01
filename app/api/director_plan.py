from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.browser_director_plan_service import (
    BrowserDirectorPlanError,
    generate_plan_with_bound_chatgpt,
)
from app.db import get_db
from app.director_plan_service import generate_director_plan
from app.log_service import write_log
from app.models import AutoDirectorConfig, BrowserExtension

router = APIRouter(dependencies=[Depends(require_admin_token)])


class DirectorPlanGenerateRequest(BaseModel):
    extension_id: str
    brief: str = Field(min_length=2, max_length=4000)
    duration_minutes: int = Field(default=45, ge=10, le=240)
    category: str = Field(default="chat", max_length=80)
    tone: str = Field(default="natural", max_length=80)
    generation_mode: str = Field(
        default="auto",
        pattern="^(auto|browser_chatgpt|api|local)$",
    )
    prefer_ai: bool = True
    api_base_url: str | None = Field(default=None, max_length=500)
    model_name: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=2000)
    current_settings: dict[str, Any] = Field(default_factory=dict)


class DirectorPlanGenerateResponse(BaseModel):
    source: str
    fallback_reason: str | None
    plan: dict[str, Any]
    summary: dict[str, Any]
    browser: dict[str, Any] | None = None


@router.post("/plan/generate", response_model=DirectorPlanGenerateResponse)
async def generate_plan(
    payload: DirectorPlanGenerateRequest,
    db: Session = Depends(get_db),
) -> DirectorPlanGenerateResponse:
    extension = db.get(BrowserExtension, payload.extension_id)
    if not extension:
        raise HTTPException(status_code=404, detail="Chrome extension not found")
    config = db.scalar(
        select(AutoDirectorConfig).where(AutoDirectorConfig.extension_id == payload.extension_id)
    )

    mode = payload.generation_mode
    if mode == "auto":
        mode = "api" if payload.prefer_ai else "local"

    if mode == "browser_chatgpt":
        try:
            result = await generate_plan_with_bound_chatgpt(
                db=db,
                extension=extension,
                brief=payload.brief,
                duration_minutes=payload.duration_minutes,
                category=payload.category,
                tone=payload.tone,
                current_settings=payload.current_settings,
            )
        except BrowserDirectorPlanError as exc:
            detail = str(exc)
            status_code = 504 if "超过" in detail else 409
            raise HTTPException(status_code=status_code, detail=detail) from exc
    else:
        result = await generate_director_plan(
            config=config,
            brief=payload.brief,
            duration_minutes=payload.duration_minutes,
            category=payload.category,
            tone=payload.tone,
            prefer_ai=mode == "api",
            api_base_url=payload.api_base_url,
            model_name=payload.model_name,
            api_key=payload.api_key,
            current_settings=payload.current_settings,
        )

    write_log(
        db,
        category="professional_director.plan.generated",
        message=f"Generated professional director plan with {result['source']}",
        details={
            "extension_id": payload.extension_id,
            "requested_mode": mode,
            "source": result["source"],
            "fallback_reason": result["fallback_reason"],
            "summary": result["summary"],
            "browser": result.get("browser"),
        },
    )
    return DirectorPlanGenerateResponse(**result)
