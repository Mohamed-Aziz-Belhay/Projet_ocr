"""
scripts/run_full_benchmark.py

Benchmark complet ExtractLY - fusion des 3 scripts existants
(benchmark_documents.py, benchmark_passports.py, benchmark_global_final.py)
étendu aux 4 sources de données réelles :

  - CIN tunisiennes      : DataSetOCR/IMAGES/        (~30 images)
  - Passeports tunisiens : DataSetOCR/Passport/      (~30 images)
  - Factures             : DataSetOCR/Facture/       (~11 images)
  - MIDV2020             : midv2020/raw/templates/images/<classe>/

Sortie centralisée sous --output-dir (par défaut ResultatTest) :

  ResultatTest/
    responses/
      cin_tn/<case_id>.json
      passport_tn/<case_id>.json
      invoice/<case_id>.json
      midv2020/<case_id>.json
    cin_tn_results.csv / .json
    passport_tn_results.csv / .json
    invoice_results.csv / .json
    midv2020_results.csv / .json
    global_summary.json

Exemple (Windows) :

  python scripts\\run_full_benchmark.py ^
    --base-url http://localhost:8000 ^
    --api-key dev-key-123 ^
    --output-dir "C:\\Users\\Belha\\Downloads\\ocr_final_modified_refactor\\ocr_final\\app\\ResultatTest"

Avant un run complet, fais d'abord un essai rapide :

  python scripts\\run_full_benchmark.py --smoke-test
  (1 cas par famille seulement - vérifie que tout est bien câblé avant
   de lancer les ~80 cas complets)

Pour voir juste ce qui serait testé, sans appeler l'API :

  python scripts\\run_full_benchmark.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# ===========================================================================
# Champs critiques par famille
# ===========================================================================
# IMPORTANT : les noms de champs CIN ci-dessous sont une hypothèse basée sur
# extract_cin_fields() (cin_rules.py / field_extractors.py) qui produit les
# clés cin_number / family_name / first_name / place_of_birth / date_of_birth.
# AVANT le run complet, lance --smoke-test et vérifie dans
# responses/cin_tn/<case>.json que les noms de champs retournés par l'API
# correspondent bien à CIN_CRITICAL_FIELDS ci-dessous. Adapte si besoin.

CIN_CRITICAL_FIELDS = [
    "id_number",
    "last_name",
    "first_name",
    "birth_date",
]

PASSPORT_CRITICAL_FIELDS = [
    "document_number",
    "surname",
    "given_names",
    "nationality",
    "birth_date",
    "expiry_date",
    "mrz",
]

INVOICE_CRITICAL_FIELDS = [
    "invoice_number",
    "invoice_date",
    "total_ttc",
]

MIDV_DEFAULT_CRITICAL_FIELDS = [
    "number",
    "id_number",
    "birth_date",
    "expiry_date",
    "name",
    "surname",
]

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


# ===========================================================================
# Découverte des cas par source
# ===========================================================================

def discover_flat_dir(
    *,
    folder: Path,
    family: str,
    document_type: str,
    template_id: Optional[str],
    engine: str,
    processing_mode: str,
    critical_fields: List[str],
    expected_document_type: str,
    expected_template_id: Optional[str],
    max_cases: int = 0,
) -> List[Dict[str, Any]]:
    """
    Découvre toutes les images directement dans `folder` (pas de sous-dossiers
    de variantes) - utilisé pour CIN TN, Passeport TN, Facture.
    """
    if not folder.exists():
        print(f"[WARN] Dossier introuvable, ignoré : {folder}")
        return []

    image_paths = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )

    if max_cases > 0:
        image_paths = image_paths[:max_cases]

    cases = []
    for image_path in image_paths:
        cases.append({
            "case_id": f"{family}__{image_path.stem}",
            "family": family,
            "file": str(image_path),
            "document_type": document_type,
            "template_id": template_id,
            "engine": engine,
            "processing_mode": processing_mode,
            "critical_fields": critical_fields,
            "expected_document_type": expected_document_type,
            "expected_template_id": expected_template_id,
        })
    return cases


def infer_midv_expected_document_type(class_name: str) -> str:
    low = class_name.lower()
    if "passport" in low:
        return "passport"
    return "id_document"


def infer_midv_expected_template(class_name: str) -> str:
    return f"midv_{class_name}"


def critical_fields_for_midv(class_name: str, expected_document_type: str) -> List[str]:
    if expected_document_type == "passport":
        return PASSPORT_CRITICAL_FIELDS
    return MIDV_DEFAULT_CRITICAL_FIELDS


def discover_midv(
    *,
    midv_root: Path,
    classes: List[str],
    engine: str,
    processing_mode: str,
    max_per_class: int = 0,
) -> List[Dict[str, Any]]:
    """
    Découvre les images MIDV2020 sous midv_root/<classe>/*.jpg
    (structure .../templates/images/<classe>/...)
    """
    cases = []

    for class_name in classes:
        class_dir = midv_root / class_name

        if not class_dir.exists():
            print(f"[WARN] Classe MIDV introuvable, ignorée : {class_dir}")
            continue

        image_paths = sorted(
            p for p in class_dir.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        )

        if max_per_class > 0:
            image_paths = image_paths[:max_per_class]

        expected_document_type = infer_midv_expected_document_type(class_name)
        expected_template_id = infer_midv_expected_template(class_name)
        critical_fields = critical_fields_for_midv(class_name, expected_document_type)

        for image_path in image_paths:
            cases.append({
                "case_id": f"midv2020__{class_name}__{image_path.stem}",
                "family": "midv2020",
                "file": str(image_path),
                "document_type": "auto",
                "template_id": None,
                "engine": engine,
                "processing_mode": processing_mode,
                "critical_fields": critical_fields,
                "expected_document_type": expected_document_type,
                "expected_template_id": expected_template_id,
            })

    return cases


# ===========================================================================
# Exécution d'un cas
# ===========================================================================

def get_field_map(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for field in payload.get("fields", []) or []:
        name = field.get("name")
        if name:
            out[name] = field
    return out


def run_case(
    *,
    case: Dict[str, Any],
    base_url: str,
    api_key: str,
    bearer_token: Optional[str],
    timeout: int,
    save_responses_dir: Optional[Path],
) -> Dict[str, Any]:
    file_path = Path(case["file"])

    row: Dict[str, Any] = {
        "case_id": case["case_id"],
        "family": case["family"],
        "file": str(file_path),
        "file_exists": file_path.exists(),
        "expected_document_type": case.get("expected_document_type"),
        "expected_template_id": case.get("expected_template_id"),
        "http_status": None,
        "http_ok": False,
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
        "critical_total": len(case.get("critical_fields", [])),
        "critical_valid_count": 0,
        "critical_missing": "",
        "template_ok": False,
        "document_type_ok": False,
        "critical_pass": False,
        "error": None,
        "response_file": "",
    }

    if not file_path.exists():
        row["error"] = "file_not_found"
        return row

    url = base_url.rstrip("/") + "/extract"

    data = {
        "document_type": case.get("document_type") or "auto",
        "engine": case.get("engine") or "auto",
        "processing_mode": case.get("processing_mode") or "balanced",
        "include_diagnostics": "true",
    }
    if case.get("template_id"):
        data["template_id"] = case["template_id"]

    headers = {"X-API-Key": api_key}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    max_attempts = 3
    last_exc: Optional[Exception] = None
    response = None
    started = time.perf_counter()

    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            with file_path.open("rb") as f:
                response = requests.post(
                    url,
                    headers=headers,
                    files={"file": (file_path.name, f, "application/octet-stream")},
                    data=data,
                    timeout=timeout,
                )
            last_exc = None
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            if attempt < max_attempts:
                print(f"      [RETRY {attempt}/{max_attempts}] {case.get('case_id')} -> {type(exc).__name__}")
                time.sleep(3 * attempt)  # backoff progressif: 3s, 6s
            continue

    if last_exc is not None:
        row["error"] = f"{type(last_exc).__name__}: {last_exc}"
        row["processing_time_ms"] = int((time.perf_counter() - started) * 1000)
        return row

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    try:
        payload = response.json()
    except Exception:
        row["error"] = "response_not_json"
        payload = {}

    if save_responses_dir is not None:
        save_responses_dir.mkdir(parents=True, exist_ok=True)
        response_path = save_responses_dir / f"{case['case_id']}.json"
        response_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        row["response_file"] = str(response_path)

    return build_row_from_payload(
        case=case,
        row=row,
        payload=payload,
        http_status=response.status_code,
        elapsed_ms=elapsed_ms,
    )


def build_row_from_payload(
    *,
    case: Dict[str, Any],
    row: Dict[str, Any],
    payload: Dict[str, Any],
    http_status: Optional[int],
    elapsed_ms: int,
) -> Dict[str, Any]:
    """
    Calcule tous les champs dérivés (critical_valid_count, template_ok, etc.)
    à partir d'un payload JSON déjà obtenu - que ce payload vienne d'un appel
    API frais ou d'un fichier de réponse rechargé depuis le disque (--resume).
    """
    row["http_status"] = http_status
    row["http_ok"] = http_status is not None and 200 <= http_status < 300

    if not row["http_ok"]:
        if not row["error"]:
            row["error"] = str(payload)[:500]
        row["processing_time_ms"] = elapsed_ms
        return row

    field_map = get_field_map(payload)
    fields = payload.get("fields", []) or []

    row["api_status"] = payload.get("status")
    row["template_id"] = payload.get("template_id")
    row["document_type"] = payload.get("document_type")
    row["engine_used"] = payload.get("engine_used")
    row["strategy"] = (payload.get("diagnostics") or {}).get("strategy")
    row["processing_time_ms"] = payload.get("processing_time_ms") or elapsed_ms
    row["global_confidence"] = payload.get("global_confidence")
    row["quality_score"] = payload.get("quality_score")
    row["field_count"] = len(fields)
    row["validated_field_count"] = sum(1 for f in fields if f.get("validated"))

    critical_fields = case.get("critical_fields", []) or []
    critical_missing = []
    critical_valid_count = 0

    for name in critical_fields:
        field = field_map.get(name)
        if field and field.get("validated") and field.get("value") not in (None, "", []):
            critical_valid_count += 1
        else:
            critical_missing.append(f"{name}:{(field or {}).get('error') or 'missing_field'}")

    row["critical_valid_count"] = critical_valid_count
    row["critical_missing"] = "; ".join(critical_missing)
    row["critical_pass"] = critical_valid_count == len(critical_fields)

    expected_template = case.get("expected_template_id")
    expected_doc_type = case.get("expected_document_type")

    row["template_ok"] = (not expected_template) or (row["template_id"] == expected_template)
    row["document_type_ok"] = (not expected_doc_type) or (row["document_type"] == expected_doc_type)

    return row


# ===========================================================================
# Écriture des fichiers de sortie
# ===========================================================================

CSV_FIELDS = [
    "case_id", "family", "file", "file_exists",
    "expected_document_type", "expected_template_id",
    "http_status", "http_ok", "api_status", "template_id", "document_type",
    "engine_used", "strategy", "processing_time_ms", "global_confidence",
    "quality_score", "field_count", "validated_field_count",
    "critical_total", "critical_valid_count", "critical_missing",
    "template_ok", "document_type_ok", "critical_pass", "error", "response_file",
]


def write_family_outputs(output_dir: Path, family: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return

    csv_path = output_dir / f"{family}_results.csv"
    json_path = output_dir / f"{family}_results.json"

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[OK] {family}: {csv_path.name} / {json_path.name} ({len(rows)} cas)")


def build_global_summary(all_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_family: Dict[str, Dict[str, Any]] = {}

    for row in all_rows:
        fam = row.get("family") or "unknown"
        item = by_family.setdefault(fam, {
            "cases": 0, "http_ok": 0, "success": 0,
            "review_required": 0, "other_status": 0,
            "critical_pass": 0, "technical_failures": 0,
        })
        item["cases"] += 1
        if row.get("http_ok"):
            item["http_ok"] += 1
        else:
            item["technical_failures"] += 1

        status = row.get("api_status")
        if status == "success":
            item["success"] += 1
        elif status == "review_required":
            item["review_required"] += 1
        elif status is not None:
            item["other_status"] += 1

        if row.get("critical_pass"):
            item["critical_pass"] += 1

    total = len(all_rows)
    return {
        "total_cases": total,
        "by_family": by_family,
    }


# ===========================================================================
# Main
# ===========================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark complet ExtractLY (CIN, passeport TN, facture, MIDV2020).")

    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--api-key", default="dev-key-123")
    p.add_argument(
        "--bearer-token",
        default=None,
        help="Token JWT obtenu via POST /auth/login (onglet Authorize de /docs, "
             "ou réponse JSON du login). Nécessaire si /extract exige un compte "
             "utilisateur en plus de la clé API.",
    )
    p.add_argument(
        "--output-dir",
        default=r"C:\Users\Belha\Downloads\ocr_final_modified_refactor\ocr_final\app\ResultatTest",
    )

    p.add_argument(
        "--cin-dir",
        default=r"C:\Users\Belha\OneDrive\Bureau\DataSetOCR\IMAGES",
    )
    p.add_argument(
        "--passport-dir",
        default=r"C:\Users\Belha\OneDrive\Bureau\DataSetOCR\Passport",
    )
    p.add_argument(
        "--invoice-dir",
        default=r"C:\Users\Belha\OneDrive\Bureau\DataSetOCR\Facture",
    )
    p.add_argument(
        "--midv-root",
        default=r"C:\Users\Belha\Downloads\ocr_final_modified_refactor\ocr_final\app\data\external\midv2020\raw\templates\images",
    )
    p.add_argument(
        "--midv-classes",
        default="svk_id,srb_passport,fin_id,est_id,aze_passport,lva_passport",
        help="Liste de classes MIDV2020 séparées par des virgules. "
             "ATTENTION: vérifie l'orthographe exacte du dossier (lva_passport vs Iva_passport).",
    )
    p.add_argument("--max-per-class-midv", type=int, default=0, help="0 = toutes les images.")
    p.add_argument("--max-cin", type=int, default=0)
    p.add_argument("--max-passport", type=int, default=0)
    p.add_argument("--max-invoice", type=int, default=0)

    p.add_argument("--cin-engine", default="paddle")
    p.add_argument("--cin-mode", default="balanced")
    p.add_argument("--passport-engine", default="paddle")
    p.add_argument("--passport-mode", default="fast")
    p.add_argument("--invoice-engine", default="paddle")
    p.add_argument("--invoice-mode", default="balanced")
    p.add_argument("--midv-engine", default="easyocr")
    p.add_argument("--midv-mode", default="fast")

    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--dry-run", action="store_true", help="Découvre les cas sans appeler l'API.")
    p.add_argument(
        "--resume",
        action="store_true",
        help="Si un fichier de réponse existe déjà pour un cas (responses/<famille>/<case_id>.json), "
             "le réutiliser au lieu de rappeler l'API. Utile après un crash serveur en plein run "
             "pour ne pas refaire les cas déjà traités avec succès.",
    )
    p.add_argument(
        "--skip-warmup",
        action="store_true",
        help="Désactive l'échauffement (1 appel jetable par famille) avant le vrai benchmark. "
             "Déconseillé : sans ça, les premiers cas peuvent échouer artificiellement "
             "à cause du chargement à froid des modèles OCR.",
    )
    p.add_argument("--smoke-test", action="store_true", help="Limite à 1 cas par famille pour un essai rapide.")

    return p.parse_args()


def main() -> int:
    args = parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    responses_root = output_dir / "responses"

    midv_classes = [c.strip() for c in args.midv_classes.split(",") if c.strip()]

    max_cin = 1 if args.smoke_test else args.max_cin
    max_passport = 1 if args.smoke_test else args.max_passport
    max_invoice = 1 if args.smoke_test else args.max_invoice
    max_midv = 1 if args.smoke_test else args.max_per_class_midv

    families: Dict[str, List[Dict[str, Any]]] = {}

    families["cin_tn"] = discover_flat_dir(
        folder=Path(args.cin_dir),
        family="cin_tn",
        document_type="cin_tn",
        template_id="cin_tn",
        engine=args.cin_engine,
        processing_mode=args.cin_mode,
        critical_fields=CIN_CRITICAL_FIELDS,
        expected_document_type="cin_tn",
        expected_template_id="cin_tn",
        max_cases=max_cin,
    )

    families["passport_tn"] = discover_flat_dir(
        folder=Path(args.passport_dir),
        family="passport_tn",
        document_type="passport",
        template_id="passport_generic",
        engine=args.passport_engine,
        processing_mode=args.passport_mode,
        critical_fields=PASSPORT_CRITICAL_FIELDS,
        expected_document_type="passport",
        expected_template_id="passport_generic",
        max_cases=max_passport,
    )

    families["invoice"] = discover_flat_dir(
        folder=Path(args.invoice_dir),
        family="invoice",
        document_type="invoice",
        template_id="invoice_tn",
        engine=args.invoice_engine,
        processing_mode=args.invoice_mode,
        critical_fields=INVOICE_CRITICAL_FIELDS,
        expected_document_type="invoice",
        expected_template_id="invoice_tn",
        max_cases=max_invoice,
    )

    families["midv2020"] = discover_midv(
        midv_root=Path(args.midv_root),
        classes=midv_classes,
        engine=args.midv_engine,
        processing_mode=args.midv_mode,
        max_per_class=max_midv,
    )

    total_cases = sum(len(v) for v in families.values())
    print(f"[INFO] Cas découverts : "
          f"cin_tn={len(families['cin_tn'])}, "
          f"passport_tn={len(families['passport_tn'])}, "
          f"invoice={len(families['invoice'])}, "
          f"midv2020={len(families['midv2020'])} "
          f"-> total={total_cases}")

    if total_cases == 0:
        print("[ERROR] Aucun cas trouvé. Vérifie les chemins --cin-dir / --passport-dir / --invoice-dir / --midv-root.")
        return 2

    if args.dry_run:
        for fam, cases in families.items():
            print(f"\n--- {fam} ({len(cases)} cas) ---")
            for c in cases[:5]:
                print(f"  {c['case_id']} -> {c['file']}")
            if len(cases) > 5:
                print(f"  ... +{len(cases) - 5} autres")
        return 0

    if not args.skip_warmup:
        print("\n[WARMUP] Échauffement du serveur (chargement des modèles en mémoire)...")
        print("         Les résultats de cette étape sont ignorés, ce n'est pas un test.")
        for fam, cases in families.items():
            if not cases:
                continue
            warmup_case = cases[0]
            print(f"[WARMUP] {fam} -> {warmup_case['case_id']}")
            warmup_row = run_case(
                case=warmup_case,
                base_url=args.base_url,
                api_key=args.api_key,
                bearer_token=args.bearer_token,
                timeout=args.timeout,
                save_responses_dir=None,  # ne pas sauvegarder, c'est jetable
            )
            print(f"         -> {warmup_row.get('processing_time_ms')}ms "
                  f"(status={warmup_row.get('api_status')}, error={warmup_row.get('error')})")
        print("[WARMUP] Terminé. Début du vrai benchmark.\n")

    all_rows: List[Dict[str, Any]] = []

    for fam, cases in families.items():
        if not cases:
            continue

        fam_responses_dir = responses_root / fam
        fam_rows = []

        for idx, case in enumerate(cases, start=1):
            existing_response_path = fam_responses_dir / f"{case['case_id']}.json"

            if args.resume and existing_response_path.exists():
                print(f"[{fam} {idx}/{len(cases)}] {case['case_id']} -> [SKIP] déjà fait (--resume)")
                try:
                    payload = json.loads(existing_response_path.read_text(encoding="utf-8"))
                    row = {
                        "case_id": case["case_id"],
                        "family": case["family"],
                        "file": case["file"],
                        "file_exists": Path(case["file"]).exists(),
                        "expected_document_type": case.get("expected_document_type"),
                        "expected_template_id": case.get("expected_template_id"),
                        "critical_total": len(case.get("critical_fields", [])),
                        "error": None,
                        "response_file": str(existing_response_path),
                    }
                    row = build_row_from_payload(
                        case=case,
                        row=row,
                        payload=payload,
                        http_status=200,
                        elapsed_ms=payload.get("processing_time_ms") or 0,
                    )
                except Exception as exc:
                    row = {"case_id": case["case_id"], "family": case["family"],
                           "error": f"resume_load_failed: {exc}", "critical_pass": False,
                           "http_ok": False}
            else:
                print(f"[{fam} {idx}/{len(cases)}] {case['case_id']}")

                row = run_case(
                    case=case,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    bearer_token=args.bearer_token,
                    timeout=args.timeout,
                    save_responses_dir=fam_responses_dir,
                )

            fam_rows.append(row)
            all_rows.append(row)

            mark = "OK" if row.get("critical_pass") else "FAIL"
            print(
                f"   [{mark}] status={row.get('api_status')} "
                f"template={row.get('template_id')} "
                f"critical={row.get('critical_valid_count')}/{row.get('critical_total')} "
                f"time={row.get('processing_time_ms')}ms"
            )
            if row.get("critical_missing"):
                print(f"        missing={row['critical_missing']}")
            if row.get("error"):
                print(f"        error={row['error']}")

        write_family_outputs(output_dir, fam, fam_rows)

    summary = build_global_summary(all_rows)
    summary_path = output_dir / "global_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print("========== RÉSUMÉ GLOBAL ==========")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[OK] Résumé global écrit : {summary_path}")
    print(f"[OK] Tous les résultats sont sous : {output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())