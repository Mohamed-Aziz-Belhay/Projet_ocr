"""
run_passport_campaign.py

Script de campagne de test — passeports tunisiens.

Même principe que run_cin_campaign.py : de VRAIES requêtes HTTP vers
POST /extract (pas d'appel direct au pipeline en Python), pour capturer
http_status, api_status, et les échecs réels observés en conditions de
charge (503 circuit breaker, 500 mémoire, 422 garde-fou de type).

Prérequis :
    pip install requests

Avant de lancer :
    1. Démarrez votre backend.
    2. Renseignez BASE_URL, EMAIL, PASSWORD et PASSPORT_FOLDER ci-dessous.

Sortie :
    - ResultatTest/responses/passport_tn/passport_tn__<nom>.json
    - ResultatTest/passport_tn_results_rerun.json   (même schéma que
      passport_tn_results.json, pour comparaison directe)

Note sur les temps de traitement :
    Le pipeline passeport explore jusqu'à dix-huit combinaisons de
    découpage MRZ avant d'abandonner (cf. Chapitre 4 du rapport) : un
    document dont la MRZ n'est jamais validée peut prendre 10 à 20~s,
    parfois plus. REQUEST_TIMEOUT_S est fixé au-delà du timeout
    applicatif du backend (60~s) pour ne jamais couper la requête avant
    que le serveur lui-même ne le fasse.
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

# Dossier contenant les images de passeports
PASSPORT_FOLDER = r"C:\Users\Belha\OneDrive\Bureau\DataSetOCR\Passport"

# Le dossier passeport contient des noms de fichiers variés
# (pass1.jpg, pass11.jpg, ... mais aussi
# "Analyse-bande-MRZ-passeport-tunisien-1.webp") : on prend donc toutes
# les images du dossier plutôt que de filtrer sur un préfixe "pass*".
FILE_PATTERNS = ("*.jpg", "*.jpeg", "*.png", "*.webp")

DOCUMENT_TYPE = "passport"
EXPECTED_TEMPLATE_ID = "passport_generic"

# Les 7 champs critiques du passeport (cf. Chapitre 7 du rapport,
# critical_total=7 ; déduits des critical_missing déjà observés :
# "document_number:...; surname:...; given_names:...; nationality:...;
#  birth_date:...; expiry_date:...; mrz:...")
CRITICAL_FIELDS = [
    "document_number",
    "surname",
    "given_names",
    "nationality",
    "birth_date",
    "expiry_date",
    "mrz",
]

OUTPUT_DIR = Path("ResultatTest")
RESPONSES_DIR = OUTPUT_DIR / "responses" / "passport_tn"
SUMMARY_FILE = OUTPUT_DIR / "passport_tn_results_rerun.json"

# Timeout très généreux : on préfère attendre trop longtemps que
# risquer d'abandonner une requête que le serveur traite toujours
# (cf. incident constaté : abandon client à 90s alors que le serveur
# répondait 200 OK bien plus tard, laissant potentiellement une
# extraction orpheline tourner en arrière-plan).
REQUEST_TIMEOUT_S = 300

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
    Mode SYNCHRONE (POST /extract) plutôt qu'asynchrone.

    Deux raisons de ce choix :
    1. Le mode asynchrone (POST /extract/async) déclenche actuellement
       une ForeignKeyViolationError côté serveur pour les utilisateurs
       authentifiés par JWT (api_key_id "jwt-derived-..." absent de la
       table api_keys) -> le job n'est pas créé en base -> le polling
       GET /jobs/{job_id} échoue ensuite en 401. Bug backend distinct,
       à corriger côté code, hors périmètre de ce script.
    2. Le mode synchrone n'utilise pas du tout la table jobs -> ce bug
       ne s'applique pas.

    Le problème initial (timeout client trop court, abandon avant la
    vraie fin du traitement serveur) est traité en portant le timeout
    à REQUEST_TIMEOUT_S (très généreux) plutôt qu'en changeant de mode.
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
    case_id = f"passport_tn__{file_path.stem}"
    http_ok = 200 <= http_status < 300

    result: Dict[str, Any] = {
        "case_id": case_id,
        "family": "passport_tn",
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
    import argparse

    parser = argparse.ArgumentParser(description="Campagne de test — passeports tunisiens")
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Ne teste qu'un seul fichier (nom exact ou chemin complet), ex. --file pass49.jpg",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pause après chaque fichier (Entrée pour continuer) : le temps de vérifier "
             "le Gestionnaire des tâches (RAM/disque) avant la requête suivante.",
    )
    args = parser.parse_args()

    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Connexion à {BASE_URL} ...")
    token = login(BASE_URL, EMAIL, PASSWORD)
    print("Authentification réussie.\n")

    all_images = list_images(PASSPORT_FOLDER, FILE_PATTERNS)

    if args.file:
        # Filtre sur le nom de fichier exact (avec ou sans extension) ou
        # chemin complet fourni directement.
        target = Path(args.file)
        if target.is_file():
            images = [target]
        else:
            wanted = args.file.lower()
            images = [
                p for p in all_images
                if p.name.lower() == wanted or p.stem.lower() == wanted
            ]
        if not images:
            print(f"Aucun fichier correspondant à '{args.file}' trouvé dans {PASSPORT_FOLDER}")
            print(f"Fichiers disponibles : {[p.name for p in all_images]}")
            return
    else:
        images = all_images

    print(f"{len(images)} image(s) à traiter.\n")

    summary: List[Dict[str, Any]] = []

    for i, file_path in enumerate(images, start=1):
        if args.interactive:
            input(
                f"\n[{i}/{len(images)}] Prêt à envoyer {file_path.name} — "
                f"vérifiez le Gestionnaire des tâches maintenant si besoin, "
                f"puis appuyez sur Entrée pour lancer la requête..."
            )

        print(f"[{i}/{len(images)}] {file_path.name} ...", end=" ", flush=True)

        t0 = time.time()
        http_status, body, request_error = extract_document(
            BASE_URL, token, file_path, DOCUMENT_TYPE
        )
        elapsed_s = time.time() - t0

        response_file = RESPONSES_DIR / f"passport_tn__{file_path.stem}.json"
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2, default=str)

        case_result = build_case_result(file_path, http_status, body, request_error, response_file)
        summary.append(case_result)

        # En mode --file, on écrit un fichier de résumé séparé pour ne pas
        # écraser le résumé complet de la campagne à 30 documents.
        summary_path = SUMMARY_FILE if not args.file else (
            OUTPUT_DIR / f"passport_tn_single_{file_path.stem}.json"
        )
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

        status_label = "OK" if case_result.get("critical_pass") else "review/fail"
        print(f"http={http_status} -> {status_label} ({elapsed_s:.1f}s)")

        if not args.interactive:
            time.sleep(0.2)

    # ── Statistiques finales ────────────────────────────────────
    n = len(summary)
    strict_pass = sum(1 for c in summary if c.get("critical_pass"))
    http_errors = sum(1 for c in summary if not c.get("http_ok"))
    print("\n" + "=" * 50)
    print(f"Total : {n} documents")
    print(f"Réussite stricte : {strict_pass}/{n} ({100 * strict_pass / n:.1f}\u202f%)" if n else "Aucun document traité")
    print(f"Échecs HTTP (500/503/422) : {http_errors}/{n}")
    print(f"Réponses brutes dans : {RESPONSES_DIR.resolve()}")
    print("=" * 50)


if __name__ == "__main__":
    main()