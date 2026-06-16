"""
tests/test_services.py
Tests unitaires pour les services, extracteurs, scoring et formatage.
"""
from __future__ import annotations
import os
import json
import tempfile
from typing import List

import pytest

os.environ.setdefault("SECRET_KEY", "test_secret_key_must_be_32chars_long!!")
os.environ.setdefault("ALLOWED_API_KEYS", '["test-api-key"]')
os.environ.setdefault("TEMPLATES_DIR", "app/templates")


# ─────────────────────────────────────────────────────────────────────────────
# Extraction scoring
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractionScoring:

    def _make_fields(self, data: list) -> list:
        from app.schemas.ocr import FieldResult
        return [
            FieldResult(
                name=d["name"], value=d.get("value"),
                confidence=d.get("conf", 0.8),
                validated=d.get("validated", True),
            )
            for d in data
        ]

    def test_global_confidence_equal_weights(self):
        from app.services.extraction_scoring import compute_global_confidence
        fields = self._make_fields([
            {"name": "a", "conf": 0.9},
            {"name": "b", "conf": 0.7},
        ])
        score = compute_global_confidence(fields)
        assert abs(score - 0.8) < 0.001

    def test_global_confidence_empty(self):
        from app.services.extraction_scoring import compute_global_confidence
        assert compute_global_confidence([]) == 0.0

    def test_global_confidence_with_null_values(self):
        from app.services.extraction_scoring import compute_global_confidence
        fields = self._make_fields([
            {"name": "a", "conf": 0.9, "value": "found"},
            {"name": "b", "conf": 0.0, "value": None},
        ])
        score = compute_global_confidence(fields)
        assert score == 0.45   # (0.9 + 0.0) / 2

    def test_extraction_status_success(self):
        from app.services.extraction_scoring import compute_extraction_status
        fields = self._make_fields([
            {"name": "a", "validated": True, "value": "x"},
        ])
        assert compute_extraction_status(fields, []) == "success"

    def test_extraction_status_partial_required_missing(self):
        from app.services.extraction_scoring import compute_extraction_status
        fields = self._make_fields([{"name": "a", "validated": True, "value": "x"}])
        warnings = ["Required field 'b' not found"]
        assert compute_extraction_status(fields, warnings) == "partial"

    def test_extraction_status_failed_no_fields(self):
        from app.services.extraction_scoring import compute_extraction_status
        assert compute_extraction_status([], []) == "failed"

    def test_field_coverage(self):
        from app.services.extraction_scoring import compute_field_coverage
        fields = self._make_fields([
            {"name": "a", "value": "x", "validated": True},
            {"name": "b", "value": None, "validated": False},
        ])
        cov = compute_field_coverage(fields)
        assert cov["total_fields"] == 2
        assert cov["extracted"] == 1
        assert cov["coverage_pct"] == 50.0


# ─────────────────────────────────────────────────────────────────────────────
# Result formatter
# ─────────────────────────────────────────────────────────────────────────────

class TestResultFormatter:

    def test_flat_dict_no_mapping(self):
        from app.services.result_formatter import format_as_flat_dict
        from app.schemas.ocr import FieldResult
        fields = [
            FieldResult(name="invoice_number", value="F001", confidence=0.9, validated=True),
            FieldResult(name="total", value="100", confidence=0.8, validated=True),
        ]
        result = format_as_flat_dict(fields)
        assert result == {"invoice_number": "F001", "total": "100"}

    def test_flat_dict_with_output_mapping(self):
        from app.services.result_formatter import format_as_flat_dict
        from app.schemas.ocr import FieldResult
        from app.schemas.template import TemplateSpec, FieldSpec, ValidationSpec, NormalizationSpec
        fields = [
            FieldResult(name="invoice_number", value="F001", confidence=0.9, validated=True),
        ]
        template = TemplateSpec(
            id="t", name="T", fields=[
                FieldSpec(
                    name="invoice_number",
                    extraction_method="regex",
                    validation=ValidationSpec(),
                    normalization=NormalizationSpec(),
                    confidence_weight=1.0,
                )
            ],
            output_mapping={"invoice_number": "invoiceNumber"},
        )
        result = format_as_flat_dict(fields, template)
        assert "invoiceNumber" in result
        assert result["invoiceNumber"] == "F001"

    def test_null_values_included(self):
        from app.services.result_formatter import format_as_flat_dict
        from app.schemas.ocr import FieldResult
        fields = [FieldResult(name="x", value=None, confidence=0.0, validated=False)]
        result = format_as_flat_dict(fields)
        assert "x" in result
        assert result["x"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Specialized extractors
# ─────────────────────────────────────────────────────────────────────────────

class TestCINExtractor:

    def test_extract_valid_cin(self):
        from app.extractors.cin_extractor import CINExtractor
        text = "Carte d'Identité Nationale\nNom: BEN SALAH\nPrénom: AHMED\n12345678\n01/05/1990  31/12/2030"
        extractor = CINExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("id_number") == "12345678"
        assert field_map.get("last_name") == "BEN SALAH"
        assert field_map.get("first_name") == "AHMED"

    def test_extract_mrz_cin(self):
        from app.extractors.cin_extractor import CINExtractor
        text = "IDTUN123456780TUN9005011M3512315<<<<<<<<<<<2\nBEN SALAH<<AHMED<<<<<<"
        extractor = CINExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("id_number") == "12345678"

    def test_extract_no_cin(self):
        from app.extractors.cin_extractor import CINExtractor
        text = "Texte sans numéro d'identité valide"
        extractor = CINExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("id_number") is None


class TestInvoiceExtractor:

    def test_extract_invoice_number(self):
        from app.extractors.invoice_extractor import InvoiceExtractor
        text = "Facture N° INV-2024-042\nDate: 15/03/2024\nTotal TTC: 1250.000 DT"
        extractor = InvoiceExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("invoice_number") == "INV-2024-042"

    def test_extract_total_amount(self):
        from app.extractors.invoice_extractor import InvoiceExtractor
        text = "Sous-total: 1000.000\nTVA 19%: 190.000\nTotal TTC: 1190.000 DT"
        extractor = InvoiceExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("amount_incl_tax") == "1190.000"

    def test_extract_currency(self):
        from app.extractors.invoice_extractor import InvoiceExtractor
        text = "Montant: 500.000 DT\nRéférence facture: F-001"
        extractor = InvoiceExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("currency") == "DT"


class TestRegistryModernExtractor:

    def test_extract_legal_form(self):
        from app.extractors.registry_modern_extractor import RegistryModernExtractor
        text = "Registre de Commerce\nForme juridique: SARL\nCapital social: 10000 DT"
        extractor = RegistryModernExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("legal_form") == "SARL"

    def test_extract_capital_numeric(self):
        from app.extractors.registry_modern_extractor import RegistryModernExtractor
        text = "Capital social: 50000.000 DT\nDénomination: TEST SARL"
        extractor = RegistryModernExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("capital") == "50000.000"

    def test_extract_capital_words(self):
        from app.extractors.registry_modern_extractor import RegistryModernExtractor
        text = "Capital: DIX MILLE DINARS\nDénomination: EXEMPLE"
        extractor = RegistryModernExtractor()
        fields = extractor.extract(text)
        field_map = {f.name: f.value for f in fields}
        assert field_map.get("capital") == "10000"


# ─────────────────────────────────────────────────────────────────────────────
# Runtime config
# ─────────────────────────────────────────────────────────────────────────────

class TestRuntimeConfig:

    def test_get_default(self):
        from app.config.runtime import RuntimeConfig
        cfg = RuntimeConfig()
        assert cfg.fast_mode is False
        assert cfg.maintenance_mode is False

    def test_set_known_key(self):
        from app.config.runtime import RuntimeConfig
        cfg = RuntimeConfig()
        cfg.set("global_fast_mode", True)
        assert cfg.fast_mode is True
        cfg.set("global_fast_mode", False)

    def test_set_unknown_key_raises(self):
        from app.config.runtime import RuntimeConfig
        cfg = RuntimeConfig()
        with pytest.raises(ValueError, match="Unknown runtime config key"):
            cfg.set("non_existent_key_xyz", 42)

    def test_set_many(self):
        from app.config.runtime import RuntimeConfig
        cfg = RuntimeConfig()
        cfg.set_many({"global_fast_mode": True, "max_pages_per_doc": 5})
        assert cfg.get("global_fast_mode") is True
        assert cfg.get("max_pages_per_doc") == 5
        cfg.reset()

    def test_reset(self):
        from app.config.runtime import RuntimeConfig
        cfg = RuntimeConfig()
        cfg.set("global_fast_mode", True)
        cfg.reset()
        assert cfg.fast_mode is False


# ─────────────────────────────────────────────────────────────────────────────
# OCR Profiles
# ─────────────────────────────────────────────────────────────────────────────

class TestOCRProfiles:

    def test_list_profiles(self):
        from app.config.ocr_profiles import list_profiles, PROFILES
        profiles = list_profiles()
        assert len(profiles) == len(PROFILES)
        names = [p["name"] for p in profiles]
        assert "arabic" in names
        assert "fast" in names
        assert "photo" in names

    def test_get_profile(self):
        from app.config.ocr_profiles import get_profile
        p = get_profile("arabic")
        assert p.engine == "paddle"
        assert p.language == "ar"

    def test_get_unknown_falls_back(self):
        from app.config.ocr_profiles import get_profile
        p = get_profile("non_existent_profile")
        assert p.name == "clean_scan"

    def test_profile_for_language_arabic(self):
        from app.config.ocr_profiles import profile_for_language
        p = profile_for_language("ar")
        assert p.name == "arabic"

    def test_profile_for_language_mixed(self):
        from app.config.ocr_profiles import profile_for_language
        p = profile_for_language("ar+fr")
        assert p.name == "arabic_french"

    def test_profile_for_language_none(self):
        from app.config.ocr_profiles import profile_for_language
        p = profile_for_language(None)
        assert p.name == "clean_scan"


# ─────────────────────────────────────────────────────────────────────────────
# Layout variant classifier
# ─────────────────────────────────────────────────────────────────────────────

class TestLayoutVariantClassifier:

    def test_detect_registry_modern(self):
        from app.classifiers.layout_variant_classifier import classify_variant
        text = "Registre de commerce\nForme juridique: SARL\nCapital social: 10000\nSiège social: Tunis"
        variant, conf = classify_variant("business_registry", text)
        assert variant == "registre_commerce_modern"
        assert conf > 0.5

    def test_detect_registry_arabic(self):
        from app.classifiers.layout_variant_classifier import classify_variant
        text = "السجل التجاري\nالشكل القانوني: شركة\nرأس المال: 10000"
        variant, conf = classify_variant("business_registry", text)
        assert variant == "registre_commerce_legacy_ar"

    def test_unknown_family_returns_generic(self):
        from app.classifiers.layout_variant_classifier import classify_variant
        variant, conf = classify_variant("unknown_family_xyz", "any text")
        assert variant == "unknown_family_xyz_generic"
        assert conf == 0.0

    def test_invoice_variant_detected(self):
        from app.classifiers.layout_variant_classifier import classify_variant
        text = "Facture commerciale\nTotal TTC: 1000 DT\nMontant HT: 840 DT"
        variant, conf = classify_variant("invoice", text)
        assert variant == "invoice_modern"


# ─────────────────────────────────────────────────────────────────────────────
# Template registry
# ─────────────────────────────────────────────────────────────────────────────

class TestTemplateRegistry:

    def test_generic_extractor_returned_with_template(self):
        from app.services.template_registry import get_extractor_for
        from app.extractors.generic_template_extractor import GenericTemplateExtractor
        from app.services.template_service import TemplateService
        svc = TemplateService("app/templates")
        template = svc.get("invoice_generic")
        extractor = get_extractor_for("invoice", "invoice_modern", template, generic_confidence=0.9)
        assert isinstance(extractor, GenericTemplateExtractor)

    def test_specialized_fallback_selected_on_low_confidence(self):
        from app.services.template_registry import get_extractor_for
        from app.extractors.invoice_extractor import InvoiceExtractor
        from app.services.template_service import TemplateService
        svc = TemplateService("app/templates")
        template = svc.get("invoice_generic")
        extractor = get_extractor_for("invoice", "invoice_modern", template, generic_confidence=0.2)
        assert isinstance(extractor, InvoiceExtractor)

    def test_noop_when_no_template_no_match(self):
        from app.services.template_registry import get_extractor_for, _NoOpExtractor
        extractor = get_extractor_for("completely_unknown_family", None, None, 0.0)
        assert isinstance(extractor, _NoOpExtractor)


# ─────────────────────────────────────────────────────────────────────────────
# Generic extraction service (integration)
# ─────────────────────────────────────────────────────────────────────────────

class TestGenericExtractionService:

    def test_extract_with_template(self):
        from app.services.generic_extraction_service import GenericExtractionService
        from app.services.template_service import TemplateService
        svc = GenericExtractionService()
        template_svc = TemplateService("app/templates")
        template = template_svc.get("invoice_generic")
        text = "Facture N° F2024-042\nDate: 15/03/2024\nTotal TTC: 1250.000 DT"
        fields = svc.extract(text, template, doc_family="invoice")
        names = [f.name for f in fields]
        assert "invoice_number" in names

    def test_extract_empty_text_returns_empty(self):
        from app.services.generic_extraction_service import GenericExtractionService
        svc = GenericExtractionService()
        fields = svc.extract("", None)
        assert fields == []

    def test_merge_results_fills_nulls(self):
        from app.services.generic_extraction_service import _merge_results
        from app.extractors.base import FieldOutput
        primary = [FieldOutput("a", None, 0.0, False, None, "not found")]
        fallback = [FieldOutput("a", "found_value", 0.75, True)]
        merged = _merge_results(primary, fallback)
        assert merged[0].value == "found_value"
        assert merged[0].confidence < 0.75  # penalty applied


# ─────────────────────────────────────────────────────────────────────────────
# Admin API routes
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminRoutes:
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    HEADERS = {"X-API-Key": "test-api-key"}

    def test_get_config(self):
        r = self.client.get("/admin/config", headers=self.HEADERS)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "global_fast_mode" in data
        assert "maintenance_mode" in data

    def test_update_config(self):
        r = self.client.patch(
            "/admin/config",
            json={"global_fast_mode": True},
            headers=self.HEADERS,
        )
        assert r.status_code == 200
        # Reset
        self.client.post("/admin/config/reset", headers=self.HEADERS)

    def test_update_unknown_key(self):
        r = self.client.patch(
            "/admin/config",
            json={"totally_unknown_key": 42},
            headers=self.HEADERS,
        )
        assert r.status_code in (422, 500)

    def test_reset_config(self):
        self.client.patch("/admin/config", json={"max_pages_per_doc": 99}, headers=self.HEADERS)
        r = self.client.post("/admin/config/reset", headers=self.HEADERS)
        assert r.status_code == 200
        assert r.json()["data"]["max_pages_per_doc"] == 20

    def test_get_engines(self):
        r = self.client.get("/admin/engines", headers=self.HEADERS)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "engines" in data
        assert "available_count" in data

    def test_get_profiles(self):
        r = self.client.get("/admin/profiles", headers=self.HEADERS)
        assert r.status_code == 200
        profiles = r.json()["data"]
        assert len(profiles) >= 5

    def test_get_profile_valid(self):
        r = self.client.get("/admin/profiles/arabic", headers=self.HEADERS)
        assert r.status_code == 200
        assert r.json()["data"]["name"] == "arabic"

    def test_get_profile_invalid(self):
        r = self.client.get("/admin/profiles/does_not_exist", headers=self.HEADERS)
        assert r.status_code == 500  # OCRServiceError

    def test_maintenance_toggle(self):
        r = self.client.post(
            "/admin/maintenance",
            json={"enabled": True, "message": "Test maintenance"},
            headers=self.HEADERS,
        )
        assert r.status_code == 200
        # Immediately disable
        self.client.post(
            "/admin/maintenance",
            json={"enabled": False, "message": ""},
            headers=self.HEADERS,
        )
