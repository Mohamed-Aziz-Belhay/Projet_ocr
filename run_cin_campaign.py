"""
run_cin_campaign.py

Script de campagne de test — CIN tunisiennes.

Contrairement à app/evaluation/benchmark_runner.py (qui appelle
run_pipeline() directement en Python, sans passer par l'API), ce script
fait de VRAIES requêtes HTTP vers POST /extract, exactement comme le
ferait le frontend Angular ou Postman. C'est la seule façon de capturer
les mêmes informations que vos fichiers ResultatTest/*.json déjà
produits : http_status, api_status, circuit breaker ouvert (503),
erreurs mémoire (500), rejets du garde-fou (422 DOCUMENT_TYPE_MISMATCH),
etc. — rien de tout ça n'existe si on appelle le pipeline en direct.

Prérequis :
    pip install requests

Utilisation :
    python run_cin_campaign.py

Avant de lancer :
    1. Démarrez votre backend (Docker Compose ou uvicorn en local).
    2. Renseignez BASE_URL, EMAIL, PASSWORD et CIN_FOLDER ci-dessous.
    3. Vérifiez que le compte utilisé a le rôle admin/operator/simple_user
       (n'importe lequel suffit, RG7 n'exige pas de rôle particulier
       pour lancer une extraction).

Sortie :
    - ResultatTest/responses/cin_tn/cin_tn__<nom>.json   (réponse brute
      de chaque appel, comme response_file dans vos fichiers existants)
    - ResultatTest/cin_tn_results_rerun.json             (résumé, même
      schéma que cin_tn_results.json, pour comparaison directe)
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

BASE_URL = "http://localhost:8000"          # URL de votre backend FastAPI
EMAIL = "belhaymedaziz@gmail.com"                  # compte existant sur la plateforme
PASSWORD = "AZIZ1230"                        # mot de passe de ce compte

# Dossier contenant les images CIN (mêmes fichiers que dans votre rapport)
CIN_FOLDER = r"C:\Users\Belha\OneDrive\Bureau\DataSetOCR\IMAGES"

# Motifs de noms de fichiers à inclure (insensible à la casse)
FILE_PATTERNS = ("carte*.jpg", "carte*.jpeg", "carte*.png")

DOCUMENT_TYPE = "cin_tn"
EXPECTED_TEMPLATE_ID = "cin_tn"

# Les 4 champs critiques de la CIN (cf. Chapitre 7 du rapport, critical_total=4)
CRITICAL_FIELDS = ["id_number", "last_name", "first_name", "birth_date"]

# Où écrire les résultats (créés automatiquement s'ils n'existent pas)
OUTPUT_DIR = Path("ResultatTest")
RESPONSES_DIR = OUTPUT_DIR / "responses" / "cin_tn"
SUMMARY_FILE = OUTPUT_DIR / "cin_tn_results_rerun.json"

# Timeout un peu au-dessus du timeout applicatif du backend (60 s, cf. Chapitre 4)
REQUEST_TIMEOUT_S = 90

# ============================================================


def login(base_url: str, email: str, password: str) -> str:
    """Authentifie et retourne le token JWT (POST /auth/login)."""
    resp = requests.post(
        f"{base_url}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    # Le champ exact dépend de votre schéma de réponse ; on tente les
    # variantes les plus courantes.
    token = (
        data.get("access_token")
        or data.get("data", {}).get("access_token")
        or data.get("token")
    )
    if not token:
        raise RuntimeError(f"Impossible de trouver le token dans la réponse login : {data}")
    return token


def list_images(folder: str, patterns: tuple[str, ...]) -> List[Path]:
    base = Path(folder)
    if not base.is_dir():
        raise FileNotFoundError(f"Dossier introuvable : {folder}")

    seen = set()
    files: List[Path] = []
    for pattern in patterns:
        for p in base.glob(pattern):
            if p.resolve() not in seen:
                seen.add(p.resolve())
                files.append(p)

    # Tri naturel approximatif (carte1, carte2, ... carte10, ...) plutôt
    # que lexicographique pur (carte1, carte10, carte2, ...).
    def sort_key(p: Path):
        import re
        m = re.search(r"(\d+)", p.stem)
        return (int(m.group(1)) if m else 0, p.stem)

    files.sort(key=sort_key)
    return files


def extract_document(
    base_url: str,
    token: str,
    file_path: Path,
    document_type: str,
) -> tuple[int, Dict[str, Any], Optional[str]]:
    """
    Envoie une requête POST /extract pour un fichier donné.
    Retourne (http_status, response_json, error_message_or_none).
    """
    url = f"{base_url}/extract"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f, "application/octet-stream")}
            data = {"document_type": document_type}
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
    """
    Construit une entrée de résumé au même format que vos fichiers
    ResultatTest/*.json déjà produits.
    """
    case_id = f"cin_tn__{file_path.stem}"
    http_ok = 200 <= http_status < 300

    result: Dict[str, Any] = {
        "case_id": case_id,
        "family": "cin_tn",
        "file": str(file_path),
        "file_exists": file_path.exists(),
        "expected_document_type": DOCUMENT_TYPE,
        "expected_template_id": EXPECTED_TEMPLATE_ID,
        "critical_total": len(CRITICAL_FIELDS),
        "error": None,
        "response_file": str(response_file),
        "http_status": http_status,
        "http_ok": http_ok,
    }

    if not http_ok:
        # Erreur HTTP (422 garde-fou, 500 mémoire, 503 circuit breaker, ...)
        detail = body.get("detail", body)
        result.update({
            "api_status": None,
            "template_id": None,
            "document_type": None,
            "engine_used": None,
            "strategy": None,
            "processing_time_ms": None,
            "global_confidence": None,
            "quality_score": None,
            "field_count": 0,
            "validated_field_count": 0,
            "critical_valid_count": 0,
            "critical_missing": "",
            "template_ok": False,
            "document_type_ok": False,
            "critical_pass": False,
            "error": str(detail) if detail else request_error,
        })
        return result

    fields = body.get("fields", []) or []
    fields_by_name = {f.get("name"): f for f in fields if isinstance(f, dict)}

    def field_is_valid(name: str) -> bool:
        f = fields_by_name.get(name)
        if not f:
            return False
        if f.get("validated") is True:
            return True
        # Repli si "validated" n'est pas présent : valeur non vide.
        return f.get("value") not in (None, "", [])

    critical_valid_count = sum(1 for name in CRITICAL_FIELDS if field_is_valid(name))
    critical_pass = critical_valid_count == len(CRITICAL_FIELDS)

    missing_parts = []
    for name in CRITICAL_FIELDS:
        if not field_is_valid(name):
            f = fields_by_name.get(name, {})
            reason = f.get("error") or f.get("reasons") or "field_not_found"
            if isinstance(reason, list):
                reason = ";".join(str(r) for r in reason)
            missing_parts.append(f"{name}:{reason}")

    validated_field_count = sum(
        1 for f in fields
        if isinstance(f, dict) and (f.get("validated") is True or f.get("value") not in (None, "", []))
    )

    result.update({
        "api_status": body.get("status"),
        "template_id": body.get("template_id"),
        "document_type": body.get("document_type"),
        "engine_used": body.get("engine_used"),
        "strategy": body.get("strategy"),
        "processing_time_ms": body.get("processing_time_ms"),
        "global_confidence": body.get("global_confidence"),
        "quality_score": body.get("quality_score"),
        "field_count": len(fields),
        "validated_field_count": validated_field_count,
        "critical_valid_count": critical_valid_count,
        "critical_missing": "; ".join(missing_parts),
        "template_ok": body.get("template_id") == EXPECTED_TEMPLATE_ID,
        "document_type_ok": body.get("document_type") == DOCUMENT_TYPE,
        "critical_pass": critical_pass,
    })

    return result


def main() -> None:
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Connexion à {BASE_URL} ...")
    token = login(BASE_URL, EMAIL, PASSWORD)
    print("Authentification réussie.\n")

    images = list_images(CIN_FOLDER, FILE_PATTERNS)
    print(f"{len(images)} images CIN trouvées dans {CIN_FOLDER}\n")

    summary: List[Dict[str, Any]] = []

    for i, file_path in enumerate(images, start=1):
        print(f"[{i}/{len(images)}] {file_path.name} ...", end=" ", flush=True)

        http_status, body, request_error = extract_document(
            BASE_URL, token, file_path, DOCUMENT_TYPE
        )

        response_file = RESPONSES_DIR / f"cin_tn__{file_path.stem}.json"
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2, default=str)

        case_result = build_case_result(file_path, http_status, body, request_error, response_file)
        summary.append(case_result)

        # Sauvegarde incrémentale : une erreur en cours de route ne fait
        # pas perdre les résultats déjà obtenus.
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

        status_label = "OK" if case_result.get("critical_pass") else "review/fail"
        print(f"http={http_status} -> {status_label}")

        time.sleep(0.2)  # petite pause pour ne pas saturer le rate limiter

    # ── Statistiques finales ────────────────────────────────────
    n = len(summary)
    strict_pass = sum(1 for c in summary if c.get("critical_pass"))
    print("\n" + "=" * 50)
    print(f"Total : {n} documents")
    print(f"Réussite stricte : {strict_pass}/{n} ({100 * strict_pass / n:.1f}\u202f%)" if n else "Aucun document traité")
    print(f"Résumé écrit dans : {SUMMARY_FILE.resolve()}")
    print(f"Réponses brutes dans : {RESPONSES_DIR.resolve()}")
    print("=" * 50)


if __name__ == "__main__":
    main()