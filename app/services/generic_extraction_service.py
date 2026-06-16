"""
app/services/generic_extraction_service.py

Generic, template-driven field extraction service.

Purpose:
- invoices
- passports
- registre de commerce
- custom business documents

Extraction methods:
- regex patterns
- label/value heuristics
- type normalization
- simple validation
- output mapping
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple


class GenericExtractionService:
    def _get(self, obj: Any, key: str, default=None):
        if obj is None:
            return default

        if isinstance(obj, dict):
            return obj.get(key, default)

        return getattr(obj, key, default)

    def _fields(self, template: Any) -> List[Dict[str, Any]]:
        fields = self._get(template, "fields", []) or []
        out: List[Dict[str, Any]] = []

        for field in fields:
            if isinstance(field, dict):
                out.append(field)
            else:
                try:
                    out.append(field.model_dump())
                except Exception:
                    out.append(
                        {
                            "name": getattr(field, "name", None),
                            "type": getattr(field, "type", "text"),
                            "required": getattr(field, "required", False),
                            "patterns": getattr(field, "patterns", []),
                            "aliases": getattr(field, "aliases", []),
                            "output_key": getattr(field, "output_key", None),
                        }
                    )

        return [f for f in out if f.get("name")]

    def _output_mapping(self, template: Any) -> Dict[str, str]:
        mapping = self._get(template, "output_mapping", {}) or {}

        if isinstance(mapping, dict):
            return mapping

        return {}

    def _clean_text(self, text: str) -> str:
        text = text or ""
        text = text.replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _normalize_value(self, value: str, field_type: str) -> Any:
        if value is None:
            return None

        value = str(value).strip(" \t\n\r:;-")

        if not value:
            return None

        if field_type in {"number", "integer"}:
            digits = re.sub(r"\D", "", value)
            return digits or None

        if field_type in {"amount", "money", "currency"}:
            cleaned = value.replace(" ", "")
            cleaned = cleaned.replace(",", ".")
            match = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
            return match.group(0) if match else value

        if field_type == "date":
            return self._normalize_date(value)

        if field_type == "mrz":
            return re.sub(r"\s+", "", value.upper())

        return value

    def _normalize_date(self, value: str) -> Optional[str]:
        value = value.strip()

        patterns = [
            r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})",
            r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})",
        ]

        for pat in patterns:
            m = re.search(pat, value)

            if not m:
                continue

            parts = m.groups()

            if len(parts[0]) == 4:
                y, mo, d = parts
            else:
                d, mo, y = parts

            try:
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            except Exception:
                return value

        # French/Arabic month fallback can be improved later.
        return value

    def _validate_value(self, value: Any, field: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        required = bool(field.get("required", False))
        field_type = field.get("type", "text")

        if value in (None, "", []):
            if required:
                return False, "required field missing"
            return False, "field not found"

        validation = field.get("validation", {}) or {}

        regex = validation.get("regex") or field.get("validation_regex")
        if regex:
            if not re.search(regex, str(value), flags=re.IGNORECASE | re.UNICODE):
                return False, "regex validation failed"

        min_len = validation.get("min_length")
        if min_len is not None and len(str(value)) < int(min_len):
            return False, "min_length validation failed"

        max_len = validation.get("max_length")
        if max_len is not None and len(str(value)) > int(max_len):
            return False, "max_length validation failed"

        if field_type in {"amount", "money", "currency"}:
            if not re.search(r"\d", str(value)):
                return False, "amount validation failed"

        return True, None

    def _extract_by_patterns(
        self,
        raw_text: str,
        field: Dict[str, Any],
    ) -> Tuple[Optional[str], Optional[str]]:
        patterns = field.get("patterns") or []

        if isinstance(patterns, str):
            patterns = [patterns]

        for pattern in patterns:
            try:
                match = re.search(
                    pattern,
                    raw_text,
                    flags=re.IGNORECASE | re.MULTILINE | re.UNICODE,
                )

                if match:
                    if match.groups():
                        return match.group(1).strip(), pattern
                    return match.group(0).strip(), pattern

            except re.error:
                continue

        return None, None

    def _extract_by_aliases(
        self,
        raw_text: str,
        field: Dict[str, Any],
    ) -> Tuple[Optional[str], Optional[str]]:
        aliases = field.get("aliases") or field.get("label_patterns") or []

        if isinstance(aliases, str):
            aliases = [aliases]

        lines = [x.strip() for x in raw_text.splitlines() if x.strip()]

        for alias in aliases:
            alias_re = re.escape(str(alias).strip())

            # Same line: "Label: value"
            pattern = rf"{alias_re}\s*[:\-]?\s*(.+)$"

            for line in lines:
                match = re.search(
                    pattern,
                    line,
                    flags=re.IGNORECASE | re.UNICODE,
                )

                if match:
                    candidate = match.group(1).strip()

                    if candidate and candidate.lower() != str(alias).lower():
                        return candidate, alias

            # Next-line heuristic
            for idx, line in enumerate(lines):
                if re.search(alias_re, line, flags=re.IGNORECASE | re.UNICODE):
                    if idx + 1 < len(lines):
                        return lines[idx + 1], alias

        return None, None

    def _fallback_extractors(
        self,
        raw_text: str,
        document_type: str,
    ) -> List[Dict[str, Any]]:
        if document_type == "invoice":
            return [
                {
                    "name": "invoice_number",
                    "type": "text",
                    "required": True,
                    "patterns": [
                        r"(?:facture|invoice|n[°o]|num[eé]ro)\s*[:#\-]?\s*([A-Z0-9\-\/]+)",
                    ],
                    "output_key": "invoiceNumber",
                },
                {
                    "name": "invoice_date",
                    "type": "date",
                    "required": False,
                    "patterns": [
                        r"(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{4})",
                    ],
                    "output_key": "invoiceDate",
                },
                {
                    "name": "total_amount",
                    "type": "amount",
                    "required": True,
                    "patterns": [
                        r"(?:total\s*(?:ttc)?|montant\s*total)\s*[:\-]?\s*([0-9\s.,]+)",
                    ],
                    "output_key": "totalAmount",
                },
            ]

        if document_type == "passport":
            return [
                {
                    "name": "passport_number",
                    "type": "text",
                    "required": True,
                    "patterns": [
                        r"(?:passport|passeport|no\.?|n[°o])\s*[:#\-]?\s*([A-Z0-9]{5,12})",
                    ],
                    "output_key": "passportNumber",
                },
                {
                    "name": "surname",
                    "type": "text",
                    "required": False,
                    "aliases": ["Surname", "Nom"],
                    "output_key": "surname",
                },
                {
                    "name": "given_names",
                    "type": "text",
                    "required": False,
                    "aliases": ["Given names", "Prénoms", "Prenom"],
                    "output_key": "givenNames",
                },
            ]

        if document_type == "registre_commerce":
            return [
                {
                    "name": "rc_number",
                    "type": "text",
                    "required": True,
                    "patterns": [
                        r"(?:registre\s*de\s*commerce|rc|r\.c\.?)\s*[:#\-]?\s*([A-Z0-9\/\-]+)",
                    ],
                    "output_key": "rcNumber",
                },
                {
                    "name": "company_name",
                    "type": "text",
                    "required": True,
                    "aliases": ["Raison sociale", "Dénomination", "Nom commercial"],
                    "output_key": "companyName",
                },
                {
                    "name": "tax_id",
                    "type": "text",
                    "required": False,
                    "patterns": [
                        r"(?:matricule\s*fiscal|identifiant\s*fiscal|mf)\s*[:#\-]?\s*([A-Z0-9\/\-]+)",
                    ],
                    "output_key": "taxId",
                },
            ]

        return []

    def extract(
        self,
        *,
        raw_text: str,
        template: Optional[Any],
        document_type: str = "auto",
        language: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
        raw_text = self._clean_text(raw_text)

        fields_cfg = self._fields(template)

        if not fields_cfg:
            fields_cfg = self._fallback_extractors(raw_text, document_type)

        output_mapping = self._output_mapping(template)

        field_results: List[Dict[str, Any]] = []
        normalized_data: Dict[str, Any] = {}
        debug: Dict[str, Any] = {
            "document_type": document_type,
            "language": language,
            "template_used": self._get(template, "id"),
            "field_debug": [],
        }
        warnings: List[str] = []

        for field in fields_cfg:
            name = field.get("name")
            field_type = field.get("type", "text")
            required = bool(field.get("required", False))

            raw_value, matched_by = self._extract_by_patterns(raw_text, field)

            source = "regex"

            if raw_value is None:
                raw_value, matched_by = self._extract_by_aliases(raw_text, field)
                source = "label_alias"

            value = self._normalize_value(raw_value, field_type)
            valid, error = self._validate_value(value, field)

            confidence = 0.0

            if valid:
                confidence = 0.86 if source == "regex" else 0.76
            elif value not in (None, ""):
                confidence = 0.45

            output_key = (
                field.get("output_key")
                or output_mapping.get(name)
                or name
            )

            if value is not None:
                normalized_data[output_key] = value
            else:
                normalized_data[output_key] = None

            result = {
                "name": name,
                "value": value,
                "confidence": round(confidence, 3),
                "validated": bool(valid),
                "raw_text": raw_value,
                "raw_template_field": name,
                "error": error,
                "selected_engine": None,
                "selected_source": source if raw_value is not None else None,
                "review_required": bool(required and not valid),
                "reasons": (
                    [f"matched_by:{source}", f"pattern:{matched_by}"]
                    if raw_value is not None
                    else ["field unresolved"]
                ),
            }

            field_results.append(result)

            debug["field_debug"].append(
                {
                    "name": name,
                    "type": field_type,
                    "required": required,
                    "source": source,
                    "matched_by": matched_by,
                    "raw_value": raw_value,
                    "normalized_value": value,
                    "valid": valid,
                    "error": error,
                }
            )

            if required and not valid:
                warnings.append(f"Required field '{name}' is missing or invalid")

        return field_results, normalized_data, debug, warnings


@lru_cache(maxsize=1)
def get_generic_extraction_service() -> GenericExtractionService:
    return GenericExtractionService()