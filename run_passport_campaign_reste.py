"""
run_passport_campaign_reste.py

Reprend le principe de run_passport_campaign.py (authentification
intégrée, POST /extract synchrone -- le même chemin API que l'interface
web utilise), en excluant les 4 passeports déjà testés manuellement
depuis l'interface (voir EXCLUDE_FILES ci-dessous).

Prérequis :
    pip install requests

Avant de lancer :
    1. Backend démarré.
    2. Renseignez EMAIL / PASSWORD ci-dessous.

Sortie :
    - ResultatTest/responses/passport_tn_reste/<nom>.json
    - ResultatTest/passport_tn_reste_results.json (résumé)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ============================================================
#  CONFIGURATION
# ============================================================

BASE_URL = "http://localhost:8000"
EMAIL = "test20@gmail.com"
PASSWORD = "TEST1230"

PASSPORT_FOLDER = r"C:\Users\Belha\OneDrive\Bureau\DataSetOCR\Passport"

# Les 4 cas déjà testés manuellement depuis l'interface, à exclure.
# "01" est inclus sous ses deux graphies possibles (01.jpg / pass01.jpg)
# selon la convention réellement utilisée dans votre dossier -- les deux
# sont tolérées, celle qui ne correspond à aucun fichier est simplement
# ignorée sans erreur.
EXCLUDE_FILES = {
    "01.jpg", "pass01.jpg",
    "pass51.jpg",
    "pass29.jpg",
    "pass98.jpg",
}

DOCUMENT_TYPE = "passport"
TEMPLATE_ID = "passport_generic"

CRITICAL_FIELDS = [
    "document_number", "surname", "given_names", "nationality",
    "birth_date", "expiry_date", "mrz",
]

OUTPUT_DIR = Path("ResultatTest")
RESPONSES_DIR = OUTPUT_DIR / "responses" / "passport_tn_reste"
SUMMARY_FILE = OUTPUT_DIR / "passport_tn_reste_results.json"

REQUEST_TIMEOUT_S = 300  # généreux, cf. leçon tirée des campagnes précédentes

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


def list_images(folder: str, exclude: set[str]) -> List[Path]:
    base = Path(folder)
    if not base.is_dir():
        raise FileNotFoundError(f"Dossier introuvable : {folder}")

    files = sorted(
        [
            p for p in base.iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            and p.name not in exclude
        ],
        key=lambda p: p.stem,
    )
    return files


def extract_document(base_url: str, token: str, file_path: Path) -> tuple[int, Dict[str, Any], Optional[str]]:
    url = f"{base_url}/extract"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "application/octet-stream")}
            data = {"document_type": DOCUMENT_TYPE, "template_id": TEMPLATE_ID}
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=REQUEST_TIMEOUT_S)
    except requests.exceptions.RequestException as exc:
        return 0, {}, str(exc)

    try:
        body = resp.json()
    except ValueError:
        body = {"raw_text": resp.text}

    return resp.status_code, body, None


def build_case_result(file_path: Path, http_status: int, body: Dict[str, Any], request_error: Optional[str], response_file: Path) -> Dict[str, Any]:
    case_id = f"passport_tn__{file_path.stem}"
    http_ok = 200 <= http_status < 300

    result: Dict[str, Any] = {
        "case_id": case_id,
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

    critical_valid_count = sum(1 for name in CRITICAL_FIELDS if fields_by_name.get(name, {}).get("validated") is True)
    missing = [
        f"{name}:{fields_by_name.get(name, {}).get('error') or 'missing_field'}"
        for name in CRITICAL_FIELDS
        if fields_by_name.get(name, {}).get("validated") is not True
    ]

    result.update({
        "api_status": body.get("status"),
        "critical_valid_count": critical_valid_count,
        "critical_total": len(CRITICAL_FIELDS),
        "critical_missing": "; ".join(missing),
        "critical_pass": critical_valid_count == len(CRITICAL_FIELDS),
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

    images = list_images(PASSPORT_FOLDER, EXCLUDE_FILES)
    print(f"{len(images)} image(s) à tester (4 cas déjà testés exclus).\n")

    summary: List[Dict[str, Any]] = []

    for i, file_path in enumerate(images, start=1):
        print(f"[{i}/{len(images)}] {file_path.name} ...", end=" ", flush=True)

        t0 = time.time()
        http_status, body, request_error = extract_document(BASE_URL, token, file_path)
        elapsed_s = time.time() - t0

        response_file = RESPONSES_DIR / f"passport_tn__{file_path.stem}.json"
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2, default=str)

        case_result = build_case_result(file_path, http_status, body, request_error, response_file)
        summary.append(case_result)

        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

        status_label = "OK (strict)" if case_result.get("critical_pass") else "review/fail"
        print(f"http={http_status} -> {status_label} ({elapsed_s:.1f}s)")

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