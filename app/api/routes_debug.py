from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.core.gemini_keys import GeminiKeyError, GeminiKeyManager

router = APIRouter(prefix="/api/debug", tags=["debug"])


class MarkCooldownRequest(BaseModel):
    error: str = "manual test"
    cooldown_seconds: int | None = None


class MarkDeadRequest(BaseModel):
    error: str = "manual test"


def _manager() -> GeminiKeyManager:
    return GeminiKeyManager.from_env()


def _debug_response(manager: GeminiKeyManager) -> dict:
    snapshot = manager.safe_snapshot()
    return {
        "ok": True,
        "key_count": snapshot["key_count"],
        "total_keys": snapshot["total_keys"],
        "current_index": snapshot["current_index"],
        "usable_count": snapshot["usable_count"],
        "cooldown_count": snapshot["cooldown_count"],
        "dead_count": snapshot["dead_count"],
        "next_available_at": snapshot["next_available_at"],
        "default_cooldown_seconds": snapshot["default_cooldown_seconds"],
        "max_wait_seconds": snapshot["max_wait_seconds"],
        "model": get_settings().gemini_model,
        "state_path": snapshot["state_path"],
        "keys": snapshot["keys"],
        "last_errors": snapshot["last_errors"],
    }


def _http_error(error: GeminiKeyError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(error),
    )


@router.get("/gemini-keys")
def get_gemini_keys():
    return _debug_response(_manager())


@router.post("/gemini-keys/rotate")
def rotate_gemini_key():
    manager = _manager()
    try:
        rotated = manager.rotate_next()
    except GeminiKeyError as exc:
        raise _http_error(exc) from exc
    response = _debug_response(manager)
    response["rotated_to_index"] = rotated["index"]
    return response


@router.post("/gemini-keys/mark-current-cooldown")
def mark_current_cooldown(payload: MarkCooldownRequest | None = None):
    manager = _manager()
    payload = payload or MarkCooldownRequest()
    try:
        current_index = manager.get_current_index()
        manager.mark_cooldown(
            current_index,
            payload.error,
            cooldown_seconds=payload.cooldown_seconds,
        )
    except GeminiKeyError as exc:
        raise _http_error(exc) from exc
    response = _debug_response(manager)
    response["marked_index"] = current_index
    return response


@router.post("/gemini-keys/mark-current-dead")
def mark_current_dead(payload: MarkDeadRequest | None = None):
    manager = _manager()
    payload = payload or MarkDeadRequest()
    try:
        current_index = manager.get_current_index()
        manager.mark_dead(current_index, payload.error)
    except GeminiKeyError as exc:
        raise _http_error(exc) from exc
    response = _debug_response(manager)
    response["marked_index"] = current_index
    return response


@router.post("/gemini-keys/clear-state")
def clear_gemini_key_state():
    manager = _manager()
    manager.clear_runtime_state()
    return _debug_response(manager)
