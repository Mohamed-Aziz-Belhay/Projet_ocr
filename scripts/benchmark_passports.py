"""
scripts/benchmark_passports.py

Benchmark passeports.

Objectif:
- Vérifier que les passeports lisibles passent en success.
- Vérifier que les passeports de mauvaise qualité restent en review_required.
- Vérifier que les champs MRZ ne sont validés que si la MRZ est fiable.
- Vérifier que le temps d'exécution reste acceptable.

Exemple Windows:

python scripts\\benchmark_passports.py ^
  --base-url http://localhost:8000 ^
  --api-key dev-key-123 ^
  --engine paddle ^
  --processing-mode fast ^
  --out-csv benchmark_passport_results.csv ^
  --out-json benchmark_passport_results.json ^
  --save-responses-dir benchmark_passport_responses
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


@dataclass
class PassportCase:
    case_id: str
    file: str
    expected_status: str
    expected_valid_min: int
    expected_max_time_ms: int
    note: str


CASES: List[PassportCase] = [
    PassportCase(
        case_id="passport_tn_good_trilla",
        file="C:/Users/Belha/Downloads/Passeport-Tunisien-Allemagne.jpg",
        expected_status="success",
        expected_valid_min=7,
        expected_max_time_ms=8000,
        note="Passeport tunisien lisible avec MRZ exploitable",
    ),
    PassportCase(
        case_id="passport_fr_good",
        file="C:/Users/Belha/Downloads/passport-fr.jpg",
        expected_status="success",
        expected_valid_min=6,
        expected_max_time_ms=8000,
        note="Passeport français lisible",
    ),
    PassportCase(
        case_id="passport_tn_low_quality_libye",
        file="C:/Users/Belha/Downloads/passport-libye.jpg",
        expected_status="review_required",
        expected_valid_min=0,
        expected_max_time_ms=12000,
        note="Image faible qualité, MRZ illisible ou absente",
    ),
    PassportCase(
        case_id="passport_tn_good_quality",
        file="C:/Users/Belha/Downloads/passeport-tunisien-1.webp",
        expected_status="review_required",
        expected_valid_min=0,
        expected_max_time_ms=12000,
        note="Passeport good qualité / MRZ complete",
    ),
]


CRITICAL_FIELDS = {
    "document_number",
    "surname",
    "nationality",
    "birth_date",
    "expiry_date",
    "mrz",
}


def _field_map(response: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    fields = response.get("fields") or []
    return {str(f.get("name")): f for f in fields if isinstance(f, dict)}


def _validated_count(response: Dict[str, Any]) -> int:
    return sum(1 for f in response.get("fields", []) if f.get("validated") is True)


def _critical_valid_count(response: Dict[str, Any]) -> int:
    fmap = _field_map(response)
    count = 0

    for name in CRITICAL_FIELDS:
        f = fmap.get(name)
        if f and f.get("validated") is True and f.get("value") not in (None, "", []):
            count += 1

    return count


def _invalid_critical_values(response: Dict[str, Any]) -> List[str]:
    """
    Pour les cas review_required, on veut éviter les faux positifs:
    un champ critique ne doit pas être validé si la MRZ est invalide.
    """
    fmap = _field_map(response)
    invalid = []

    status = response.get("status")

    if status != "review_required":
        return invalid

    for name in CRITICAL_FIELDS:
        f = fmap.get(name)
        if not f:
            continue

        if f.get("validated") is True and f.get("value") not in (None, "", []):
            invalid.append(name)

    return invalid


def _strategy(response: Dict[str, Any]) -> Optional[str]:
    diagnostics = response.get("diagnostics") or {}
    return diagnostics.get("strategy")


def _selected_source(response: Dict[str, Any]) -> Optional[str]:
    diagnostics = response.get("diagnostics") or {}
    passport = diagnostics.get("passport_extraction") or {}
    return passport.get("selected_source")


def _selected_score(response: Dict[str, Any]) -> Optional[Any]:
    diagnostics = response.get("diagnostics") or {}
    passport = diagnostics.get("passport_extraction") or {}
    return passport.get("selected_score")


def _mrz_core_valid(response: Dict[str, Any]) -> Optional[Any]:
    diagnostics = response.get("diagnostics") or {}
    passport = diagnostics.get("passport_extraction") or {}
    return passport.get("selected_mrz_core_valid")


def _quality_score(response: Dict[str, Any]) -> Optional[Any]:
    return response.get("quality_score")


def run_case(
    case: PassportCase,
    *,
    base_url: str,
    api_key: str,
    engine: str,
    processing_mode: str,
    save_responses_dir: Optional[Path],
) -> Dict[str, Any]:
    file_path = Path(case.file)
    exists = file_path.exists()

    row: Dict[str, Any] = {
        "case_id": case.case_id,
        "file": str(file_path),
        "file_exists": exists,
        "expected_status": case.expected_status,
        "expected_valid_min": case.expected_valid_min,
        "expected_max_time_ms": case.expected_max_time_ms,
        "note": case.note,
        "http_status": None,
        "http_ok": False,
        "api_status": None,
        "template_id": None,
        "document_type": None,
        "engine_used": None,
        "strategy": None,
        "selected_source": None,
        "selected_score": None,
        "mrz_core_valid": None,
        "quality_score": None,
        "processing_time_ms": None,
        "validated_count": 0,
        "critical_valid_count": 0,
        "invalid_critical_values": "",
        "behavior_pass": False,
        "time_pass": False,
        "benchmark_pass": False,
        "error": "",
        "response_file": "",
    }

    if not exists:
        row["error"] = "file_not_found"
        return row

    url = f"{base_url.rstrip('/')}/extract"

    files = {
        "file": (file_path.name, file_path.open("rb"), "application/octet-stream"),
    }

    data = {
        "document_type": "passport",
        "template_id": "passport_generic",
        "engine": engine,
        "processing_mode": processing_mode,
        "language_hint": "en",
        "include_diagnostics": "true",
    }

    started = time.perf_counter()

    try:
        response = requests.post(
            url,
            headers={"X-API-Key": api_key},
            files=files,
            data=data,
            timeout=120,
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        row["http_status"] = response.status_code
        row["http_ok"] = 200 <= response.status_code < 300

        try:
            payload = response.json()
        except Exception:
            payload = {"raw_response": response.text}

        if save_responses_dir is not None:
            save_responses_dir.mkdir(parents=True, exist_ok=True)
            response_path = save_responses_dir / f"{case.case_id}.json"
            response_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            row["response_file"] = str(response_path)

        if not row["http_ok"]:
            row["error"] = str(payload)
            row["processing_time_ms"] = elapsed_ms
            return row

        row["api_status"] = payload.get("status")
        row["template_id"] = payload.get("template_id")
        row["document_type"] = payload.get("document_type")
        row["engine_used"] = payload.get("engine_used")
        row["strategy"] = _strategy(payload)
        row["selected_source"] = _selected_source(payload)
        row["selected_score"] = _selected_score(payload)
        row["mrz_core_valid"] = _mrz_core_valid(payload)
        row["quality_score"] = _quality_score(payload)
        row["processing_time_ms"] = payload.get("processing_time_ms") or elapsed_ms

        row["validated_count"] = _validated_count(payload)
        row["critical_valid_count"] = _critical_valid_count(payload)

        invalid_critical = _invalid_critical_values(payload)
        row["invalid_critical_values"] = ";".join(invalid_critical)

        status_ok = row["api_status"] == case.expected_status

        if case.expected_status == "success":
            fields_ok = row["validated_count"] >= case.expected_valid_min
            no_false_review = not invalid_critical
            row["behavior_pass"] = bool(status_ok and fields_ok and no_false_review)

        elif case.expected_status == "review_required":
            # Pour une mauvaise image, le bon comportement est:
            # review_required + pas de champs critiques faussement validés.
            no_false_positive = len(invalid_critical) == 0
            row["behavior_pass"] = bool(status_ok and no_false_positive)

        else:
            row["behavior_pass"] = status_ok

        try:
            row["time_pass"] = int(row["processing_time_ms"] or 0) <= case.expected_max_time_ms
        except Exception:
            row["time_pass"] = False

        row["benchmark_pass"] = bool(row["behavior_pass"] and row["time_pass"])

        return row

    except Exception as exc:
        row["error"] = str(exc)
        row["processing_time_ms"] = int((time.perf_counter() - started) * 1000)
        return row

    finally:
        try:
            files["file"][1].close()
        except Exception:
            pass


def write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    if not rows:
        return

    fieldnames = list(rows[0].keys())

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)

    def count(key: str, value: Any = True) -> int:
        return sum(1 for r in rows if r.get(key) == value)

    success_cases = [r for r in rows if r.get("expected_status") == "success"]
    review_cases = [r for r in rows if r.get("expected_status") == "review_required"]

    return {
        "total_cases": total,
        "http_ok": count("http_ok", True),
        "behavior_passed": count("behavior_pass", True),
        "time_passed": count("time_pass", True),
        "benchmark_passed": count("benchmark_pass", True),
        "success_expected_cases": len(success_cases),
        "success_expected_passed": sum(1 for r in success_cases if r.get("benchmark_pass") is True),
        "review_expected_cases": len(review_cases),
        "review_expected_passed": sum(1 for r in review_cases if r.get("benchmark_pass") is True),
        "technical_failures": sum(1 for r in rows if not r.get("http_ok")),
        "avg_processing_time_ms": (
            round(
                sum(int(r.get("processing_time_ms") or 0) for r in rows) / max(1, total),
                2,
            )
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default="dev-key-123")
    parser.add_argument("--engine", default="paddle")
    parser.add_argument("--processing-mode", default="fast")
    parser.add_argument("--out-csv", default="benchmark_passport_results.csv")
    parser.add_argument("--out-json", default="benchmark_passport_results.json")
    parser.add_argument("--save-responses-dir", default="benchmark_passport_responses")

    args = parser.parse_args()

    save_dir = Path(args.save_responses_dir) if args.save_responses_dir else None

    rows: List[Dict[str, Any]] = []

    for case in CASES:
        print(f"[RUN] {case.case_id} -> {case.file}")

        row = run_case(
            case,
            base_url=args.base_url,
            api_key=args.api_key,
            engine=args.engine,
            processing_mode=args.processing_mode,
            save_responses_dir=save_dir,
        )

        rows.append(row)

        status = "PASS" if row["benchmark_pass"] else "FAIL"

        print(
            f"[{status}] "
            f"http={row['http_status']} "
            f"api={row['api_status']} "
            f"valid={row['validated_count']} "
            f"critical={row['critical_valid_count']} "
            f"time={row['processing_time_ms']}ms "
            f"source={row['selected_source']}"
        )

        if row.get("invalid_critical_values"):
            print(f"      invalid_critical={row['invalid_critical_values']}")

        if row.get("error"):
            print(f"      error={row['error']}")

    summary = build_summary(rows)

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)

    write_csv(rows, out_csv)

    out_json.write_text(
        json.dumps(
            {
                "summary": summary,
                "cases": [asdict(c) for c in CASES],
                "results": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n[SUMMARY]")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[OK] wrote {out_csv}")
    print(f"[OK] wrote {out_json}")

    return 0 if summary["benchmark_passed"] == summary["total_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())