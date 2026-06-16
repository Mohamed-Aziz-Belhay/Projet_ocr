"""
app/utils/rtl_text.py
Helpers for Arabic / RTL OCR cleanup and candidate validation.
"""
from __future__ import annotations
import re
from typing import Iterable, List, Optional

ARABIC_RE = re.compile(r"ýÿ-Û¿]")
ARABIC_TOKEN_RE = re.compile(r"[Øýÿ¿]{2,}")
BIDI_RE = re.compile(r"[âââª-â®â¦-â©]")
DIGIT_TRANSLATION = str.maketrans("Ù Ù¡Ù¢Ù£Ù¤Ù¥Ù¦Ù§Ù¨Ù©", "0123456789")

COMMON_LABEL_TOKENS = {
    ýÿýÿýÿýÿýÿýÿýÿýÿýÿ©", ýÿýÿýÿýÿýÿýÿýÿýÿ©", ýÿýÿýÿýÿýÿ©", ýÿýÿýÿýÿýÿýÿýÿ", ýÿýÿýÿýÿýÿýÿýÿ©",
    ýÿýÿýÿýÿýÿ¨", ýÿýÿýÿýÿýÿ", ýÿýÿýÿýÿ", ýÿýÿýÿýÿýÿ", ýÿýÿýÿýÿýÿýÿýÿ©", ýÿýÿýÿýÿýÿ®", ýÿýÿýÿýÿýÿýÿ§", ýÿýÿýÿýÿ", ýÿýÿýÿýÿýÿýÿýÿ©",
    ýÿýÿýÿýÿýÿýÿýÿ§", ýÿýÿýÿýÿýÿýÿ©", ýÿýÿýÿýÿýÿ©", ýÿýÿýÿýÿýÿ",
}
WEAK_PLACE_TOKENS = {"ÙØ§", "ÙØ¹Ù", "ÙÙØ§", "ÙÙØ§Ù"}


def strip_bidi_controls(text: str) -> str:
    return BIDI_RE.sub("", text or "")


def normalize_arabic_digits(text: str) -> str:
    return (text or "").translate(DIGIT_TRANSLATION)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def cleanup_arabic_text(text: str) -> str:
    text = strip_bidi_controls(text)
    text = normalize_arabic_digits(text)
    text = re.sub(r"[^\u0600-\u06FF0-9\s:/\\.\-]", " ", text)
    return normalize_spaces(text)


def contains_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text or ""))


def arabic_tokens(text: str) -> List[str]:
    return ARABIC_TOKEN_RE.findall(cleanup_arabic_text(text))


def filter_label_tokens(tokens: Iterable[str], forbidden: Optional[Iterable[str]] = None) -> List[str]:
    banned = set(COMMON_LABEL_TOKENS)
    if forbidden:
        banned.update(forbidden)
    return [t for t in tokens if t not in banned]


def plausible_arabic_phrase(text: str, *, max_tokens: int = 4, forbidden: Optional[Iterable[str]] = None) -> Optional[str]:
    toks = filter_label_tokens(arabic_tokens(text), forbidden)
    if not toks:
        return None
    if len(toks) > max_tokens:
        toks = toks[:max_tokens]
    phrase = " ".join(toks).strip()
    if len(phrase) < 2:
        return None
    return phrase


def score_arabic_phrase_quality(text: str) -> float:
    text = cleanup_arabic_text(text)
    if not text:
        return 0.0
    toks = arabic_tokens(text)
    if not toks:
        return 0.0
    penalty = 0.0
    if any(tok in COMMON_LABEL_TOKENS for tok in toks):
        penalty += 0.35
    if re.search(r"\d", text):
        penalty += 0.25
    if len(toks) > 3:
        penalty += 0.25
    if len(set(toks)) < len(toks):
        penalty += 0.10
    score = 0.9 - penalty
    return max(0.0, min(1.0, score))


def has_forbidden_label_token(text: str, forbidden: Optional[Iterable[str]] = None) -> bool:
    toks = set(arabic_tokens(text))
    banned = set(COMMON_LABEL_TOKENS)
    if forbidden:
        banned.update(forbidden)
    return bool(toks & banned)


def is_probable_name_value(text: str, forbidden: Optional[Iterable[str]] = None) -> bool:
    cleaned = cleanup_arabic_text(text)
    toks = filter_label_tokens(arabic_tokens(cleaned), forbidden)
    if not toks:
        return False
    if len(toks) > 2:
        return False
    if any(tok in WEAK_PLACE_TOKENS for tok in toks):
        return False
    if any(len(tok) < 2 for tok in toks):
        return False
    if re.search(r"\d", cleaned):
        return False
    return True


def is_probable_place_value(text: str, forbidden: Optional[Iterable[str]] = None) -> bool:
    cleaned = cleanup_arabic_text(text)
    toks = filter_label_tokens(arabic_tokens(cleaned), forbidden)
    if not toks:
        return False
    if len(toks) > 3:
        return False
    if any(tok in WEAK_PLACE_TOKENS for tok in toks):
        return False
    if re.search(r"\d", cleaned):
        return False
    return True
