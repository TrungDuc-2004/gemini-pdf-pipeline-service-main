from __future__ import annotations

import logging
from typing import Callable

from app.core.gemini_keys import GeminiAllKeysCooldownError, GeminiKeyError, GeminiKeyManager

_log = logging.getLogger(__name__)


class GeminiPdfClient:
    def __init__(self, key_manager: GeminiKeyManager) -> None:
        self.key_manager = key_manager

    def generate_with_pdf(
        self,
        *,
        pdf_path: str,
        prompt: str,
        model: str,
        status_cb: Callable[[str], None] | None = None,
    ) -> str:
        last_error: Exception | None = None
        last_classification: dict | None = None
        attempts = max(1, self.key_manager.key_count())

        for _ in range(attempts):
            selected = self.key_manager.get_available_key()
            index = selected["index"]
            api_key = selected["key"]
            if status_cb:
                status_cb(f"Using Gemini key index {index}")

            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=api_key)
                uploaded = client.files.upload(file=pdf_path)
                config = types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                )
                response = client.models.generate_content(
                    model=model,
                    contents=[prompt, uploaded],
                    config=config,
                )
                return (response.text or "").strip()
            except Exception as exc:
                last_error = exc
                classification = self.key_manager.classify_error(exc)
                last_classification = classification
                safe_reason = classification["reason"]
                _log.warning(
                    "Gemini request failed for key index %s: %s",
                    index,
                    safe_reason,
                )
                if status_cb:
                    status_cb(f"Gemini key index {index} failed: {safe_reason}")

                if classification["type"] == "dead":
                    self.key_manager.mark_dead(index, str(exc))
                elif classification["type"] == "cooldown":
                    self.key_manager.mark_cooldown(
                        index,
                        str(exc),
                        cooldown_seconds=classification.get("cooldown_seconds"),
                    )
                else:
                    self.key_manager.mark_error(index, str(exc))
                    raise RuntimeError(safe_reason) from exc

        if last_error is not None and last_classification and last_classification.get("type") == "cooldown":
            try:
                self.key_manager.get_available_key()
            except GeminiAllKeysCooldownError:
                raise
            except GeminiKeyError:
                pass
        if last_error is not None:
            raise RuntimeError("Gemini request failed after rotating available keys.") from last_error
        raise GeminiKeyError("No Gemini API keys configured.")
