from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


CHECK_WEIGHTS = [7, 3, 1]


@dataclass
class MRZParseResult:
    valid: bool
    document_type: Optional[str]
    issuing_country: Optional[str]
    document_number: Optional[str]
    surname: Optional[str]
    given_names: Optional[str]
    nationality: Optional[str]
    birth_date: Optional[str]
    gender: Optional[str]
    expiry_date: Optional[str]
    personal_number: Optional[str]
    mrz_lines: List[str]
    checks: Dict[str, bool]
    errors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "document_type": self.document_type,
            "issuing_country": self.issuing_country,
            "document_number": self.document_number,
            "surname": self.surname,
            "given_names": self.given_names,
            "nationality": self.nationality,
            "birth_date": self.birth_date,
            "gender": self.gender,
            "expiry_date": self.expiry_date,
            "personal_number": self.personal_number,
            "mrz_lines": self.mrz_lines,
            "checks": self.checks,
            "errors": self.errors,
        }


def _char_value(ch: str) -> int:
    if ch.isdigit():
        return int(ch)

    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10

    if ch == "<":
        return 0

    return 0


def mrz_checksum(data: str) -> str:
    total = 0

    for idx, ch in enumerate(data):
        total += _char_value(ch) * CHECK_WEIGHTS[idx % 3]

    return str(total % 10)


def _clean_line_loose(line: str) -> str:
    """
    Keep MRZ characters without changing letters into digits globally.

    Important:
    Do NOT replace D/I/L/B/O/S with digits here.
    Those replacements corrupt names:
    ABDULLAYEV -> A80U11AYEV
    DIL        -> 011
    """

    line = str(line or "").upper()
    line = line.replace(" ", "")
    line = line.replace("\t", "")
    line = line.replace("\r", "")
    line = line.replace("«", "<").replace("‹", "<").replace("＜", "<")
    line = line.replace("_", "<")
    line = re.sub(r"[^A-Z0-9<]", "", line)
    return line


def _fix_line_length(line: str, target_len: int = 44) -> str:
    line = _clean_line_loose(line)

    if len(line) > target_len:
        return line[:target_len]

    if len(line) < target_len:
        return line + ("<" * (target_len - len(line)))

    return line


def extract_td3_mrz_lines(raw_text: str) -> Tuple[List[str], List[str]]:
    """
    Extract passport TD3 MRZ lines:
    - 2 lines
    - 44 chars each
    - first line usually starts with P
    """

    errors: List[str] = []

    if not raw_text:
        return [], ["empty_mrz_text"]

    raw_lines = [
        _clean_line_loose(line)
        for line in str(raw_text).splitlines()
        if _clean_line_loose(line)
    ]

    candidates = [line for line in raw_lines if len(line) >= 30]

    for i in range(len(candidates) - 1):
        l1 = _fix_line_length(candidates[i], 44)
        l2 = _fix_line_length(candidates[i + 1], 44)

        if l1.startswith("P") and len(l1) == 44 and len(l2) == 44:
            return [l1, l2], []

    joined = "".join(_clean_line_loose(line) for line in str(raw_text).splitlines())
    joined = re.sub(r"[^A-Z0-9<]", "", joined)

    idx = joined.find("P<")

    if idx == -1:
        idx = joined.find("P")

    if idx != -1 and len(joined) - idx >= 70:
        chunk = joined[idx : idx + 88]
        chunk = chunk.ljust(88, "<")[:88]
        return [chunk[:44], chunk[44:88]], []

    errors.append("td3_mrz_lines_not_found")
    return [], errors


def _letters_from_ocr(value: str) -> str:
    """
    Normalize OCR confusions when a field should contain letters.

    Example:
    nationality raw '42E' should become 'AZE'.
    """

    value = str(value or "").upper()

    replacements = {
        "0": "O",
        "1": "I",
        "2": "Z",
        "4": "A",
        "5": "S",
        "8": "B",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = re.sub(r"[^A-Z<]", "", value)
    return value


def _digits_from_ocr(value: str) -> str:
    """
    Normalize OCR confusions when a field should contain digits.
    """

    value = str(value or "").upper()

    replacements = {
        "O": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "L": "1",
        "|": "1",
        "Z": "2",
        "S": "5",
        "B": "8",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = re.sub(r"[^0-9]", "", value)
    return value


def _document_number_from_ocr(value: str) -> str:
    """
    Document number can contain letters and digits.
    Keep letters; do not force all letters to digits.
    """

    value = str(value or "").upper()
    value = value.replace(" ", "")
    value = re.sub(r"[^A-Z0-9<]", "", value)
    return value.replace("<", "")


def _optional_data_from_ocr(value: str) -> Optional[str]:
    """
    Personal number / optional data can contain letters and digits.
    Preserve letters like L and V.
    """

    value = str(value or "").upper()
    value = re.sub(r"[^A-Z0-9<]", "", value)
    value = value.replace("<", "")
    return value or None


def _parse_names(name_field: str) -> Tuple[Optional[str], Optional[str]]:
    if not name_field:
        return None, None

    parts = name_field.split("<<", 1)

    surname_raw = parts[0] if parts else ""
    given_raw = parts[1] if len(parts) > 1 else ""

    surname = surname_raw.replace("<", " ").strip()
    given_names = given_raw.replace("<", " ").strip()

    surname = re.sub(r"[^A-Z' -]", "", surname)
    given_names = re.sub(r"[^A-Z' -]", "", given_names)

    surname = re.sub(r"\s+", " ", surname).strip()
    given_names = re.sub(r"\s+", " ", given_names).strip()

    return surname or None, given_names or None


def _parse_yymmdd(value: str, kind: str) -> Optional[str]:
    value = _digits_from_ocr(value)

    if not value or not re.fullmatch(r"\d{6}", value):
        return None

    yy = int(value[0:2])
    mm = int(value[2:4])
    dd = int(value[4:6])

    current_year = datetime.utcnow().year % 100

    if kind == "birth":
        century = 1900 if yy > current_year else 2000
    else:
        century = 2000 if yy < 80 else 1900

    year = century + yy

    try:
        dt = datetime(year, mm, dd)
    except ValueError:
        return None

    return dt.strftime("%Y-%m-%d")


def _normalize_gender(value: str) -> Optional[str]:
    value = str(value or "").upper().strip()

    if value in {"M", "F"}:
        return value

    if value == "<":
        return None

    return None


def parse_td3_passport_mrz(raw_text: str) -> MRZParseResult:
    errors: List[str] = []
    checks: Dict[str, bool] = {}

    lines, line_errors = extract_td3_mrz_lines(raw_text)
    errors.extend(line_errors)

    if len(lines) != 2:
        return MRZParseResult(
            valid=False,
            document_type=None,
            issuing_country=None,
            document_number=None,
            surname=None,
            given_names=None,
            nationality=None,
            birth_date=None,
            gender=None,
            expiry_date=None,
            personal_number=None,
            mrz_lines=lines,
            checks=checks,
            errors=errors,
        )

    l1 = _fix_line_length(lines[0], 44)
    l2 = _fix_line_length(lines[1], 44)

    document_type = l1[0:2].replace("<", "") or None

    issuing_country_raw = l1[2:5]
    issuing_country = _letters_from_ocr(issuing_country_raw).replace("<", "") or None

    name_field = l1[5:44]
    surname, given_names = _parse_names(name_field)

    document_number_raw = l2[0:9]
    document_number = _document_number_from_ocr(document_number_raw)
    document_number_check = l2[9]

    nationality_raw = l2[10:13]
    nationality = _letters_from_ocr(nationality_raw).replace("<", "") or None

    birth_raw = l2[13:19]
    birth_check = l2[19]

    gender = _normalize_gender(l2[20])

    expiry_raw = l2[21:27]
    expiry_check = l2[27]

    personal_number_raw = l2[28:42]
    personal_number = _optional_data_from_ocr(personal_number_raw)
    personal_number_check = l2[42]

    composite_check = l2[43]

    checks["document_number"] = mrz_checksum(document_number_raw) == document_number_check
    checks["birth_date"] = mrz_checksum(birth_raw) == birth_check
    checks["expiry_date"] = mrz_checksum(expiry_raw) == expiry_check

    if personal_number_raw.strip("<"):
        checks["personal_number"] = mrz_checksum(personal_number_raw) == personal_number_check
    else:
        checks["personal_number"] = True

    composite_data = (
        l2[0:10]
        + l2[13:20]
        + l2[21:28]
        + l2[28:43]
    )
    checks["composite"] = mrz_checksum(composite_data) == composite_check

    birth_date = _parse_yymmdd(birth_raw, "birth")
    expiry_date = _parse_yymmdd(expiry_raw, "expiry")

    if not document_number:
        errors.append("missing_document_number")

    if not surname:
        errors.append("missing_surname")

    if not nationality or not re.fullmatch(r"[A-Z]{3}", nationality):
        errors.append("invalid_nationality")

    if not birth_date:
        errors.append("invalid_birth_date")

    if not expiry_date:
        errors.append("invalid_expiry_date")

    if gender not in {"M", "F", None}:
        errors.append("invalid_gender")

    if issuing_country and nationality and len(nationality) == 3:
        # For most MIDV samples, nationality should be the same as issuing country.
        # This also fixes OCR case: raw '42E' -> normalized 'AZE'.
        if nationality != issuing_country and issuing_country == "AZE":
            errors.append("nationality_mismatch_with_issuing_country")

    core_ok = bool(
        document_number
        and surname
        and nationality
        and re.fullmatch(r"[A-Z]{3}", nationality)
        and birth_date
        and expiry_date
    )

    strong_checks_ok = (
        checks.get("document_number", False)
        and checks.get("birth_date", False)
        and checks.get("expiry_date", False)
    )

    valid = core_ok and (strong_checks_ok or checks.get("composite", False))

    if not valid:
        errors.append("mrz_checksum_or_core_validation_failed")

    # Canonical output line:
    # Keep line 1 as cleaned OCR.
    # Correct nationality only in line 2 because nationality has no direct checksum.
    canonical_l2 = l2[:10] + (nationality or l2[10:13]).ljust(3, "<")[:3] + l2[13:44]

    return MRZParseResult(
        valid=valid,
        document_type=document_type,
        issuing_country=issuing_country,
        document_number=document_number or None,
        surname=surname,
        given_names=given_names,
        nationality=nationality,
        birth_date=birth_date,
        gender=gender,
        expiry_date=expiry_date,
        personal_number=personal_number,
        mrz_lines=[l1, canonical_l2],
        checks=checks,
        errors=list(dict.fromkeys(errors)),
    )