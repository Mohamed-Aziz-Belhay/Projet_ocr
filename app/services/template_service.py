"""
app/services/template_service.py
Chargement, validation, CRUD des templates YAML/JSON.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from app.core.errors import TemplateNotFoundError, TemplateValidationError
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.schemas.template import TemplateListItem, TemplateSpec

log      = get_logger(__name__)
settings = get_settings()

_AUTO_DETECT_ANCHORS = {
    "cin_tn": [
        "اللقب", "الاسم", "تاريخ الولادة",
        "بطاقة التعريف الوطنية", "الجمهورية التونسية",
    ],
    "invoice_tn": ["Total TTC", "Facture", "TVA", "Total HT"],
    "registre_commerce_tn": [
        "السجل التجاري", "الاسم التجاري", "رأس المال", "الشركة",
    ],
    "invoice_generic": ["Invoice", "Total", "VAT"],
    "aze_passport": [
        "PASSPORT", "Passport No", "Date of birth",
        "Date of expiry", "MINISTRY OF INTERNAL AFFAIRS",
    ],
    "est_id": ["ISIKUKOOD", "SURNAME", "GIVEN NAMES", "SEX", "CARD NO"],
}


class TemplateService:
    def __init__(self, templates_dir: Optional[str] = None):
        self._dir   = Path(templates_dir or settings.TEMPLATES_DIR)
        self._cache: Dict[str, TemplateSpec] = {}
        self._load_all()

    def _load_all(self) -> None:
        self._cache.clear()
        if not self._dir.exists():
            log.warning("Répertoire templates introuvable", extra={"dir": str(self._dir)})
            return
        count = 0
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                spec = self._parse_file(path)
                self._cache[spec.id] = spec
                count += 1
            except Exception as exc:
                log.error("Échec chargement template", extra={"file": path.name, "error": str(exc)})
        log.info(f"{count} templates chargés", extra={"dir": str(self._dir)})

    def _parse_file(self, path: Path) -> TemplateSpec:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return TemplateSpec(**data)

    def _save_file(self, spec: TemplateSpec) -> None:
        path = self._dir / f"{spec.id}.yaml"
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(spec.model_dump(), f, allow_unicode=True, sort_keys=False)

    def get(self, template_id: str) -> TemplateSpec:
        spec = self._cache.get(template_id)
        if not spec:
            raise TemplateNotFoundError(
                f"Template '{template_id}' introuvable",
                available=list(self._cache.keys()),
            )
        return spec

    def list_all(self) -> List[TemplateListItem]:
        return [
            TemplateListItem(
                id=s.id, name=s.name, version=s.version,
                doc_family=s.doc_family, field_count=len(s.fields), pipeline=s.pipeline,
            )
            for s in self._cache.values()
        ]

    def create(self, spec: TemplateSpec) -> TemplateSpec:
        if spec.id in self._cache:
            raise TemplateValidationError(f"Template '{spec.id}' existe déjà")
        self._cache[spec.id] = spec
        self._save_file(spec)
        return spec

    def update(self, template_id: str, spec: TemplateSpec) -> TemplateSpec:
        if template_id not in self._cache:
            raise TemplateNotFoundError(f"Template '{template_id}' introuvable")
        if spec.id != template_id:
            raise TemplateValidationError("L'ID dans le body doit correspondre à l'URL")
        self._cache[template_id] = spec
        self._save_file(spec)
        return spec

    def delete(self, template_id: str) -> None:
        if template_id not in self._cache:
            raise TemplateNotFoundError(f"Template '{template_id}' introuvable")
        path = self._dir / f"{template_id}.yaml"
        if path.exists():
            path.unlink()
        del self._cache[template_id]

    def reload(self) -> int:
        self._load_all()
        return len(self._cache)

    def auto_detect(self, text: str) -> Optional[TemplateSpec]:
        scores: Dict[str, int] = {}
        text_norm = text.lower()
        for tid, spec in self._cache.items():
            score = 0
            for anchor in spec.anchors_required:
                if anchor.lower() in text_norm or anchor in text:
                    score += 3
            for field in spec.fields:
                for a in field.anchors:
                    if a.text.lower() in text_norm or a.text in text:
                        score += 1
            for anchor in _AUTO_DETECT_ANCHORS.get(tid, []):
                if anchor.lower() in text_norm or anchor in text:
                    score += 2
            scores[tid] = score
        best_id    = max(scores, key=lambda k: scores[k], default=None)
        best_score = scores.get(best_id, 0) if best_id else 0
        if best_score < 3:
            return None
        return self._cache[best_id]


_service: Optional[TemplateService] = None


def get_template_service() -> TemplateService:
    global _service
    if _service is None:
        _service = TemplateService()
    return _service