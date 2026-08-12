"""
run_invoice_logistics_campaign.py

Revalidation des factures invoice_logistics_intl apres correctif de
invoice_extractor.py (motifs TOTAL/date internationaux + separateurs
de milliers).

Reprend la meme methodologie que la campagne d'origine
(invoice_logistics_results.json), pour un resultat directement
comparable : 3 champs critiques -- invoice_number, invoice_date,
total_ttc (cf. generic_runner.py, critical_fields).

Prerequis :
    pip install requests

Avant de lancer :
    1. Backend demarre, avec invoice_extractor.py remplace par la
       version corrigee.
    2. Renseignez BASE_URL, EMAIL, PASSWORD, INVOICE_FOLDER et
       INVOICE_FILES ci-dessous.

Sortie :
    - ResultatTest/responses/invoice_logistics_recheck/<nom>.json
    - ResultatTest/invoice_logistics_recheck_results.json (resume)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ============================================================
#  CONFIGURATION — à adapter avant de lancer
# ============================================================

BASE_URL = "http://localhost:8000"
EMAIL = "test20@gmail.com"
PASSWORD = "TEST1230"

# Dossier contenant les factures (chemin vu dans vos données d'origine)
INVOICE_FOLDER = r"C:\Users\Belha\OneDrive\Bureau\DataSetOCR\Facture"

# Les 6 cas exacts de la campagne d'origine (invoice_logistics_results.json)
INVOICE_FILES = [
    "facture2.jpg",
    "facture4.jpg",
    "facture5.jpg",
    "facture6.jpg",
    "facture7.jpg",
    "facture8.jpg",
]

DOCUMENT_TYPE = "invoice"
TEMPLATE_ID = "invoice_logistics_intl"

# Les 3 champs critiques (cf. generic_runner.py, critical_fields =
# {"invoice_number", "invoice_date", "total_ttc"}, deja confirme dans
# vos donnees d'origine avec critical_total=3)
CRITICAL_FIELDS = ["invoice_number", "invoice_date", "total_ttc"]

OUTPUT_DIR = Path("ResultatTest")
RESPONSES_DIR = OUTPUT_DIR / "responses" / "invoice_logistics_recheck"
SUMMARY_FILE = OUTPUT_DIR / "invoice_logistics_recheck_results.json"

REQUEST_TIMEOUT_S = 120

# ============================================================


def login(base_url: str, email: str, password: str) -> str:
    resp = requests.post(
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = (
        data.get("access_token")
        or data.get("data", {}).get("access_token")
        or data.get("token")
    )
    if not token:
        raise RuntimeError(f"Impossible de trouver le token dans la réponse login : {data}")
    return token


def extract_document(
    base_url: str,
    token: str,
    file_path: Path,
) -> tuple[int, Dict[str, Any], Optional[str]]:
    url = f"{base_url}/extract"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "application/octet-stream")}
            data = {
                "document_type": DOCUMENT_TYPE,
                "template_id": TEMPLATE_ID,
            }
            resp = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=REQUEST_TIMEOUT_S,
            )
    except requests.exceptions.RequestException as exc:
        return 0, {}, str(exc)

    try:
        body = resp.json()
    except ValueError:
        body = {"raw_text": resp.text}

    return resp.status_code, body, None


def build_case_result(
    file_path: Path,
    http_status: int,
    body: Dict[str, Any],
    request_error: Optional[str],
    response_file: Path,
) -> Dict[str, Any]:
    case_id = f"invoice_logistics__{file_path.stem}"
    http_ok = 200 <= http_status < 300

    result: Dict[str, Any] = {
        "case_id": case_id,
        "family": "invoice_logistics",
        "file": str(file_path),
        "http_status": http_status,
        "http_ok": http_ok,
        "response_file": str(response_file),
    }

    if not http_ok:
        result.update({
            "api_status": None,
            "critical_valid_count": 0,
            "critical_total": len(CRITICAL_FIELDS),
            "critical_missing": "",
            "critical_pass": False,
            "error": str(body.get("detail", body)) if body else request_error,
        })
        return result

    fields = body.get("fields", []) or []
    fields_by_name = {f.get("name"): f for f in fields if isinstance(f, dict)}

    critical_valid_count = sum(
        1 for name in CRITICAL_FIELDS
        if fields_by_name.get(name, {}).get("validated") is True
    )
    missing = [
        f"{name}:{fields_by_name.get(name, {}).get('error') or 'missing_field'}"
        for name in CRITICAL_FIELDS
        if fields_by_name.get(name, {}).get("validated") is not True
    ]

    # Valeurs des 3 champs critiques, pour comparaison visuelle rapide
    values = {
        name: fields_by_name.get(name, {}).get("value")
        for name in CRITICAL_FIELDS
    }

    result.update({
        "api_status": body.get("status"),
        "critical_valid_count": critical_valid_count,
        "critical_total": len(CRITICAL_FIELDS),
        "critical_missing": "; ".join(missing),
        "critical_pass": critical_valid_count == len(CRITICAL_FIELDS),
        "values": values,
        "global_confidence": body.get("global_confidence"),
        "processing_time_ms": body.get("processing_time_ms"),
        "error": None,
    })

    return result


def main() -> None:
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Connexion à {BASE_URL} ...")
    token = login(BASE_URL, EMAIL, PASSWORD)
    print("Authentification réussie.\n")

    base = Path(INVOICE_FOLDER)
    images = [base / name for name in INVOICE_FILES]

    missing_files = [p for p in images if not p.is_file()]
    if missing_files:
        print("ATTENTION, fichiers introuvables (vérifiez INVOICE_FOLDER/INVOICE_FILES) :")
        for p in missing_files:
            print(f"  - {p}")
        print()

    images = [p for p in images if p.is_file()]
    print(f"{len(images)} facture(s) à tester.\n")

    summary: List[Dict[str, Any]] = []

    for i, file_path in enumerate(images, start=1):
        print(f"[{i}/{len(images)}] {file_path.name} ...", end=" ", flush=True)

        t0 = time.time()
        http_status, body, request_error = extract_document(BASE_URL, token, file_path)
        elapsed_s = time.time() - t0

        response_file = RESPONSES_DIR / f"invoice_logistics__{file_path.stem}.json"
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2, default=str)

        case_result = build_case_result(file_path, http_status, body, request_error, response_file)
        summary.append(case_result)

        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

        status_label = "OK (strict)" if case_result.get("critical_pass") else "review/fail"
        vals = case_result.get("values", {})
        print(f"http={http_status} -> {status_label} ({elapsed_s:.1f}s)  "
              f"[num={vals.get('invoice_number')!r} date={vals.get('invoice_date')!r} "
              f"ttc={vals.get('total_ttc')!r}]")

        time.sleep(0.3)

    n = len(summary)
    strict_pass = sum(1 for c in summary if c.get("critical_pass"))
    print("\n" + "=" * 50)
    print(f"Total : {n} documents")
    if n:
        print(f"Réussite stricte : {strict_pass}/{n} ({100 * strict_pass / n:.1f}\u202f%)")
    print(f"Résumé écrit dans : {SUMMARY_FILE.resolve()}")
    print("=" * 50)


if __name__ == "__main__":
    main()