"""
app/pipeline/lang_detect.py
Lightweight language detection from OCR text.
Falls back to hint if detection is uncertain.
"""
from __future__ import annotations
import re
from typing import Optional


_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]+")
_LATIN_RE = re.compile(r"[a-zA-ZÀ-ÿ]{3,}")


def detect_language(text: str, hint: Optional[str] = None) -> str:
    """
    Returns BCP-47 code. Uses character frequency heuristics.
    Tries langdetect for longer texts (if installed).
    """
    if not text or len(text.strip()) < 10:
        return hint or "en"

    arabic_chars = len(_ARABIC_RE.findall(text))
    latin_chars = len(_LATIN_RE.findall(text))

    if arabic_chars > latin_chars:
        if hint and hint.startswith("fr"):
            return "ar+fr"
        return "ar"

    # Try langdetect for Latin text
    try:
        from langdetect import detect, LangDetectException  # type: ignore
        lang = detect(text)
        return lang
    except Exception:
        pass

    return hint or "en"
