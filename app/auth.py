from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_admin_token(x_aliver_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if expected and x_aliver_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-ALiver-Token",
        )
