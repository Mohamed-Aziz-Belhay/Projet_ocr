from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Tuple


LATIN_NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ' -]{3,}$")


def _strip_noise(text: str) -> str:
    if text is None:
        return ""

    text = str(text).strip()
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _contains_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def _replace_digit_confusions(text: str) -> str:
    """
    For fields expected to be numeric or mostly numeric.
    Do not use this for names.
    """

    text = _strip_noise(text)

    replacements = {
        "O": "0",
        "o": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "l": "1",
        "|": "1",
        "S": "5",
        "B": "8",
        "٠": "0",
        "۰": "0",
        "١": "1",
        "۱": "1",
        "٢": "2",
        "۲": "2",
        "٣": "3",
        "۳": "3",
        "٤": "4",
        "۴": "4",
        "٥": "5",
        "۵": "5",
        "٦": "6",
        "۶": "6",
        "٧": "7",
        "۷": "7",
        "٨": "8",
        "۸": "8",
        "٩": "9",
        "۹": "9",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text.strip()


def _template_expected_nationality(template_id: Optional[str]) -> Optional[str]:
    tid = str(template_id or "").lower()

    if "svk" in tid:
        return "SVK"

    if "aze" in tid:
        return "AZE"

    if "srb" in tid:
        return "SRB"

    return None


def normalize_midv_date(raw: str) -> Tuple[Optional[str], bool]:
    text = _replace_digit_confusions(raw)

    patterns = [
        r"(\d{1,2})[.\-/ ]+(\d{1,2})[.\-/ ]+(\d{4})",
        r"(\d{4})[.\-/ ]+(\d{1,2})[.\-/ ]+(\d{1,2})",
    ]

    for idx, pattern in enumerate(patterns):
        m = re.search(pattern, text)

        if not m:
            continue

        try:
            if idx == 0:
                day = int(m.group(1))
                month = int(m.group(2))
                year = int(m.group(3))
            else:
                year = int(m.group(1))
                month = int(m.group(2))
                day = int(m.group(3))

            dt = datetime(year, month, day)
            return dt.strftime("%Y-%m-%d"), True

        except ValueError:
            return None, False

    return None, False


def normalize_gender(raw: str) -> Tuple[Optional[str], bool]:
    text = _strip_noise(raw).upper()
    text = re.sub(r"[^MF]", "", text)

    if text in {"M", "F"}:
        return text, True

    return None, False


def normalize_document_number(raw: str) -> Tuple[Optional[str], bool]:
    """
    MIDV document number, e.g. EU394022, TJ033945.
    """

    text = _strip_noise(raw).upper()

    replacements = {
        "[": "E",
        "]": "",
        "|": "I",
        " ": "",
        "\n": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[^A-Z0-9]", "", text)

    # OCR case: U394022 -> EU394022
    if text.startswith("U") and len(text) >= 7:
        text = "E" + text

    m = re.search(r"[A-Z]{2}\d{6}", text)
    if m:
        return m.group(0), True

    m = re.search(r"[A-Z]{1,3}\d{5,9}", text)
    if m:
        value = m.group(0)
        return value, True

    return text or None, False


def normalize_personal_number(raw: str) -> Tuple[Optional[str], bool]:
    """
    Personal number, e.g. 550522/3941 or 800618/4033.
    """

    text = _replace_digit_confusions(raw)

    replacements = {
        ";": "5",
        "؛": "5",
        ":": "",
        "-": "",
        " ": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[^0-9/]", "", text)

    m = re.search(r"\d{6}/\d{3,4}", text)
    if m:
        return m.group(0), True

    # Sometimes slash is missed.
    m = re.search(r"(\d{6})(\d{3,4})", text)
    if m:
        return f"{m.group(1)}/{m.group(2)}", True

    return text or None, False


def normalize_nationality(
    raw: str,
    expected_nationality: Optional[str] = None,
) -> Tuple[Optional[str], bool]:
    """
    Strict nationality validation.

    If expected_nationality is provided, only that value is accepted.
    This prevents OCR garbage like GTT/CVV from becoming valid for midv_svk_id.
    """

    text_raw = _strip_noise(raw).upper()
    cleaned = re.sub(r"[^A-Z]", "", text_raw)

    expected = (expected_nationality or "").strip().upper()

    if expected:
        if expected in cleaned:
            return expected, True

        return cleaned or None, False

    # Generic fallback: accept only known MIDV countries already used in the project.
    known = {
        "SVK",
        "AZE",
        "SRB",
        "ALB",
        "ESP",
        "EST",
        "FIN",
        "GRC",
        "LVA",
        "RUS",
    }

    if cleaned in known:
        return cleaned, True

    return cleaned or None, False


def normalize_latin_name(raw: str) -> Tuple[Optional[str], bool]:
    text = _strip_noise(raw)

    if not text:
        return None, False

    if _contains_arabic(text):
        return None, False

    upper = text.upper()

    forbidden_keywords = {
        "ID CARD",
        "CARD",
        "IDENTITY",
        "REPUBLIC",
        "SLOVAK",
        "SLOVENSKA",
        "PERSONAL",
        "NUMBER",
        "DATE",
        "DOCUMENT",
        "PASSPORT",
        "NATIONALITY",
        "SIGNATURE",
        "SURNAME",
        "GIVEN",
        "NAME",
    }

    if any(keyword in upper for keyword in forbidden_keywords):
        return None, False

    text = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' -]", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return None, False

    # Reject very fragmented OCR noise:
    # examples: "C Zc", "C S E trjeo"
    tokens = [t for t in re.split(r"[\s'-]+", text) if t]

    if not tokens:
        return None, False

    long_tokens = [t for t in tokens if len(t) >= 3]

    # For MIDV name/surname, require at least one solid word.
    if not long_tokens:
        return text, False

    # Reject strings with too many one-letter fragments.
    one_letter_tokens = [t for t in tokens if len(t) == 1]

    if len(one_letter_tokens) >= 2 and len(long_tokens) <= 1:
        return text, False

    # Reject suspicious mixed garbage with many tiny fragments.
    if len(tokens) >= 3 and len(long_tokens) <= 1:
        return text, False

    cleaned = " ".join(tokens)

    if len(cleaned) < 3:
        return cleaned or None, False

    if LATIN_NAME_RE.fullmatch(cleaned):
        return cleaned, True

    return cleaned or None, False


def normalize_optional_latin_text(raw: str) -> Tuple[Optional[str], bool]:
    text = _strip_noise(raw)

    if not text:
        return None, False

    if _contains_arabic(text):
        return None, False

    text = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' .,/()-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return None, False

    tokens = [t for t in re.split(r"[\s'.,/()-]+", text) if t]

    if not tokens:
        return None, False

    # Reject tiny OCR fragments like "Md", "Oa", "X".
    if len(tokens) == 1 and len(tokens[0]) < 3:
        return text, False

    # Require at least one meaningful token.
    if not any(len(t) >= 3 for t in tokens):
        return text, False

    return text, True


def normalize_midv_field(
    field_name: str,
    raw_text: str,
    expected_nationality: Optional[str] = None,
    template_id: Optional[str] = None,
) -> Tuple[Optional[str], bool, Optional[str]]:
    """
    Compatible MIDV field normalizer.

    Supports both:
    - expected_nationality=...
    - template_id=...

    This keeps compatibility with older calls and the new ROI service.
    """

    field_name = str(field_name or "").strip()

    if not expected_nationality:
        expected_nationality = _template_expected_nationality(template_id)

    if field_name in {"birth_date", "expiry_date", "issue_date"}:
        value, valid = normalize_midv_date(raw_text)

        if not valid:
            return value, False, "invalid_date"

        return value, True, None

    if field_name == "gender":
        value, valid = normalize_gender(raw_text)

        if not valid:
            return value, False, "invalid_gender"

        return value, True, None

    if field_name == "number":
        value, valid = normalize_document_number(raw_text)

        if not valid:
            return value, False, "invalid_document_number"

        return value, True, None

    if field_name == "id_number":
        value, valid = normalize_personal_number(raw_text)

        if not valid:
            return value, False, "invalid_personal_number"

        return value, True, None

    if field_name == "nationality":
        value, valid = normalize_nationality(
            raw_text,
            expected_nationality=expected_nationality,
        )

        if not valid:
            return value, False, "invalid_nationality"

        return value, True, None

    if field_name in {"name", "surname"}:
        value, valid = normalize_latin_name(raw_text)

        if not valid:
            return value, False, "invalid_latin_name"

        return value, True, None

    if field_name == "issue_place":
        value, valid = normalize_optional_latin_text(raw_text)

        if not valid:
            return value, False, "invalid_issue_place"

        return value, True, None

    text = _strip_noise(raw_text)

    if not text:
        return None, False, "empty"

    return text, True, None