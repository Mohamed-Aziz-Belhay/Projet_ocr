"""
tests/conftest.py — Enterprise Edition
Async fixtures with SQLite in-memory. No Postgres required.
"""
from __future__ import annotations
import asyncio, os, struct, zlib
import pytest
import pytest_asyncio

os.environ.setdefault("SECRET_KEY",          "test_secret_key_must_be_32chars_long!!")
os.environ.setdefault("ALLOWED_API_KEYS",    '["test-api-key"]')
os.environ.setdefault("SUPER_ADMIN_KEY",     "super-admin-test-key")
os.environ.setdefault("TEMPLATES_DIR",       "app/templates")
os.environ.setdefault("UPLOAD_DIR",          "/tmp/ocr_test_uploads")
os.environ.setdefault("RESULT_DIR",          "/tmp/ocr_test_results")
os.environ.setdefault("DATABASE_URL",        "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("RATE_LIMIT_ENABLED",  "false")
os.environ.setdefault("ENCRYPT_STORED_FILES","false")
os.environ.setdefault("ENVIRONMENT",         "test")
os.environ.setdefault("LOG_JSON",            "false")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def api_headers():
    return {"X-API-Key": "test-api-key"}


@pytest.fixture(scope="session")
def super_admin_headers():
    return {"X-Super-Admin-Key": "super-admin-test-key"}


@pytest.fixture(scope="session")
def test_client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture(scope="session")
def minimal_png_bytes() -> bytes:
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(c[4:]) & 0xFFFFFFFF)
    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xFF\xFF\xFF"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


@pytest.fixture(scope="session")
def sample_invoice_text():
    return (
        "Facture N° INV-2024-0042\nDate: 15/03/2024\n"
        "Fournisseur: EXEMPLE TECH SARL\nClient: ACME\n"
        "Total TTC: 1249.500 DT\nTVA 19%: 199.500 DT\n"
    )


@pytest.fixture(scope="session")
def sample_cin_text():
    return (
        "Carte d'Identité Nationale Tunisienne\n"
        "Nom: BEN SALAH\nPrénom: AHMED\n12345678\n"
        "Date de naissance: 01/05/1990\nValable jusqu'au: 31/12/2030\n"
    )
