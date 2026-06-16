# Plan implémenté — fichier par fichier

## Schémas / contrats
- `app/schemas/ocr.py` : ajout de `review_required`, `normalized_data`, `diagnostics`, `include_diagnostics`.
- `app/schemas/template.py` : ajout de `pipeline`, `fixed_zones`, `engines`, `field_policies`, `review_policy`.

## Configuration
- `app/core/settings.py` : ajouts des switches multi-engine / review et fallback API key locale.

## OCR / moteurs
- `app/engines/engine_factory.py` : normalisation `recognize_document()` / `recognize_zones()`.
- `app/engines/paddle_engine.py` : meilleure gestion `ar+fr`.

## Prétraitement / localisation
- `app/pipeline/preprocess.py` : bundle enrichi (`quality`, `variants`, `transforms`).
- `app/pipeline/cin_localizer.py` : zones fixes + raffinement par ancres.

## Extraction métier
- `app/extractors/cin_extractor.py` : candidats par champ pour `cin_tn`.
- `app/services/field_resolver.py` : scoring métier / sélection par champ.
- `app/services/extraction_scoring.py` : statut final incluant `review_required`.

## Orchestration
- `app/services/engine_selector.py` : plan primaire / secondaire / fallback numérique.
- `app/pipeline/runner.py` : pipeline générique + pipeline spécialisé CIN.
- `app/services/document_orchestrator.py` : orchestration alignée avec le nouveau runner.
- `app/services/ocr_service.py` : propagation `include_diagnostics`.

## Templates / API / UI
- `app/services/template_service.py` : templates enrichis avec `pipeline`.
- `app/templates/cin_tn.yaml` : politique complète `cin_specialized_v1`.
- `app/routers/routes_extract.py` : option `include_diagnostics`.
- `app/static/index.html` : support visuel de `review_required`.

## Documentation embarquée
- `IMPLEMENTATION_ARCHITECTURE.md`
- `IMPLEMENTATION_CHECKLIST.md`
- `IMPLEMENTATION_FILE_BY_FILE.md`
