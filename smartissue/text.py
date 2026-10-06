from __future__ import annotations

import re


def redact_sensitive_text(value: str) -> str:
    value = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[redacted email]", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:\d[ -]?){8,19}\b", "[redacted number]", value)
    return re.sub(r"\b(?:\+?\d[\d(). -]{6,}\d)\b", "[redacted phone]", value)


def clean_text(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", redact_sensitive_text(value)).strip()[:limit]
