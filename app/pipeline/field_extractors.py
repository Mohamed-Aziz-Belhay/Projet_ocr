#/pipline/field_extractors
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.template import FieldSpec, ValidationSpec, NormalizationSpec
from app.core.logging import get_logger

log = get_logger(__name__)

_SEPARATORS = re.compile(r"\s*[:/–\-]\s*|\s{2,}")
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_ARABIC_DIGITS_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_MONTH_ALIASES = {
    "جانفي": "جانفي",
    "يفناج": "جانفي",
    "فيفري": "فيفري",
    "يرفيف": "فيفري",
    "مارس": "مارس",
    "سرام": "مارس",
    "افريل": "افريل",
    "أفريل": "افريل",
    "ليرفا": "افريل",
    "ماي": "ماي",
    "يام": "ماي",
    "جوان": "جوان",
    "ناوج": "جوان",
    "جويلية": "جويلية",
    "جويليه": "جويلية",
    "ةيليوج": "جويلية",
    "أوت": "اوت",
    "اوت": "اوت",
    "توأ": "اوت",
    "سبتمبر": "سبتمبر",
    "ربمتبس": "سبتمبر",
    "أكتوبر": "اكتوبر",
    "اكتوبر": "اكتوبر",
    "ربوتكأ": "اكتوبر",
    "ربوتكا": "اكتوبر",
    "نوفمبر": "نوفمبر",
    "ربمفون": "نوفمبر",
    "ديسمبر": "ديسمبر",
    "ربمسيد": "ديسمبر",
}

_MONTHS_REGEX = r"(?:جانفي|فيفري|مارس|افريل|أفريل|ماي|جوان|جويلية|جويليه|أوت|اوت|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر)"

_CIN_LABELS_LAST = ["اللقب", "اللب", "بللا", "بقللا", "بّمللا"]
_CIN_LABELS_FIRST = ["الاسم", "السام", "مسالا", "مسا", "مسرا"]
_CIN_LABELS_PLACE = ["مكان الولادة", "مكانها", "الولادة", "ةدالولا", "خراب ةدالولا"]


def _collapse_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", (s or "").translate(_ARABIC_DIGITS_TRANS))


def _clean_text_basic(s: str) -> str:
    s = (s or "").translate(_ARABIC_DIGITS_TRANS)
    s = s.replace("\xa0", " ")
    s = s.replace("|", " ")
    s = s.replace("،", " ")
    s = s.replace("؛", " ")
    return _collapse_spaces(s)


def _clean_ar_value(s: str) -> str:
    s = _clean_text_basic(s)
    s = re.sub(r"[^\u0600-\u06FF0-9\s\-/]", " ", s)
    return _collapse_spaces(s)


def _normalize_place(s: str) -> str:
    s = _clean_ar_value(s)
    s = s.replace("القلعة الكبر", "القلعة الكبرى")
    s = s.replace(" الكبر", " الكبرى")
    s = s.replace("ربكلا", "الكبرى")
    return _collapse_spaces(s)


def _reverse_arabic_token(token: str) -> str:
    if re.search(r"[\u0600-\u06FF]", token):
        return token[::-1]
    return token


def _reverse_arabic_tokens_in_line(line: str) -> str:
    parts = re.split(r"(\s+)", line or "")
    parts = [_reverse_arabic_token(p) if not p.isspace() else p for p in parts]
    return "".join(parts)


def _build_text_variants(text: str) -> List[str]:
    base = (text or "").translate(_ARABIC_DIGITS_TRANS)
    lines = [x.rstrip() for x in base.splitlines()]
    v1 = "\n".join(lines)
    v2 = "\n".join(_reverse_arabic_tokens_in_line(x) for x in lines)

    out: List[str] = []
    for v in (v1, v2):
        v = "\n".join(_collapse_spaces(x) for x in v.splitlines())
        v = "\n".join(x for x in v.splitlines() if x.strip())
        if v and v not in out:
            out.append(v)
    return out


def _valid_ar_name(s: str) -> bool:
    t = _clean_ar_value(s)
    if len(t) < 2 or len(t) > 50:
        return False
    if len(_digits_only(t)) >= 2:
        return False

    parts = [p.strip(" .:-") for p in t.split() if p.strip(" .:-")]
    if not (1 <= len(parts) <= 6):
        return False

    return all(re.fullmatch(r"[\u0600-\u06FF]{2,}", p) for p in parts)


def _valid_place(s: str) -> bool:
    t = _normalize_place(s)
    if len(t) < 2 or len(t) > 60:
        return False
    if len(_digits_only(t)) > 0:
        return False

    parts = [p.strip(" .:-") for p in t.split() if p.strip(" .:-")]
    if not (1 <= len(parts) <= 5):
        return False

    return all(re.fullmatch(r"[\u0600-\u06FF]{2,}", p) for p in parts)


def _normalize_month_name(month: str) -> str:
    m = _collapse_spaces(month).replace("أ", "ا")
    return _MONTH_ALIASES.get(m, m)


def parse_date_any(text: str) -> Optional[str]:
    if not text:
        return None

    t = _clean_text_basic(text)

    m = re.search(r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})\b", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m = re.search(rf"\b(\d{{1,2}})\s+({_MONTHS_REGEX})\s+(\d{{4}})\b", t)
    if m:
        d = int(m.group(1))
        month_name = _normalize_month_name(m.group(2))
        y = int(m.group(3))
        month_map = {
            "جانفي": 1,
            "فيفري": 2,
            "مارس": 3,
            "افريل": 4,
            "ماي": 5,
            "جوان": 6,
            "جويلية": 7,
            "اوت": 8,
            "سبتمبر": 9,
            "اكتوبر": 10,
            "نوفمبر": 11,
            "ديسمبر": 12,
        }
        mo = month_map.get(month_name)
        if mo is None:
            return None
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def extract_cin_number(text: str) -> Optional[str]:
    if not text:
        return None

    for variant in _build_text_variants(text):
        for line in variant.splitlines():
            s = line.strip()
            if not s:
                continue
            if re.fullmatch(r"[\d\s]{8,20}", s):
                d = _digits_only(s)
                if re.fullmatch(r"\d{8}", d):
                    return d

        for m in re.finditer(r"(?<!\d)((?:\d[\s]*){8})(?!\d)", variant):
            d = _digits_only(m.group(1))
            if re.fullmatch(r"\d{8}", d):
                return d

        for m in re.finditer(r"\b(\d{8})\b", variant):
            return m.group(1)

    return None


def _extract_value_near_label(lines: List[str], labels: List[str], kind: str = "name") -> Optional[str]:
    for i, line in enumerate(lines):
        line_s = _collapse_spaces(line)
        if not line_s:
            continue

        matched_label = next((lab for lab in labels if lab in line_s), None)
        if not matched_label:
            continue

        candidate = line_s.replace(matched_label, " ")
        candidate = re.sub(r"[:/\-–]+", " ", candidate)
        candidate = _collapse_spaces(candidate)

        if kind == "name":
            if _valid_ar_name(candidate):
                return _clean_ar_value(candidate)
        else:
            candidate = _normalize_place(candidate)
            if _valid_place(candidate):
                return candidate

        for j in range(max(0, i - 1), min(len(lines), i + 3)):
            if j == i:
                continue
            neighbor = _collapse_spaces(lines[j])
            if not neighbor:
                continue
            if any(lbl in neighbor for lbl in labels):
                continue

            if kind == "name":
                if _valid_ar_name(neighbor):
                    return _clean_ar_value(neighbor)
            else:
                neighbor = _normalize_place(neighbor)
                if _valid_place(neighbor):
                    return neighbor

    return None


def _extract_birth_date_from_text(text: str) -> Optional[str]:
    if not text:
        return None

    for variant in _build_text_variants(text):
        m = re.search(r"\b(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{4})\b", variant)
        if m:
            return m.group(1)

        m = re.search(rf"\b(\d{{1,2}}\s+{_MONTHS_REGEX}\s+\d{{4}})\b", variant)
        if m:
            return m.group(1)

    return None


def _extract_place_from_bottom(text: str) -> Optional[str]:
    for variant in _build_text_variants(text):
        lines = [_collapse_spaces(x) for x in variant.splitlines()]
        lines = [x for x in lines if x]

        for line in reversed(lines[-5:]):
            cand = _normalize_place(line)
            cand = re.sub(r"^(مكان\s+الولادة|الولادة)\s*", "", cand).strip()
            if _valid_place(cand):
                return cand

    return None


def extract_cin_fields(text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    variants = _build_text_variants(text)

    cin = extract_cin_number(text)
    if cin:
        result["cin_number"] = cin

    for variant in variants:
        lines = [_collapse_spaces(x) for x in variant.splitlines() if x.strip()]

        if "family_name" not in result:
            family = _extract_value_near_label(lines, _CIN_LABELS_LAST, kind="name")
            if family:
                result["family_name"] = family

        if "first_name" not in result:
            first = _extract_value_near_label(lines, _CIN_LABELS_FIRST, kind="name")
            if first:
                result["first_name"] = _clean_ar_value(first).split()[0]

        if "place_of_birth" not in result:
            place = _extract_value_near_label(lines, _CIN_LABELS_PLACE, kind="place")
            if not place:
                place = _extract_place_from_bottom(variant)
            if place:
                result["place_of_birth"] = _normalize_place(place)

        if "date_of_birth" not in result:
            dob = _extract_birth_date_from_text(variant)
            if dob:
                result["date_of_birth"] = dob

    return result


def normalize(text: str, spec: NormalizationSpec) -> str:
    result = text or ""

    if getattr(spec, "strip", False):
        result = result.strip()

    for src, dst in getattr(spec, "custom_replace", {}).items():
        result = result.replace(src, dst)

    if getattr(spec, "remove_spaces", False):
        result = result.replace(" ", "")

    if getattr(spec, "uppercase", False):
        result = result.upper()
    elif getattr(spec, "lowercase", False):
        result = result.lower()

    return result


def validate_field(value: Any, spec: ValidationSpec) -> Tuple[bool, Optional[str]]:
    if value is None:
        if getattr(spec, "type", None) == "required":
            return False, "Champ requis"
        return False, "Aucune valeur extraite"

    sv = str(value)
    spec_type = getattr(spec, "type", None)

    if spec_type == "regex" and getattr(spec, "pattern", None):
        if not re.fullmatch(spec.pattern, sv):
            return False, f"Pattern non respecté: {spec.pattern}"

    elif spec_type == "date":
        if not parse_date_any(sv):
            return False, f"Date invalide: {sv}"

    elif spec_type == "number":
        try:
            float(sv.replace(",", ".").replace(" ", "").replace("\xa0", ""))
        except ValueError:
            return False, f"Pas un nombre: {sv}"

    elif spec_type == "enum":
        allowed = [str(v) for v in getattr(spec, "allowed_values", [])]
        if sv not in allowed:
            return False, "Valeur hors liste"

    elif spec_type == "required":
        if not sv.strip():
            return False, "Champ requis"

    min_length = getattr(spec, "min_length", None)
    max_length = getattr(spec, "max_length", None)

    if min_length and len(sv) < min_length:
        return False, f"Trop court (min {min_length})"
    if max_length and len(sv) > max_length:
        return False, f"Trop long (max {max_length})"

    return True, None


def extract_by_regex(text: str, patterns: List[str]) -> Tuple[Optional[str], float]:
    for i, pattern in enumerate(patterns):
        try:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.UNICODE)
            if m:
                value = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
                conf = max(0.95 - i * 0.07, 0.50)
                return value.strip(), conf
        except re.error as exc:
            log.warning("Regex invalide", extra={"pattern": pattern, "error": str(exc)})
    return None, 0.0


def _is_value(text: str) -> bool:
    if not text or len(text.strip()) < 2:
        return False
    return bool(re.search(r"[A-Za-zÀ-ÿ\u0600-\u06FF0-9]", text))


def extract_by_anchor(text: str, field: FieldSpec) -> Tuple[Optional[str], float]:
    lines = text.splitlines()
    anchors = getattr(field, "anchors", []) or []

    for anchor_spec in anchors:
        anchor_text = getattr(anchor_spec, "text", str(anchor_spec)).strip()
        anchor_lower = anchor_text.lower()
        is_arabic_anchor = bool(_ARABIC.search(anchor_text))

        for i, line in enumerate(lines):
            line_s = line.strip()
            line_l = line_s.lower()

            if anchor_lower not in line_l:
                continue

            parts = _SEPARATORS.split(line_s, maxsplit=1)
            if len(parts) == 2:
                left, right = parts[0].strip(), parts[1].strip()
                if anchor_lower in left.lower() and _is_value(right):
                    return right, 0.85
                if anchor_lower in right.lower() and _is_value(left):
                    return left, 0.83

            if is_arabic_anchor and anchor_lower in line_l:
                without_anchor = re.sub(re.escape(anchor_text), "", line_s, flags=re.IGNORECASE).strip(" :/–-")
                if _is_value(without_anchor):
                    return without_anchor, 0.80

            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if not next_line:
                    continue
                if _is_value(next_line):
                    return next_line, 0.75

    patterns = getattr(field, "patterns", None) or []
    if patterns:
        v, c = extract_by_regex(text, patterns)
        if v:
            return v, c * 0.88

    return None, 0.0


def extract_field(text: str, field: FieldSpec) -> dict:
    raw_value: Optional[str] = None
    confidence: float = 0.0
    method = getattr(field, "extraction_method", None)

    patterns = getattr(field, "patterns", None) or []
    anchors = getattr(field, "anchors", None) or []

    if method == "regex":
        if patterns:
            raw_value, confidence = extract_by_regex(text, patterns)
        if raw_value is None and anchors:
            raw_value, confidence = extract_by_anchor(text, field)
            if raw_value:
                confidence *= 0.88

    elif method == "anchor":
        raw_value, confidence = extract_by_anchor(text, field)
        if raw_value is None and patterns:
            raw_value, confidence = extract_by_regex(text, patterns)
            if raw_value:
                confidence *= 0.88
    else:
        if patterns:
            raw_value, confidence = extract_by_regex(text, patterns)
        if raw_value is None and anchors:
            raw_value, confidence = extract_by_anchor(text, field)

    normalization = getattr(field, "normalization", None)
    validation = getattr(field, "validation", None)

    norm_value = normalize(raw_value, normalization) if raw_value is not None and normalization else raw_value
    if validation:
        is_valid, error_msg = validate_field(norm_value, validation)
    else:
        is_valid, error_msg = (norm_value is not None), None

    output_name = getattr(field, "output_key", None) or getattr(field, "name", "unknown")

    return {
        "name": output_name,
        "value": norm_value,
        "confidence": round(confidence, 3),
        "validated": is_valid,
        "raw_text": raw_value,
        "error": error_msg if not is_valid and norm_value is not None else None,
    }