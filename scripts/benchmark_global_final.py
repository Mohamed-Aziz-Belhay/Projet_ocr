from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


PASSPORT_CRITICAL_FIELDS = [
    "document_number",
    "surname",
    "given_names",
    "nationality",
    "birth_date",
    "expiry_date",
    "mrz",
]

SVK_CRITICAL_FIELDS = [
    "birth_date",
    "expiry_date",
    "id_number",
    "name",
    "number",
    "surname",
]

INVOICE_CRITICAL_FIELDS = [
    "invoice_number",
    "invoice_date",
    "total_ttc",
    "amount_consistency",
]


CASES: List[Dict[str, Any]] = [
    # ------------------------------------------------------------------
    # PASSPORTS - expected automatic success
    # ------------------------------------------------------------------
    {
        "case_id": "aze_passport_upright_00",
        "family": "passport",
        "file": "app/data/external/midv2020/raw/scan_upright/images/aze_passport/00.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_aze_passport",
        "expected_document_type": "passport",
        "critical_fields": PASSPORT_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "aze_passport_upright_01",
        "family": "passport",
        "file": "app/data/external/midv2020/raw/scan_upright/images/aze_passport/01.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_aze_passport",
        "expected_document_type": "passport",
        "critical_fields": PASSPORT_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "aze_passport_rotated_00",
        "family": "passport",
        "file": "app/data/external/midv2020/raw/scan_rotated/images/aze_passport/00.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_aze_passport",
        "expected_document_type": "passport",
        "critical_fields": PASSPORT_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "aze_passport_rotated_01",
        "family": "passport",
        "file": "app/data/external/midv2020/raw/scan_rotated/images/aze_passport/01.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_aze_passport",
        "expected_document_type": "passport",
        "critical_fields": PASSPORT_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "srb_passport_upright_00",
        "family": "passport",
        "file": "app/data/external/midv2020/raw/scan_upright/images/srb_passport/00.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_srb_passport",
        "expected_document_type": "passport",
        "critical_fields": PASSPORT_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "srb_passport_upright_01",
        "family": "passport",
        "file": "app/data/external/midv2020/raw/scan_upright/images/srb_passport/01.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_srb_passport",
        "expected_document_type": "passport",
        "critical_fields": PASSPORT_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "srb_passport_rotated_00",
        "family": "passport",
        "file": "app/data/external/midv2020/raw/scan_rotated/images/srb_passport/00.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_srb_passport",
        "expected_document_type": "passport",
        "critical_fields": PASSPORT_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "srb_passport_rotated_01",
        "family": "passport",
        "file": "app/data/external/midv2020/raw/scan_rotated/images/srb_passport/01.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_srb_passport",
        "expected_document_type": "passport",
        "critical_fields": PASSPORT_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },

    # ------------------------------------------------------------------
    # SVK ID - mixed: one success, difficult cases controlled review
    # ------------------------------------------------------------------
    {
        "case_id": "svk_id_upright_00",
        "family": "id_document",
        "file": "app/data/external/midv2020/raw/scan_upright/images/svk_id/00.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "review_required",
        "expected_template_id": "midv_svk_id",
        "expected_document_type": "id_document",
        "critical_fields": SVK_CRITICAL_FIELDS,
        "expected_behavior": "controlled_review",
    },
    {
        "case_id": "svk_id_upright_01",
        "family": "id_document",
        "file": "app/data/external/midv2020/raw/scan_upright/images/svk_id/01.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "success",
        "expected_template_id": "midv_svk_id",
        "expected_document_type": "id_document",
        "critical_fields": SVK_CRITICAL_FIELDS,
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "svk_id_rotated_00",
        "family": "id_document",
        "file": "app/data/external/midv2020/raw/scan_rotated/images/svk_id/00.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "review_required",
        "expected_template_id": "midv_svk_id",
        "expected_document_type": "id_document",
        "critical_fields": SVK_CRITICAL_FIELDS,
        "expected_behavior": "controlled_review",
    },
    {
        "case_id": "svk_id_rotated_01",
        "family": "id_document",
        "file": "app/data/external/midv2020/raw/scan_rotated/images/svk_id/01.jpg",
        "document_type": "auto",
        "template_id": None,
        "engine": "easyocr",
        "processing_mode": "fast",
        "expected_status": "review_required",
        "expected_template_id": "midv_svk_id",
        "expected_document_type": "id_document",
        "critical_fields": SVK_CRITICAL_FIELDS,
        "expected_behavior": "controlled_review",
    },

    # ------------------------------------------------------------------
    # INVOICES - 2 clear success, 2 low-quality controlled review
    # ------------------------------------------------------------------
    {
        "case_id": "invoice_facture1_ttn_clear",
        "family": "invoice",
        "file": "app/data/samples/invoices/facture1.jpg",
        "document_type": "invoice",
        "template_id": "invoice_tn",
        "engine": "paddle",
        "processing_mode": "balanced",
        "expected_status": "success",
        "expected_template_id": "invoice_tn",
        "expected_document_type": "invoice",
        "critical_fields": INVOICE_CRITICAL_FIELDS,
        "expected_fields": {
            "invoice_number": "50",
            "invoice_date": "2015-04-03",
            "supplier_name": "Tunisie TradeNet",
            "total_ht": "135.500",
            "vat_amount": "16.260",
            "stamp_amount": "0.500",
            "total_ttc": "152.260",
            "currency": "TND",
            "amount_consistency": True,
        },
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "invoice_facture3_ttn_clear",
        "family": "invoice",
        "file": "app/data/samples/invoices/facture3.jpg",
        "document_type": "invoice",
        "template_id": "invoice_tn",
        "engine": "paddle",
        "processing_mode": "balanced",
        "expected_status": "success",
        "expected_template_id": "invoice_tn",
        "expected_document_type": "invoice",
        "critical_fields": INVOICE_CRITICAL_FIELDS,
        "expected_fields": {
            "invoice_number": "50",
            "invoice_date": "2015-04-03",
            "supplier_name": "Tunisie TradeNet",
            "total_ht": "135.500",
            "vat_amount": "16.260",
            "stamp_amount": "0.500",
            "total_ttc": "152.260",
            "currency": "TND",
            "amount_consistency": True,
        },
        "expected_behavior": "auto_success",
    },
    {
        "case_id": "invoice_facture2_low_quality",
        "family": "invoice",
        "file": "app/data/samples/invoices/facture2.png",
        "document_type": "invoice",
        "template_id": "invoice_tn",
        "engine": "paddle",
        "processing_mode": "balanced",
        "expected_status": "review_required",
        "expected_template_id": "invoice_tn",
        "expected_document_type": "invoice",
        "critical_fields": ["invoice_number", "invoice_date", "total_ttc"],
        "expected_behavior": "controlled_review",
    },
    {
        "case_id": "invoice_model_facture_2025_low_quality",
        "family": "invoice",
        "file": "app/data/samples/invoices/model-facture-2025.jpg",
        "document_type": "invoice",
        "template_id": "invoice_tn",
        "engine": "paddle",
        "processing_mode": "balanced",
        "expected_status": "review_required",
        "expected_template_id": "invoice_tn",
        "expected_document_type": "invoice",
        "critical_fields": ["invoice_number", "invoice_date", "total_ttc"],
        "expected_behavior": "controlled_review",
    },
]


def normalize_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    value = str(value).strip()

    if value.lower() == "true":
        return True

    if value.lower() == "false":
        return False

    return value


def field_exact_match(actual: Any, expected: Any) -> bool:
    actual = normalize_value(actual)
    expected = normalize_value(expected)

    if isinstance(expected, bool):
        return actual is expected

    return str(actual) == str(expected)


def get_field_map(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}

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
    timeout: int,
    save_responses_dir: Optional[Path],
) -> Dict[str, Any]:
    file_path = Path(case["file"])

    row: Dict[str, Any] = {
        "case_id": case["case_id"],
        "family": case["family"],
        "file": str(file_path),
        "file_exists": file_path.exists(),
        "expected_behavior": case.get("expected_behavior"),
        "expected_status": case.get("expected_status"),
        "expected_template_id": case.get("expected_template_id"),
        "expected_document_type": case.get("expected_document_type"),
        "http_status": None,
        "http_ok": False,
        "api_status": None,
        "template_id": None,
        "document_type": None,
        "engine_used": None,
        "strategy": None,
        "processing_time_ms": None,
        "critical_valid_count": 0,
        "critical_total": len(case.get("critical_fields", [])),
        "expected_match_count": 0,
        "expected_total": len(case.get("expected_fields", {}) or {}),
        "missing_expected": "",
        "wrong_expected": "",
        "critical_missing_or_invalid": "",
        "status_ok": False,
        "template_ok": False,
        "document_type_ok": False,
        "behavior_pass": False,
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

    started = time.perf_counter()

    try:
        with file_path.open("rb") as f:
            response = requests.post(
                url,
                headers={"X-API-Key": api_key},
                files={"file": (file_path.name, f, "application/octet-stream")},
                data=data,
                timeout=timeout,
            )

        row["http_status"] = response.status_code
        row["http_ok"] = 200 <= response.status_code < 300

        try:
            payload = response.json()
        except Exception:
            row["error"] = "response_not_json"
            row["raw_response"] = response.text[:500]
            return row

    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    diagnostics = payload.get("diagnostics") or {}
    field_map = get_field_map(payload)

    row["api_status"] = payload.get("status")
    row["template_id"] = payload.get("template_id")
    row["document_type"] = payload.get("document_type")
    row["engine_used"] = payload.get("engine_used")
    row["strategy"] = diagnostics.get("strategy")
    row["processing_time_ms"] = payload.get("processing_time_ms") or elapsed_ms

    row["status_ok"] = row["api_status"] == case.get("expected_status")
    row["template_ok"] = row["template_id"] == case.get("expected_template_id")
    row["document_type_ok"] = row["document_type"] == case.get("expected_document_type")

    expected_fields = case.get("expected_fields", {}) or {}
    missing_expected: List[str] = []
    wrong_expected: List[str] = []
    expected_match_count = 0

    for name, expected_value in expected_fields.items():
        field = field_map.get(name)

        if not field:
            missing_expected.append(name)
            continue

        actual_value = field.get("value")
        validated = bool(field.get("validated"))

        if validated and field_exact_match(actual_value, expected_value):
            expected_match_count += 1
        else:
            wrong_expected.append(
                f"{name}: expected={expected_value!r}, actual={actual_value!r}, valid={validated}"
            )

    critical_fields = case.get("critical_fields", []) or []
    critical_missing_or_invalid: List[str] = []
    critical_valid_count = 0

    for name in critical_fields:
        field = field_map.get(name)

        if not field:
            critical_missing_or_invalid.append(name)
            continue

        if field.get("validated") and field.get("value") not in (None, "", []):
            critical_valid_count += 1
        else:
            critical_missing_or_invalid.append(
                f"{name}:{field.get('error') or 'invalid'}"
            )

    row["critical_valid_count"] = critical_valid_count
    row["expected_match_count"] = expected_match_count
    row["missing_expected"] = "; ".join(missing_expected)
    row["wrong_expected"] = "; ".join(wrong_expected)
    row["critical_missing_or_invalid"] = "; ".join(critical_missing_or_invalid)

    expected_behavior = case.get("expected_behavior")

    if expected_behavior == "auto_success":
        row["behavior_pass"] = (
            row["http_ok"]
            and row["status_ok"]
            and row["template_ok"]
            and row["document_type_ok"]
            and critical_valid_count == len(critical_fields)
            and not missing_expected
            and not wrong_expected
        )

    elif expected_behavior == "controlled_review":
        row["behavior_pass"] = (
            row["http_ok"]
            and row["status_ok"]
            and row["template_ok"]
            and row["document_type_ok"]
        )

    else:
        row["behavior_pass"] = False
        row["error"] = "unknown_expected_behavior"

    if save_responses_dir:
        save_responses_dir.mkdir(parents=True, exist_ok=True)
        response_path = save_responses_dir / f"{case['case_id']}.json"

        with response_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        row["response_file"] = str(response_path)

    return row


def build_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    http_ok = sum(1 for row in rows if row.get("http_ok"))
    behavior_passed = sum(1 for row in rows if row.get("behavior_pass"))

    success_count = sum(1 for row in rows if row.get("api_status") == "success")
    review_count = sum(1 for row in rows if row.get("api_status") == "review_required")
    partial_count = sum(1 for row in rows if row.get("api_status") == "partial")
    failed_count = sum(1 for row in rows if row.get("api_status") == "failed")

    by_family: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        family = row.get("family") or "unknown"

        if family not in by_family:
            by_family[family] = {
                "cases": 0,
                "http_ok": 0,
                "success": 0,
                "review_required": 0,
                "partial": 0,
                "failed": 0,
                "behavior_passed": 0,
            }

        item = by_family[family]
        item["cases"] += 1

        if row.get("http_ok"):
            item["http_ok"] += 1

        status = row.get("api_status")

        if status == "success":
            item["success"] += 1
        elif status == "review_required":
            item["review_required"] += 1
        elif status == "partial":
            item["partial"] += 1
        elif status == "failed":
            item["failed"] += 1

        if row.get("behavior_pass"):
            item["behavior_passed"] += 1

    return {
        "total_cases": total,
        "http_ok": http_ok,
        "success": success_count,
        "review_required": review_count,
        "partial": partial_count,
        "failed": failed_count,
        "behavior_passed": behavior_passed,
        "automatic_success_rate": round(success_count / total, 4) if total else 0.0,
        "controlled_review_rate": round(review_count / total, 4) if total else 0.0,
        "technical_failure_rate": round(failed_count / total, 4) if total else 0.0,
        "by_family": by_family,
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "case_id",
        "family",
        "file",
        "file_exists",
        "expected_behavior",
        "expected_status",
        "expected_template_id",
        "expected_document_type",
        "http_status",
        "http_ok",
        "api_status",
        "template_id",
        "document_type",
        "engine_used",
        "strategy",
        "processing_time_ms",
        "critical_valid_count",
        "critical_total",
        "expected_match_count",
        "expected_total",
        "missing_expected",
        "wrong_expected",
        "critical_missing_or_invalid",
        "status_ok",
        "template_ok",
        "document_type_ok",
        "behavior_pass",
        "error",
        "response_file",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()

        for row in rows:
            writer.writerow(row)


def write_json(path: Path, rows: List[Dict[str, Any]], summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": summary,
                "results": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Final global benchmark for passport, ID and invoice extraction."
    )

    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
    )
    parser.add_argument(
        "--api-key",
        default="dev-key-123",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
    )
    parser.add_argument(
        "--out-csv",
        default="benchmark_global_final.csv",
    )
    parser.add_argument(
        "--out-json",
        default="benchmark_global_final.json",
    )
    parser.add_argument(
        "--save-responses-dir",
        default="benchmark_global_final_responses",
    )

    args = parser.parse_args()

    save_responses_dir = (
        Path(args.save_responses_dir)
        if args.save_responses_dir
        else None
    )

    rows: List[Dict[str, Any]] = []

    for case in CASES:
        print(f"[RUN] {case['case_id']}")

        row = run_case(
            case=case,
            base_url=args.base_url,
            api_key=args.api_key,
            timeout=args.timeout,
            save_responses_dir=save_responses_dir,
        )

        rows.append(row)

        label = "PASS" if row.get("behavior_pass") else "FAIL"

        print(
            f"[{label}] family={row.get('family')} "
            f"http={row.get('http_status')} "
            f"api={row.get('api_status')} "
            f"template={row.get('template_id')} "
            f"critical={row.get('critical_valid_count')}/{row.get('critical_total')} "
            f"expected={row.get('expected_match_count')}/{row.get('expected_total')}"
        )

        if row.get("critical_missing_or_invalid"):
            print(f"      critical_missing={row.get('critical_missing_or_invalid')}")

        if row.get("wrong_expected"):
            print(f"      wrong={row.get('wrong_expected')}")

        if row.get("missing_expected"):
            print(f"      missing={row.get('missing_expected')}")

        if row.get("error"):
            print(f"      error={row.get('error')}")

    summary = build_summary(rows)

    write_csv(Path(args.out_csv), rows)
    write_json(Path(args.out_json), rows, summary)

    print()
    print("[SUMMARY]")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[OK] wrote {args.out_csv}")
    print(f"[OK] wrote {args.out_json}")

    return 0 if summary["behavior_passed"] == summary["total_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())