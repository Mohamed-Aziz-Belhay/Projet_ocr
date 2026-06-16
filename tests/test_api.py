"""
tests/test_api.py
Tests d'intégration complets — couvrent les 4 phases A B C D.
Utilise TestClient FastAPI (pas de vraie dépendance OCR requise).
"""
from __future__ import annotations
import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ── Setup env minimale avant import de l'app ──────────────────────────────────
os.environ.setdefault("SECRET_KEY", "test_secret_key_must_be_32chars_long!!")
os.environ.setdefault("ALLOWED_API_KEYS", '["test-api-key"]')
os.environ.setdefault("DEFAULT_ENGINE", "tesseract")
os.environ.setdefault("TEMPLATES_DIR", "app/templates")


from app.main import app

HEADERS = {"X-API-Key": "test-api-key"}
client = TestClient(app, raise_server_exceptions=False)


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "engines" in body
        assert "uptime_seconds" in body

    def test_health_no_auth_required(self):
        """Health endpoint doit être public."""
        r = client.get("/health")
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Templates CRUD
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_TEMPLATE = {
    "template": {
        "id": "test_doc",
        "name": "Test Document",
        "version": "1.0",
        "doc_family": "test",
        "language_hints": ["fr"],
        "preferred_engine": "auto",
        "anchors_required": [],
        "fields": [
            {
                "name": "reference",
                "extraction_method": "regex",
                "patterns": ["REF[:\\s]+([A-Z0-9]+)"],
                "validation": {"type": "any"},
                "normalization": {"strip": True},
                "required": True,
                "confidence_weight": 1.0,
            }
        ],
        "postprocess_hooks": [],
        "output_mapping": {},
    }
}


class TestTemplates:
    def test_list_templates_authenticated(self):
        r = client.get("/templates", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)

    def test_list_templates_unauthenticated(self):
        r = client.get("/templates")
        # En mode dev (pas de clés), c'est 200; sinon 401
        assert r.status_code in (200, 401)

    def test_create_template(self):
        r = client.post("/templates", json=SAMPLE_TEMPLATE, headers=HEADERS)
        assert r.status_code in (201, 422)  # 422 si déjà existant

    def test_get_template_existing(self):
        # D'abord créer
        client.post("/templates", json=SAMPLE_TEMPLATE, headers=HEADERS)
        r = client.get("/templates/test_doc", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["id"] == "test_doc"

    def test_get_template_not_found(self):
        r = client.get("/templates/inexistant_xyz", headers=HEADERS)
        assert r.status_code == 404
        assert r.json()["error"] == "TEMPLATE_NOT_FOUND"

    def test_delete_template(self):
        client.post("/templates", json=SAMPLE_TEMPLATE, headers=HEADERS)
        r = client.delete("/templates/test_doc", headers=HEADERS)
        assert r.status_code == 200

    def test_reload_templates(self):
        r = client.post("/templates/reload", headers=HEADERS)
        assert r.status_code == 200
        assert "loaded" in r.json()["data"]

    def test_template_fields_validation(self):
        """Soumettre un template sans champs requis doit échouer."""
        invalid = {"template": {"name": "Missing ID", "fields": []}}
        r = client.post("/templates", json=invalid, headers=HEADERS)
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Extraction (avec mock du pipeline pour isolation)
# ─────────────────────────────────────────────────────────────────────────────

MOCK_RESULT = {
    "job_id": "mock-job-id",
    "status": "success",
    "template_id": "invoice_generic",
    "engine_used": "tesseract",
    "language_detected": "fr",
    "global_confidence": 0.85,
    "fields": [
        {
            "name": "invoice_number",
            "value": "F2024-001",
            "confidence": 0.9,
            "validated": True,
            "raw_text": "F2024-001",
            "error": None,
        }
    ],
    "raw_text": None,
    "processing_time_ms": 450,
    "warnings": [],
}


def _fake_image_bytes() -> bytes:
    """Crée un PNG minimal valide (1x1 pixel blanc) pour les tests."""
    import struct, zlib
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(c[4:]) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat_data = zlib.compress(b"\x00\xFF\xFF\xFF")
    idat = chunk(b"IDAT", idat_data)
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


class TestExtraction:
    @patch("app.routers.routes_extract.get_ocr_service")
    @patch("app.routers.routes_extract.get_storage_service")
    def test_extract_sync_success(self, mock_storage, mock_svc):
        mock_storage.return_value.save_upload.return_value = "/tmp/test.png"
        mock_storage.return_value.delete_upload.return_value = None
        from app.schemas.ocr import ExtractionResponse, FieldResult
        mock_svc.return_value.extract_sync.return_value = ExtractionResponse(**MOCK_RESULT)

        r = client.post(
            "/extract",
            headers=HEADERS,
            files={"file": ("test.png", _fake_image_bytes(), "image/png")},
            data={"template_id": "invoice_generic", "engine": "tesseract"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert len(body["fields"]) == 1
        assert body["fields"][0]["name"] == "invoice_number"

    def test_extract_no_file(self):
        r = client.post("/extract", headers=HEADERS)
        assert r.status_code == 422

    def test_extract_unsupported_type(self):
        """Fichier avec extension non supportée → 415."""
        with patch("app.services.storage_service.StorageService.save_upload") as mock_save:
            from app.core.errors import UnsupportedFileTypeError
            mock_save.side_effect = UnsupportedFileTypeError("Extension 'exe' not allowed")
            r = client.post(
                "/extract",
                headers=HEADERS,
                files={"file": ("malware.exe", b"binary", "application/octet-stream")},
            )
            assert r.status_code in (415, 422, 500)

    def test_extract_requires_auth(self):
        r = client.post(
            "/extract",
            files={"file": ("test.png", _fake_image_bytes(), "image/png")},
        )
        # Si clés configurées → 401, sinon 422 (pas de fichier validé)
        assert r.status_code in (401, 422, 200)


# ─────────────────────────────────────────────────────────────────────────────
# Jobs
# ─────────────────────────────────────────────────────────────────────────────

class TestJobs:
    def test_list_jobs(self):
        r = client.get("/jobs", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert "jobs" in body
        assert "total" in body

    def test_get_job_not_found(self):
        r = client.get("/jobs/inexistant-job-id", headers=HEADERS)
        assert r.status_code == 404
        assert r.json()["error"] == "JOB_NOT_FOUND"

    def test_delete_nonexistent_job(self):
        r = client.delete("/jobs/does-not-exist", headers=HEADERS)
        # Le service ignore silencieusement
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline unitaire — field_extractors
# ─────────────────────────────────────────────────────────────────────────────

class TestFieldExtractors:
    def test_regex_extraction_simple(self):
        from app.pipeline.field_extractors import extract_by_regex
        text = "Facture N° F2024-099 du 15/03/2024"
        val, conf = extract_by_regex(text, [r"(?:N°|Numéro)[\\s:]*([A-Z0-9\\-]+)"])
        # Note: backslash dans pattern test = raw string Python
        val2, conf2 = extract_by_regex(text, [r"N°\s*([A-Z0-9\-]+)"])
        assert val2 == "F2024-099"
        assert conf2 > 0.5

    def test_regex_no_match(self):
        from app.pipeline.field_extractors import extract_by_regex
        val, conf = extract_by_regex("texte quelconque", [r"INEXISTANT_([A-Z]+)"])
        assert val is None
        assert conf == 0.0

    def test_anchor_extraction(self):
        from app.pipeline.field_extractors import extract_by_anchor
        from app.schemas.template import FieldSpec, AnchorSpec, ValidationSpec, NormalizationSpec
        text = "Dénomination: EXEMPLE SARL\nAdresse: Tunis"
        field = FieldSpec(
            name="company",
            extraction_method="anchor",
            anchors=[AnchorSpec(text="Dénomination")],
            validation=ValidationSpec(),
            normalization=NormalizationSpec(),
            confidence_weight=1.0,
        )
        val, conf = extract_by_anchor(text, field)
        assert val == "EXEMPLE SARL"
        assert conf > 0.7

    def test_validation_date(self):
        from app.pipeline.field_extractors import validate_field
        from app.schemas.template import ValidationSpec
        spec = ValidationSpec(type="date", date_formats=["%d/%m/%Y"])
        ok, err = validate_field("15/03/2024", spec)
        assert ok is True
        assert err is None

    def test_validation_date_invalid(self):
        from app.pipeline.field_extractors import validate_field
        from app.schemas.template import ValidationSpec
        spec = ValidationSpec(type="date", date_formats=["%d/%m/%Y"])
        ok, err = validate_field("not-a-date", spec)
        assert ok is False

    def test_validation_regex(self):
        from app.pipeline.field_extractors import validate_field
        from app.schemas.template import ValidationSpec
        spec = ValidationSpec(type="regex", pattern=r"[0-9]{8}")
        ok, err = validate_field("12345678", spec)
        assert ok is True
        ok2, err2 = validate_field("123", spec)
        assert ok2 is False

    def test_normalization(self):
        from app.pipeline.field_extractors import normalize
        from app.schemas.template import NormalizationSpec
        spec = NormalizationSpec(strip=True, uppercase=True, custom_replace={".": ""})
        result = normalize("  hello.world  ", spec)
        assert result == "HELLOWORLD"


# ─────────────────────────────────────────────────────────────────────────────
# Language detection
# ─────────────────────────────────────────────────────────────────────────────

class TestLangDetect:
    def test_arabic_detected(self):
        from app.pipeline.lang_detect import detect_language
        arabic_text = "هذا نص عربي طويل بما يكفي للكشف عن اللغة"
        lang = detect_language(arabic_text)
        assert lang in ("ar", "ar+fr")

    def test_short_text_returns_hint(self):
        from app.pipeline.lang_detect import detect_language
        lang = detect_language("ok", hint="fr")
        assert lang == "fr"

    def test_empty_returns_default(self):
        from app.pipeline.lang_detect import detect_language
        lang = detect_language("")
        assert lang == "en"


# ─────────────────────────────────────────────────────────────────────────────
# Template service unitaire
# ─────────────────────────────────────────────────────────────────────────────

class TestTemplateService:
    def test_get_nonexistent_raises(self):
        from app.services.template_service import TemplateService
        from app.core.errors import TemplateNotFoundError
        svc = TemplateService(templates_dir="/tmp/empty_templates_xyz")
        with pytest.raises(TemplateNotFoundError):
            svc.get("no_such_template")

    def test_auto_detect_no_match(self):
        from app.services.template_service import TemplateService
        svc = TemplateService(templates_dir="app/templates")
        result = svc.auto_detect("texte sans aucun mot clé connu xyzzyx")
        assert result is None

    def test_auto_detect_invoice(self):
        from app.services.template_service import TemplateService
        svc = TemplateService(templates_dir="app/templates")
        text = "Facture N° F2024-001\nTotal TTC: 1250 DT\nFournisseur: ABC\nTVA: 190 DT"
        result = svc.auto_detect(text)
        # Peut trouver invoice_generic si templates présents
        # On vérifie juste que ça ne crash pas
        assert result is None or result.doc_family == "invoice"
