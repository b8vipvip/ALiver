from fastapi import APIRouter

from app import __version__

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ALiver", "version": __version__}
