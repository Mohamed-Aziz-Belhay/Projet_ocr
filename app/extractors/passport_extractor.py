from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.extractors.base import BaseExtractor, FieldOutput

_MRZ2_RE = re.compile(r"([A-Z0-9<]{44})")
_PASSPORT_NO_RE = re.compile(r"(?:passport\s*no|passport\s*n[o°]|pasportun\s*n[o°]mrasi|document\s*no)\s*[:#]?\s*([A-Z0-9]{6,12})", re.I)
_DATE_RE = re.compile(r"(\d{2})[./-](\d{2})[./-](\d{4})")
_SEX_RE = re.compile(r"([MF])|([QK])/(?:[FM])", re.I)


def _iso_date(raw: str) -> Optional[str]:
    m = _DATE_RE.search(raw or "")
    if not m:
        return None
    d, mo, y = m.groups()
    return f"{y}-{mo}-{d}"


class PassportExtractor(BaseExtractor):
    doc_family = "id_document"
    variant_id = "passport_generic"

    def can_handle(self, doc_family: str, variant_id: Optional[str] = None) -> bool:
        if doc_family != "id_document":
            return False
        return bool(variant_id and "passport" in variant_id)

    def extract(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[FieldOutput]:
        fields: List[FieldOutput] = []
        mrz_lines = re.findall(r"[A-Z0-9<]{30,44}", text or "")
        passport_number = None
        m = _PASSPORT_NO_RE.search(text or "")
        if m:
            passport_number = m.group(1).strip().upper()
        elif len(mrz_lines) >= 2:
            m2 = mrz_lines[-1]
            passport_number = m2[:9].replace("<", "").strip() or None

        surname = None
        given_names = None
        if mrz_lines:
            m1 = mrz_lines[0]
            if '<<' in m1:
                parts = m1.split('<<', 1)
                surname = parts[0][5:].replace('<', ' ').strip() or None
                given_names = parts[1].replace('<', ' ').strip() or None

        dob = None
        exp = None
        dates = _DATE_RE.findall(text or "")
        if len(dates) >= 1:
            d, mo, y = dates[0]
            dob = f"{y}-{mo}-{d}"
        if len(dates) >= 2:
            d, mo, y = dates[1]
            exp = f"{y}-{mo}-{d}"

        sex = None
        ms = _SEX_RE.search(text or "")
        if ms:
            sex = (ms.group(1) or ms.group(2) or '').upper()

        def make(name, value, conf=0.0, raw=None):
            return self._make_field(name, value, conf, raw, validated=value is not None, error=None if value is not None else 'Not found')

        fields.append(make('passport_number', passport_number, 0.92 if passport_number else 0.0, passport_number))
        fields.append(make('surname', surname, 0.86 if surname else 0.0, surname))
        fields.append(make('given_names', given_names, 0.86 if given_names else 0.0, given_names))
        fields.append(make('date_of_birth', dob, 0.82 if dob else 0.0, dob))
        fields.append(make('date_of_expiry', exp, 0.82 if exp else 0.0, exp))
        fields.append(make('sex', sex, 0.75 if sex else 0.0, sex))
        if len(mrz_lines) >= 1:
            fields.append(make('mrz_line_1', mrz_lines[0], 0.95, mrz_lines[0]))
        if len(mrz_lines) >= 2:
            fields.append(make('mrz_line_2', mrz_lines[1], 0.95, mrz_lines[1]))
        return fields
