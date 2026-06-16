"""
app/services/template_registry.py

Deux rôles :
1) registre extracteurs spécialisés / génériques
2) registre de résolution template par famille documentaire pour le routage
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.extractors.base import BaseExtractor
try:
    from app.extractors.cin_extractor_legacy import CINExtractor
except Exception:
    from app.extractors.cin_extractor import CINExtractor
from app.extractors.generic_template_extractor import GenericTemplateExtractor
from app.extractors.invoice_extractor import InvoiceExtractor
from app.extractors.passport_extractor import PassportExtractor
from app.extractors.registre_commerce_extractor import RegistryLegacyArExtractor
from app.extractors.registry_modern_extractor import RegistryModernExtractor
from app.schemas.template import TemplateSpec
from app.services.template_service import get_template_service

log = get_logger(__name__)

_FALLBACK_EXTRACTORS: List[BaseExtractor] = [
    RegistryModernExtractor(),
    RegistryLegacyArExtractor(),
    PassportExtractor(),
    CINExtractor(),
    InvoiceExtractor(),
]

_FALLBACK_THRESHOLD = 0.50


class _NoOpExtractor(BaseExtractor):
    doc_family = "generic"
    variant_id = "noop"

    def extract(self, text, metadata=None):
        return []


def get_extractor_for(
    doc_family: str,
    variant_id: Optional[str],
    template: Optional[TemplateSpec],
    generic_confidence: float = 1.0,
) -> BaseExtractor:
    if template and generic_confidence >= _FALLBACK_THRESHOLD:
        return GenericTemplateExtractor(template)
    for extractor in _FALLBACK_EXTRACTORS:
        if extractor.can_handle(doc_family, variant_id):
            log.info("Using specialized fallback extractor", extra={"extractor": extractor.__class__.__name__, "generic_conf": round(generic_confidence, 3)})
            return extractor
    if template:
        return GenericTemplateExtractor(template)
    return _NoOpExtractor()


_CLASS_TO_TEMPLATE_HINTS: Dict[str, List[str]] = {
    "alb_id": ["alb_id"],
    "esp_id": ["esp_id"],
    "est_id": ["est_id"],
    "fin_id": ["fin_id"],
    "svk_id": ["svk_id"],
    "aze_passport": ["aze_passport"],
    "grc_passport": ["grc_passport"],
    "lva_passport": ["lva_passport"],
    "srb_passport": ["srb_passport"],
    "rus_internalpassport": ["rus_internalpassport"],
}


class TemplateRegistry:
    def __init__(self):
        self.templates = get_template_service()

    def get_template(self, template_id: str) -> TemplateSpec:
        return self.templates.get(template_id)

    def list_all(self) -> List[TemplateSpec]:
        items: List[TemplateSpec] = []
        for item in self.templates.list_all():
            try:
                items.append(self.templates.get(item.id))
            except Exception:
                pass
        return items

    def find_templates_by_family(self, doc_family: Optional[str]) -> List[TemplateSpec]:
        if not doc_family:
            return []
        return [tpl for tpl in self.list_all() if tpl.doc_family == doc_family]

    def _safe_single_template_for_family(self, doc_family: Optional[str], templates: List[TemplateSpec]) -> Optional[str]:
        if not templates:
            return None
        if len(templates) == 1:
            if doc_family == "id_document":
                return None
            return templates[0].id
        return None

    def resolve_template_for_family(self, *, doc_family: Optional[str], predicted_class: Optional[str] = None, confidence: float = 0.0) -> Tuple[Optional[str], List[str], List[str]]:
        reasons: List[str] = []
        family_templates = self.find_templates_by_family(doc_family)
        candidate_ids = [tpl.id for tpl in family_templates]
        if not family_templates:
            reasons.append("no templates for predicted family")
            return None, [], reasons
        safe = self._safe_single_template_for_family(doc_family, family_templates)
        if safe:
            reasons.append("single safe template resolved from family")
            return safe, candidate_ids, reasons
        if len(family_templates) > 1:
            reasons.append("multiple templates available for family")
        if doc_family == "id_document":
            reasons.append("id_document family requires OCR/template anchors before final template choice")
            return None, candidate_ids, reasons
        if predicted_class:
            hinted = _CLASS_TO_TEMPLATE_HINTS.get(predicted_class, [])
            for tpl in family_templates:
                if tpl.id in hinted:
                    reasons.append("template selected from class hint")
                    return tpl.id, candidate_ids, reasons
        return None, candidate_ids, reasons

    def resolve_best_template_for_family_and_text(self, *, doc_family: Optional[str], ocr_hint_text: str = "") -> Optional[str]:
        candidates = self.find_templates_by_family(doc_family)
        if not candidates:
            return None
        if len(candidates) == 1 and doc_family != "id_document":
            return candidates[0].id
        text_norm = (ocr_hint_text or "").lower()
        best_tpl: Optional[TemplateSpec] = None
        best_score = 0
        for tpl in candidates:
            score = 0
            for anchor in tpl.anchors_required or []:
                if anchor.lower() in text_norm or anchor in ocr_hint_text:
                    score += 3
            for field in tpl.fields:
                for a in field.anchors:
                    if a.text.lower() in text_norm or a.text in ocr_hint_text:
                        score += 1
            if score > best_score:
                best_score = score
                best_tpl = tpl
        return best_tpl.id if best_tpl and best_score >= 2 else None


_registry: Optional[TemplateRegistry] = None


def get_template_registry() -> TemplateRegistry:
    global _registry
    if _registry is None:
        _registry = TemplateRegistry()
    return _registry
