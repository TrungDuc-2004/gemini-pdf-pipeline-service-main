from __future__ import annotations

import json
import re

from app.core.gemini_keys import GeminiKeyManager
from app.pipeline.gemini_client import GeminiPdfClient


def _parse_json_loose(text: str) -> dict:
    clean = (text or "").strip()

    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        clean,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        return json.loads(fenced.group(1))

    first = clean.find("{")
    last = clean.rfind("}")
    if first != -1 and last != -1 and last > first:
        return json.loads(clean[first : last + 1])

    raise json.JSONDecodeError("No JSON object found", clean, 0)


def extract_structure_from_pdf(
    key_manager: GeminiKeyManager,
    pdf_path: str,
    prompt: str,
    model: str,
    status_cb=None,
) -> dict:
    raw = ""
    try:
        raw = GeminiPdfClient(key_manager).generate_with_pdf(
            pdf_path=pdf_path,
            prompt=prompt,
            model=model,
            status_cb=status_cb,
        )
        return _parse_json_loose(raw)
    except json.JSONDecodeError as exc:
        snippet = raw[:500] + ("..." if len(raw) > 500 else "")
        raise RuntimeError(f"Gemini returned invalid JSON. Snippet:\n{snippet}") from exc

