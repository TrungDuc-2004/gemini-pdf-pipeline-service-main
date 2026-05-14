import os
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.core.paths import gemini_rotation_state_path
from app.utils.files import ensure_dir, read_json, write_json
from app.utils.time import parse_iso_datetime, utc_now_iso


class GeminiKeyError(RuntimeError):
    pass


class AllGeminiKeysCoolingDown(GeminiKeyError):
    def __init__(
        self,
        next_available_at: str | None,
        *,
        cooldown_seconds: int,
        usable_count: int,
        cooldown_count: int,
        dead_count: int,
    ) -> None:
        self.next_available_at = next_available_at
        self.cooldown_seconds = cooldown_seconds
        self.usable_count = usable_count
        self.cooldown_count = cooldown_count
        self.dead_count = dead_count
        super().__init__(
            f"All Gemini API keys are in cooldown. Next available time: {next_available_at}."
        )


GeminiAllKeysCooldownError = AllGeminiKeysCoolingDown


@dataclass(frozen=True)
class ErrorClassification:
    type: str
    reason: str
    should_rotate: bool
    cooldown_seconds: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "reason": self.reason,
            "should_rotate": self.should_rotate,
            "cooldown_seconds": self.cooldown_seconds,
        }


class GeminiKeyManager:
    def __init__(
        self,
        keys: list[str],
        state_path: Path,
        cooldown_seconds: int = 300,
        max_wait_seconds: int = 300,
    ) -> None:
        self.keys = [key.strip() for key in keys if key and key.strip()]
        self.state_path = state_path
        self.cooldown_seconds = cooldown_seconds
        self.max_wait_seconds = max_wait_seconds
        self.state = self.load_state()
        self._normalize_current_index()

    @classmethod
    def from_env(cls) -> "GeminiKeyManager":
        keys: list[str] = []
        combined = os.getenv("GEMINI_API_KEYS", "")
        if combined.strip():
            keys.extend(part.strip() for part in combined.split(",") if part.strip())

        numbered_keys: list[tuple[int, str]] = []
        for name, value in os.environ.items():
            match = re.fullmatch(r"GEMINI_API_KEY_(\d+)", name)
            if match and value.strip():
                numbered_keys.append((int(match.group(1)), value.strip()))
        keys.extend(value for _, value in sorted(numbered_keys))

        from app.core.config import get_settings

        settings = get_settings()
        return cls(
            keys=keys,
            state_path=gemini_rotation_state_path(),
            cooldown_seconds=settings.gemini_cooldown_seconds,
            max_wait_seconds=settings.gemini_max_wait_seconds,
        )

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        try:
            state = read_json(self.state_path)
        except Exception:
            return self._default_state()
        default = self._default_state()
        default.update({key: value for key, value in state.items() if key in default})
        if not isinstance(default["cooldowns"], dict):
            default["cooldowns"] = {}
        if not isinstance(default["dead_keys"], dict):
            default["dead_keys"] = {}
        if not isinstance(default["last_errors"], dict):
            default["last_errors"] = {}
        return default

    def save_state(self) -> None:
        self.state["updated_at"] = utc_now_iso()
        ensure_dir(self.state_path.parent)
        write_json(self.state_path, self.state)

    def snapshot(self) -> dict[str, Any]:
        return {
            "key_count": self.key_count(),
            "current_index": self.get_current_index(),
            "state_path": str(self.state_path),
            "state": self.state,
        }

    def safe_snapshot(self) -> dict[str, Any]:
        keys = []
        usable_count = 0
        cooldown_count = 0
        dead_count = 0
        for index in range(self.key_count()):
            index_key = str(index)
            status = "available"
            cooldown_until = self.state["cooldowns"].get(index_key)
            dead_info = self.state["dead_keys"].get(index_key)
            if dead_info:
                status = "dead"
                dead_count += 1
            elif cooldown_until and not self._cooldown_expired(cooldown_until):
                status = "cooldown"
                cooldown_count += 1
            else:
                usable_count += 1
            remaining_seconds = self._remaining_cooldown_seconds(cooldown_until) if status == "cooldown" else None
            keys.append(
                {
                    "index": index,
                    "status": status,
                    "cooldown_until": cooldown_until if status == "cooldown" else None,
                    "remaining_seconds": remaining_seconds,
                    "dead_reason": dead_info.get("reason") if dead_info else None,
                    "last_error": self.state["last_errors"].get(index_key),
                }
            )
        return {
            "key_count": self.key_count(),
            "total_keys": self.key_count(),
            "current_index": self.get_current_index(),
            "usable_count": usable_count,
            "cooldown_count": cooldown_count,
            "dead_count": dead_count,
            "next_available_at": self._next_available_time(),
            "default_cooldown_seconds": self.cooldown_seconds,
            "max_wait_seconds": self.max_wait_seconds,
            "state_path": str(self.state_path),
            "keys": keys,
            "last_errors": self.state.get("last_errors", {}),
            "updated_at": self.state.get("updated_at"),
        }

    def key_count(self) -> int:
        return len(self.keys)

    def get_current_index(self) -> int:
        if self.key_count() == 0:
            return 0
        return int(self.state.get("current_index", 0)) % self.key_count()

    def get_current_key(self) -> str:
        if self.key_count() == 0:
            raise GeminiKeyError("No Gemini API keys configured.")
        index = self.get_current_index()
        if not self._is_available(index):
            return self.get_available_key()["key"]
        return self.keys[index]

    def get_available_key(self) -> dict[str, Any]:
        if self.key_count() == 0:
            raise GeminiKeyError("No Gemini API keys configured.")

        current = self.get_current_index()
        for offset in range(self.key_count()):
            index = (current + offset) % self.key_count()
            if self._is_available(index):
                self.state["current_index"] = index
                self.save_state()
                return {"index": index, "key": self.keys[index]}

        if self._all_dead():
            raise GeminiKeyError("All Gemini API keys are marked dead.")

        raise self._all_cooling_down_error()

    def rotate_next(self) -> dict[str, Any]:
        if self.key_count() == 0:
            raise GeminiKeyError("No Gemini API keys configured.")

        start = (self.get_current_index() + 1) % self.key_count()
        for offset in range(self.key_count()):
            index = (start + offset) % self.key_count()
            if self._is_available(index):
                self.state["current_index"] = index
                self.save_state()
                return {"index": index}

        if self._all_dead():
            raise GeminiKeyError("All Gemini API keys are marked dead.")

        raise self._all_cooling_down_error()

    def mark_cooldown(
        self,
        index: int,
        error: str,
        cooldown_seconds: int | None = None,
    ) -> None:
        self._validate_index(index)
        seconds = cooldown_seconds if cooldown_seconds is not None else self.cooldown_seconds
        until = parse_iso_datetime(utc_now_iso()) + timedelta(seconds=seconds)
        self.state["cooldowns"][str(index)] = until.isoformat()
        self.mark_error(index, error, save=False)
        self._move_current_if_needed(index)
        self.save_state()

    def mark_dead(self, index: int, error: str) -> None:
        self._validate_index(index)
        self.state["cooldowns"].pop(str(index), None)
        self.state["dead_keys"][str(index)] = {
            "reason": self._safe_error(error),
            "marked_at": utc_now_iso(),
        }
        self.mark_error(index, error, save=False)
        self._move_current_if_needed(index)
        self.save_state()

    def mark_error(self, index: int, error: str, save: bool = True) -> None:
        self._validate_index(index)
        self.state["last_errors"][str(index)] = {
            "error": self._safe_error(error),
            "at": utc_now_iso(),
        }
        if save:
            self.save_state()

    def clear_runtime_state(self) -> None:
        self.state["current_index"] = 0
        self.state["cooldowns"] = {}
        self.state["dead_keys"] = {}
        self.state["last_errors"] = {}
        self.save_state()

    def classify_error(self, error: Any) -> dict[str, Any]:
        text = str(error or "")
        lowered = text.lower()

        dead_markers = [
            "api_key_invalid",
            "api key expired",
            "api key not valid",
            "invalid api key",
            "key expired",
            "consumer has been suspended",
            "consumer_suspended",
            "reported as leaked",
            "api key was reported as leaked",
            "leaked",
            "suspended",
        ]
        if any(marker in lowered for marker in dead_markers) or (
            "permission denied" in lowered and ("leaked" in lowered or "suspended" in lowered)
        ):
            return ErrorClassification(
                type="dead",
                reason="Gemini API key is invalid, expired, or suspended.",
                should_rotate=True,
            ).as_dict()

        cooldown_markers = [
            "429",
            "quota",
            "rate limit",
            "rate-limit",
            "retrydelay",
            "retry delay",
            "resource_exhausted",
            "500",
            "502",
            "503",
            "unavailable",
            "overload",
            "timeout",
            "deadline exceeded",
            "deadline_exceeded",
            "internal server error",
            "bad gateway",
        ]
        if any(marker in lowered for marker in cooldown_markers):
            retry_delay_seconds = self._parse_retry_delay_seconds(text)
            return ErrorClassification(
                type="cooldown",
                reason="Transient Gemini quota, rate-limit, or server error.",
                should_rotate=True,
                cooldown_seconds=max(
                    self.cooldown_seconds,
                    retry_delay_seconds or 0,
                ),
            ).as_dict()

        non_rotatable_markers = [
            "invalid json from model",
            "bad prompt",
            "schema issue",
            "local file error",
            "pdf not found",
            "missing file",
            "validation error",
        ]
        if any(marker in lowered for marker in non_rotatable_markers):
            return ErrorClassification(
                type="non_rotatable",
                reason="Local input, validation, prompt, or model response issue.",
                should_rotate=False,
            ).as_dict()

        return ErrorClassification(
            type="non_rotatable",
            reason="Unclassified error; treating as non-rotatable.",
            should_rotate=False,
        ).as_dict()

    def _default_state(self) -> dict[str, Any]:
        now = utc_now_iso()
        return {
            "current_index": 0,
            "cooldowns": {},
            "dead_keys": {},
            "last_errors": {},
            "updated_at": now,
        }

    def _normalize_current_index(self) -> None:
        if self.key_count() == 0:
            self.state["current_index"] = 0
            return
        try:
            current = int(self.state.get("current_index", 0))
        except (TypeError, ValueError):
            current = 0
        self.state["current_index"] = current % self.key_count()

    def _validate_index(self, index: int) -> None:
        if index < 0 or index >= self.key_count():
            raise GeminiKeyError(f"Gemini key index out of range: {index}.")

    def _is_available(self, index: int) -> bool:
        index_key = str(index)
        if index_key in self.state["dead_keys"]:
            return False
        cooldown_until = self.state["cooldowns"].get(index_key)
        if cooldown_until and not self._cooldown_expired(cooldown_until):
            return False
        return True

    def _cooldown_expired(self, cooldown_until: str) -> bool:
        try:
            return parse_iso_datetime(cooldown_until) <= parse_iso_datetime(utc_now_iso())
        except ValueError:
            return True

    def _all_dead(self) -> bool:
        return self.key_count() > 0 and all(
            str(index) in self.state["dead_keys"] for index in range(self.key_count())
        )

    def _next_available_time(self) -> str | None:
        times: list[str] = []
        for index in range(self.key_count()):
            if str(index) in self.state["dead_keys"]:
                continue
            cooldown_until = self.state["cooldowns"].get(str(index))
            if cooldown_until and not self._cooldown_expired(cooldown_until):
                times.append(cooldown_until)
        if not times:
            return None
        return min(times)

    def _remaining_cooldown_seconds(self, cooldown_until: str | None) -> int | None:
        if not cooldown_until:
            return None
        try:
            remaining = parse_iso_datetime(cooldown_until) - parse_iso_datetime(utc_now_iso())
        except ValueError:
            return None
        return max(0, int(remaining.total_seconds()))

    def _all_cooling_down_error(self) -> AllGeminiKeysCoolingDown:
        snapshot = self.safe_snapshot()
        return AllGeminiKeysCoolingDown(
            snapshot["next_available_at"],
            cooldown_seconds=self.cooldown_seconds,
            usable_count=snapshot["usable_count"],
            cooldown_count=snapshot["cooldown_count"],
            dead_count=snapshot["dead_count"],
        )

    def _move_current_if_needed(self, changed_index: int) -> None:
        if self.key_count() == 0 or self.get_current_index() != changed_index:
            return
        for offset in range(1, self.key_count() + 1):
            next_index = (changed_index + offset) % self.key_count()
            if self._is_available(next_index):
                self.state["current_index"] = next_index
                return

    def _safe_error(self, error: str) -> str:
        text = str(error or "")
        for key in self.keys:
            if key:
                text = text.replace(key, "[REDACTED_KEY]")
        return text

    def _parse_retry_delay_seconds(self, text: str) -> int | None:
        patterns = [
            r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s",
            r"retry_delay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s",
            r"seconds['\"]?\s*[:=]\s*(\d+)",
            r"retry[-_ ]?after['\"]?\s*[:=]\s*['\"]?(\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            try:
                return max(0, int(float(match.group(1))))
            except (TypeError, ValueError):
                return None
        return None
