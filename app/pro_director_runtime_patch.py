from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from app import pro_director_service

_original_base_context = pro_director_service._base_context


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def safe_base_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Return the AI director context as plain JSON-compatible data."""
    context = _original_base_context(*args, **kwargs)
    return json.loads(json.dumps(context, ensure_ascii=False, default=_json_default))


pro_director_service._base_context = safe_base_context
