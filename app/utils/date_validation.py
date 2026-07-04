"""
app/utils/date_validation.py

Helpers to parse and validate dates found on Tunisian identity/administrative
documents (CIN, registre de commerce, factures...): numeric formats
(JJ/MM/AAAA, JJ-MM-AAAA, JJ.MM.AAAA, AAAA-MM-JJ) and Maghrebi Arabic textual
dates ("15 جانفي 1990"). Always returns ISO "YYYY-MM-DD" or None.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

_ARABIC_DIGITS_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# Mois maghrébins (calques du français, utilisés en Tunisie/Algérie/Maroc),
# avec quelques variantes d'orthographe et de formes inversées vues en OCR.
_MONTH_ALIASES = {
    "جانفي": 1, "جانفى": 1, "يفناج": 1,
    "فيفري": 2, "فيفرى": 2, "فبراير": 2, "يرفيف": 2,
    "مارس": 3, "سرام": 3,
    "افريل": 4, "أفريل": 4, "ابريل": 4, "أبريل": 4, "ليرفا": 4,
    "ماي": 5, "يام": 5,
    "جوان": 6, "يونيو": 6, "ناوج": 6,
    "جويلية": 7, "جويليه": 7, "يوليو": 7, "ةيليوج": 7,
    "اوت": 8, "أوت": 8, "توأ": 8,
    "سبتمبر": 9, "ربمتبس": 9,
    "اكتوبر": 10, "أكتوبر": 10, "ربوتكأ": 10, "ربوتكا": 10,
    "نوفمبر": 11, "ربمفون": 11,
    "ديسمبر": 12, "ربمسيد": 12,
}
_MONTHS_REGEX = "(?:" + "|".join(sorted(_MONTH_ALIASES, key=len, reverse=True)) + ")"

_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")
_ISO_DATE_RE = re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b")
_TEXTUAL_DATE_RE = re.compile(rf"\b(\d{{1,2}})\s+({_MONTHS_REGEX})\s+(\d{{4}})\b")

_MIN_YEAR = 1900
_MAX_YEAR = 2100


def _clean(text: str) -> str:
    text = (text or "").translate(_ARABIC_DIGITS_TRANS)
    text = text.replace("\xa0", " ").replace("|", " ").replace("،", " ").replace("؛", " ")
    return re.sub(r"\s+", " ", text).strip()


def _to_iso(year: int, month: int, day: int) -> Optional[str]:
    if not (_MIN_YEAR <= year <= _MAX_YEAR):
        return None
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def normalize_date_strict(value: str) -> Optional[str]:
    """Normalise une date numérique (JJ/MM/AAAA, JJ-MM-AAAA, JJ.MM.AAAA ou
    AAAA-MM-JJ) vers le format ISO "YYYY-MM-DD". Retourne None si la valeur
    ne contient pas de date numérique valide."""
    if not value:
        return None
    text = _clean(value)

    m = _ISO_DATE_RE.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        iso = _to_iso(y, mo, d)
        if iso:
            return iso

    m = _NUMERIC_DATE_RE.search(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _to_iso(y, mo, d)

    return None


def parse_and_normalize(text: str) -> Optional[str]:
    """Parse une date numérique ou textuelle arabe maghrébine (ex: "15 جانفي
    1990") depuis un texte OCR bruit et la normalise en "YYYY-MM-DD".
    Retourne None si aucune date valide n'est trouvée."""
    if not text:
        return None
    cleaned = _clean(text)

    numeric = normalize_date_strict(cleaned)
    if numeric:
        return numeric

    m = _TEXTUAL_DATE_RE.search(cleaned)
    if m:
        day = int(m.group(1))
        month = _MONTH_ALIASES.get(m.group(2))
        year = int(m.group(3))
        if month is not None:
            return _to_iso(year, month, day)

    return None
