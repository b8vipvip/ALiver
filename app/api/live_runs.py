from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_admin_token
from app.db import get_db
from app.live_run_service import live_run_recorder, remove_run

router = APIRouter(
    prefix="/api/live-runs",
    tags=["live-runs"],
    dependencies=[Depends(require_admin_token)],
)


class LiveRunStart(BaseModel):
    title: str | None = None
    include_audience_text: bool = True


class LiveRunSettings(BaseModel):
    auto_start_on_director: bool = True
    include_audience_text: bool = True
    sample_interval_seconds: float = Field(default=2.0, ge=1.0, le=30.0)
    metric_interval_seconds: float = Field(default=5.0, ge=2.0, le=60.0)


@router.get("/status")
def status() -> dict[str, Any]:
    return live_run_recorder.status()


@router.get("")
def list_runs(limit: int = Query(default=30, ge=1, le=200)) -> list[dict[str, Any]]:
    return live_run_recorder.list_runs(limit)


@router.put("/settings")
def save_settings(payload: LiveRunSettings) -> dict[str, Any]:
    return live_run_recorder.update_settings(payload.model_dump())


@router.post("/start")
def start_run(payload: LiveRunStart, db: Session = Depends(get_db)) -> dict[str, Any]:
    return live_run_recorder.start(
        db,
        title=payload.title,
        source="manual",
        include_audience_text=payload.include_audience_text,
    )


@router.post("/snapshot")
def snapshot(db: Session = Depends(get_db)) -> dict[str, Any]:
    return live_run_recorder.sample(db)


@router.post("/export")
def export_active(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return live_run_recorder.export(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop_run(db: Session = Depends(get_db)) -> dict[str, Any]:
    return live_run_recorder.stop(db, reason="manual")


@router.get("/{run_id}/download")
def download(run_id: str) -> FileResponse:
    path = live_run_recorder.bundle_for_run(run_id)
    if path is None:
        raise HTTPException(status_code=404, detail="直播记录不存在")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.delete("/{run_id}", status_code=204)
def delete_run(run_id: str) -> None:
    current = live_run_recorder.status()
    if current.get("active") and current.get("run_id") == run_id:
        raise HTTPException(status_code=409, detail="正在记录的直播不能删除，请先停止")
    if not remove_run(run_id):
        raise HTTPException(status_code=404, detail="直播记录不存在")
