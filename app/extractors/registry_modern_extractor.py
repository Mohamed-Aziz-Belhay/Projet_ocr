"""
app/extractors/registry_modern_extractor.py
Fallback specialized extractor for modern Tunisian business registry.
Used ONLY when GenericTemplateExtractor confidence < 0.5 for this variant.

Handles edge-cases specific to this format:
  - Multi-line company names
  - Capital written in words (e.g. "CINQ MILLE DINARS")
  - Two-column layout on some versions
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

from app.extractors.base import BaseExtractor, FieldOutput
from app.core.logging import get_logger

log = get_logger(__name__)

_WORDS_TO_NUM: Dict[str, int] = {
    "mille": 1000, "cinq mille": 5000, "dix mille": 10000,
    "cent mille": 100000, "un million": 1000000,
}


def _parse_capital_words(text: str) -> Optional[str]:
    lower = text.lower()
    for phrase, val in sorted(_WORDS_TO_NUM.items(), key=lambda x: -len(x[0])):
        if phrase in lower:
            return str(val)
    return None


class RegistryModernExtractor(BaseExtractor):
    doc_family = "business_registry"
    variant_id = "registre_commerce_modern"

    def extract(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[FieldOutput]:
        fields: List[FieldOutput] = []

        # ── Registration number ────────────────────────────────────
        m = re.search(r"\b([A-Z]{1,2}[0-9]{6,10}|B\d{6,10})\b", text)
        if m:
            fields.append(self._make_field("registration_number", m.group(1), 0.90, m.group(1)))
        else:
            fields.append(self._make_field("registration_number", None, 0.0, None, False, "Not found"))

        # ── Company name — handle multi-line ─────────────────────
        lines = text.splitlines()
        company = None
        for i, line in enumerate(lines):
            if re.search(r"(dénomination|raison sociale)", line, re.I):
                after = re.split(r":\s*", line, maxsplit=1)
                if len(after) > 1 and len(after[1].strip()) > 2:
                    company = after[1].strip()
                elif i + 1 < len(lines) and lines[i + 1].strip():
                    company = lines[i + 1].strip()
                    # Absorb continuation lines (no anchor keyword)
                    if i + 2 < len(lines) and not re.search(
                        r"(forme|capital|adresse|objet|date)", lines[i + 2], re.I
                    ) and len(lines[i + 2].strip()) > 2:
                        company += " " + lines[i + 2].strip()
                break
        fields.append(self._make_field(
            "company_name", company.upper() if company else None,
            0.82 if company else 0.0, company
        ))

        # ── Legal form ────────────────────────────────────────────
        m = re.search(r"\b(SARL|SA|SUARL|SNC|SCS|GIE)\b", text, re.I)
        fields.append(self._make_field(
            "legal_form", m.group(1).upper() if m else None, 0.88 if m else 0.0
        ))

        # ── Capital — numeric or spelled out ─────────────────────
        m = re.search(r"capital[^:]*:\s*([0-9][\d\s.,]*)\s*(dt|tnd|dinars?)?", text, re.I)
        if m:
            cap = m.group(1).strip().replace(" ", "").replace(",", ".")
            fields.append(self._make_field("capital", cap, 0.85, m.group(0)))
        else:
            # Try words
            cap_words = _parse_capital_words(text)
            fields.append(self._make_field(
                "capital", cap_words, 0.70 if cap_words else 0.0, None,
                cap_words is not None
            ))

        log.debug(
            "RegistryModernExtractor done",
            extra={"fields": len(fields), "variant": self.variant_id},
        )
        return fields
