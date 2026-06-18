from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.schemas.template import DocumentTemplate, TemplateField, TemplateStrategy
from app.utils.date_validation import normalize_date_strict


def _collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _normalize_one(value: str, normalizer: str | None) -> str:
    if value is None:
        return ""

    value = str(value).strip()

    if normalizer == "strip":
        return value.strip()
    if normalizer == "upper":
        return value.upper()
    if normalizer == "lower":
        return value.lower()
    if normalizer in {"collapse_spaces", "compact_spaces"}:
        return _collapse_spaces(value)
    if normalizer == "digits_only":
        return re.sub(r"\D+", "", value)
    if normalizer in {"date_strict", "date_dd_mm_yyyy"}:
        return normalize_date_strict(value) or value
    if normalizer == "amount":
        return value.replace(" ", "").replace(",", ".")

    return value


def _apply_normalizers(value: Any, field: TemplateField) -> Any:
    if value is None:
        return None

    result = str(value)
    chain = []

    if field.normalizer:
        chain.append(field.normalizer)
    chain.extend(field.normalize or [])

    seen = set()
    ordered = []
    for item in chain:
        if item and item not in seen:
            ordered.append(item)
            seen.add(item)

    for normalizer in ordered:
        result = _normalize_one(result, normalizer)

    return result


def _extract_with_regex(text: str, pattern: str) -> Optional[str]:
    try:
        m = re.search(pattern, text or "", flags=re.IGNORECASE | re.MULTILINE)
        if not m:
            return None
        return m.group(1) if m.groups() else m.group(0)
    except re.error:
        return None


class GenericTemplateExtractor:
    def _candidate_texts(self, source: Dict[str, Any], field: TemplateField) -> List[str]:
        candidates: List[str] = []

        global_text = source.get("text") or ""
        if global_text:
            candidates.append(global_text)

        pages = source.get("pages") or []
        for page in pages:
            page_text = page.get("text") or ""
            if page_text:
                candidates.append(page_text)

        # futur: support vrai region OCR/layout
        if field.source_region:
            regions = source.get("regions") or {}
            region_text = regions.get(field.source_region)
            if isinstance(region_text, str) and region_text.strip():
                candidates.insert(0, region_text)

        return candidates

    def _run_strategy(
        self,
        strategy: TemplateStrategy,
        field: TemplateField,
        source: Dict[str, Any],
    ) -> Optional[str]:
        stype = (strategy.type or "regex").strip().lower()

        if stype == "field_fallback":
            source_field = strategy.source_field or field.name
            raw = source.get("critical_fields_raw", {}).get(source_field)
            if raw not in (None, "", [], {}):
                return str(raw)

            cooked = source.get("critical_fields", {}).get(source_field)
            if cooked not in (None, "", [], {}):
                return str(cooked)

            return None

        if stype == "regex":
            for txt in self._candidate_texts(source, field):
                if not txt:
                    continue
                out = _extract_with_regex(txt, strategy.pattern or "")
                if out:
                    return out
            return None

        if stype == "anchor":
            anchor = (strategy.anchor or "").strip()
            if not anchor:
                return None

            for txt in self._candidate_texts(source, field):
                idx = txt.find(anchor)
                if idx < 0:
                    continue
                window = int(strategy.window or 80)
                segment = txt[idx: idx + window]
                # si pattern existe, applique-le dans la fenêtre
                if strategy.pattern:
                    out = _extract_with_regex(segment, strategy.pattern)
                    if out:
                        return out
                else:
                    return segment.replace(anchor, "").strip()
            return None

        if stype == "text_line":
            for txt in self._candidate_texts(source, field):
                lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
                if lines:
                    return lines[0]
            return None

        return None

    def _validate_value(self, value: Any, field: TemplateField) -> tuple[bool, List[str]]:
        errors: List[str] = []

        if field.required and value in (None, "", [], {}):
            errors.append("required")
            return False, errors

        sval = str(value).strip() if value not in (None, "", [], {}) else ""

        for validator in field.validators or []:
            vtype = (validator.get("type") or "").strip().lower()

            if vtype == "required":
                if value in (None, "", [], {}):
                    errors.append("required")

            elif vtype == "regex":
                pattern = validator.get("pattern")
                if sval and pattern:
                    if not re.fullmatch(pattern, sval):
                        errors.append(f"regex:{pattern}")

            elif vtype == "min_length":
                n = int(validator.get("value", 0))
                if sval and len(sval) < n:
                    errors.append(f"min_length:{n}")

            elif vtype == "no_digits":
                if sval and re.search(r"\d", sval):
                    errors.append("no_digits")

            elif vtype == "forbidden_substrings":
                forbidden = validator.get("values", []) or []
                lowered = sval.lower()
                for item in forbidden:
                    if item and item.lower() in lowered:
                        errors.append(f"forbidden_substring:{item}")

            elif vtype == "not_regex":
                pattern = validator.get("pattern")
                if sval and pattern:
                    if re.search(pattern, sval):
                        errors.append(f"not_regex:{pattern}")

        return len(errors) == 0, errors

    def extract(self, source: Any, template: Any) -> Dict[str, Any]:
        template_model = (
            template
            if isinstance(template, DocumentTemplate)
            else DocumentTemplate.model_validate(template)
        )

        # compatibilité si source = texte brut
        if isinstance(source, str):
            source = {
                "text": source,
                "critical_fields": {},
                "critical_fields_raw": {},
                "pages": [],
            }

        if not isinstance(source, dict):
            source = {
                "text": str(source or ""),
                "critical_fields": {},
                "critical_fields_raw": {},
                "pages": [],
            }

        result: Dict[str, Any] = {}
        validation: Dict[str, Any] = {}
        errors: List[str] = []

        for field in template_model.fields:
            found_value = None

            # 1) stratégies explicites
            for strategy in field.strategies or []:
                found_value = self._run_strategy(strategy, field, source)
                if found_value not in (None, "", [], {}):
                    break

            # 2) fallback ancien patterns[]
            if found_value in (None, "", [], {}) and field.patterns:
                for pattern in field.patterns:
                    for txt in self._candidate_texts(source, field):
                        out = _extract_with_regex(txt, pattern)
                        if out:
                            found_value = out
                            break
                    if found_value:
                        break

            # 3) fallback critical_fields si même nom
            if found_value in (None, "", [], {}):
                raw = source.get("critical_fields_raw", {}).get(field.name)
                if raw not in (None, "", [], {}):
                    found_value = raw
                else:
                    cooked = source.get("critical_fields", {}).get(field.name)
                    if cooked not in (None, "", [], {}):
                        found_value = cooked

            # 4) default
            if found_value in (None, "", [], {}):
                found_value = field.default

            # normalisation
            found_value = _apply_normalizers(found_value, field)

            ok, field_errors = self._validate_value(found_value, field)

            key = field.output_key or field.name
            if found_value not in (None, "", [], {}):
                if ok:
                   result[key] = found_value

            validation[key] = {
                "ok": ok,
                "errors": field_errors,
            }

            if field_errors:
                errors.append(f"{key}: {', '.join(field_errors)}")

        if template_model.output_mapping:
            mapped: Dict[str, Any] = {}
            mapped_validation: Dict[str, Any] = {}
            for mp in template_model.output_mapping:
                if mp.source_field in result:
                    mapped[mp.target_field] = result[mp.source_field]
                if mp.source_field in validation:
                    mapped_validation[mp.target_field] = validation[mp.source_field]
            return {
                "fields": mapped,
                "validation": mapped_validation,
                "errors": errors,
            }

        return {
            "fields": result,
            "validation": validation,
            "errors": errors,
        }