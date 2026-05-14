from __future__ import annotations

from app.core.gemini_keys import GeminiAllKeysCooldownError
from app.models.job_models import JobStatus
from app.services.job_service import update_job_state
from app.services.progress_service import update_progress, update_result


COOLDOWN_MESSAGE = "Tất cả Gemini API key đang tạm cooldown. Có thể thử lại sau khoảng 300 giây."


def is_all_keys_cooldown_error(exc: Exception) -> bool:
    if isinstance(exc, GeminiAllKeysCooldownError):
        return True
    text = str(exc or "").lower()
    return "all gemini api keys are in cooldown" in text


def next_available_from_error(exc: Exception) -> str | None:
    if isinstance(exc, GeminiAllKeysCooldownError):
        return exc.next_available_at
    text = str(exc or "")
    marker = "Next available time:"
    if marker not in text:
        return None
    return text.split(marker, 1)[1].strip().rstrip(".") or None


def cooldown_seconds_from_error(exc: Exception) -> int:
    if isinstance(exc, GeminiAllKeysCooldownError):
        return exc.cooldown_seconds
    return 300


def mark_waiting_for_gemini_cooldown(
    job_id: str,
    *,
    retry_stage: str,
    percent: int,
    exc: Exception,
) -> None:
    next_available_at = next_available_from_error(exc)
    cooldown_seconds = cooldown_seconds_from_error(exc)
    message = f"Tất cả Gemini API key đang tạm cooldown. Có thể thử lại sau khoảng {cooldown_seconds} giây."
    update_job_state(
        job_id,
        status=JobStatus.waiting_gemini_cooldown,
        stage=retry_stage,
        error=message,
    )
    update_progress(
        job_id,
        status=JobStatus.waiting_gemini_cooldown,
        stage="waiting_gemini_key",
        message=message,
        percent=percent,
        next_available_at=next_available_at,
        recoverable=True,
        retry_stage=retry_stage,
        cooldown_seconds=cooldown_seconds,
    )
    update_result(
        job_id,
        ok=False,
        status=JobStatus.waiting_gemini_cooldown,
        message=message,
        data={
            "recoverable": True,
            "retry_stage": retry_stage,
            "next_available_at": next_available_at,
            "cooldown_seconds": cooldown_seconds,
        },
        error=message,
    )
