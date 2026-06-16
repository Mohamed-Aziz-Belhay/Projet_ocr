from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


DEFAULT_CASES = [
    {
        "case_id": "facture1_ttn_clear",
        "file": "app/data/samples/invoices/facture1.jpg",
        "expected_status": None,
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
        "critical_fields": [
            "invoice_number",
            "invoice_date",
            "total_ttc",
            "amount_consistency",
        ],
        "allow_review_required": False,
    },
    {
        "case_id": "facture3_ttn_clear",
        "file": "app/data/samples/invoices/facture3.jpg",
        "expected_status": None,
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
        "critical_fields": [
            "invoice_number",
            "invoice_date",
            "total_ttc",
            "amount_consistency",
        ],
        "allow_review_required": False,
    },
    {
        "case_id": "facture2_low_quality",
        "file": "app/data/samples/invoices/facture2.png",
        "expected_status": "review_required",
        "expected_fields": {},
        "critical_fields": [
            "invoice_number",
            "invoice_date",
            "total_ttc",
        ],
        "allow_review_required": True,
    },
    {
        "case_id": "model_facture_2025_low_quality",
        "file": "app/data/samples/invoices/model-facture-2025.jpg",
        "expected_status": "review_required",
        "expected_fields": {},
        "critical_fields": [
            "invoice_number",
            "invoice_date",
            "total_ttc",
        ],
        "allow_review_required": True,
    },
]


def normalize_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return str(value)

    value = str(value).strip()

    if value.lower() in {"true", "false"}:
        return value.lower() == "true"

    return value


def get_field_map(response: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}

    for field in response.get("fields", []) or []:
        name = field.get("name")

        if name:
            out[name] = field

    return out


def field_exact_match(actual: Any, expected: Any) -> bool:
    actual = normalize_value(actual)
    expected = normalize_value(expected)

    if isinstance(expected, bool):
        return actual is expected

    return str(actual) == str(expected)


def run_case(
    *,
    case: Dict[str, Any],
    base_url: str,
    api_key: str,
    engine: str,
    processing_mode: str,
    timeout: int,
    save_responses_dir: Optional[Path],
) -> Dict[str, Any]:
    file_path = Path(case["file"])

    result: Dict[str, Any] = {
        "case_id": case["case_id"],
        "file": str(file_path),
        "file_exists": file_path.exists(),
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
        "expected_total": len(case.get("expected_fields", {})),
        "missing_expected": "",
        "wrong_expected": "",
        "critical_missing_or_invalid": "",
        "benchmark_pass": False,
        "error": None,
    }

    if not file_path.exists():
        result["error"] = "file_not_found"
        return result

    url = base_url.rstrip("/") + "/extract"

    started = time.perf_counter()

    try:
        with file_path.open("rb") as f:
            files = {
                "file": (file_path.name, f, "application/octet-stream"),
            }

            data = {
                "document_type": "invoice",
                "template_id": "invoice_tn",
                "engine": engine,
                "processing_mode": processing_mode,
                "include_diagnostics": "true",
            }

            headers = {
                "X-API-Key": api_key,
            }

            response = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=timeout,
            )

        result["http_status"] = response.status_code
        result["http_ok"] = 200 <= response.status_code < 300

        try:
            payload = response.json()
        except Exception:
            result["error"] = "response_not_json"
            result["raw_response"] = response.text[:500]
            return result

    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    diagnostics = payload.get("diagnostics") or {}
    field_map = get_field_map(payload)

    result["api_status"] = payload.get("status")
    result["template_id"] = payload.get("template_id")
    result["document_type"] = payload.get("document_type")
    result["engine_used"] = payload.get("engine_used")
    result["strategy"] = diagnostics.get("strategy")
    result["processing_time_ms"] = payload.get("processing_time_ms") or elapsed_ms

    expected_fields: Dict[str, Any] = case.get("expected_fields", {}) or {}
    critical_fields: List[str] = case.get("critical_fields", []) or []

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

    result["critical_valid_count"] = critical_valid_count
    result["expected_match_count"] = expected_match_count
    result["missing_expected"] = "; ".join(missing_expected)
    result["wrong_expected"] = "; ".join(wrong_expected)
    result["critical_missing_or_invalid"] = "; ".join(critical_missing_or_invalid)

    expected_status = case.get("expected_status")
    allow_review_required = bool(case.get("allow_review_required"))

    if expected_fields:
        result["benchmark_pass"] = (
            result["http_ok"]
            and result["strategy"] == "invoice_raw_text_rules"
            and expected_match_count == len(expected_fields)
            and not wrong_expected
            and not missing_expected
        )
    elif allow_review_required:
        result["benchmark_pass"] = (
            result["http_ok"]
            and result["strategy"] == "invoice_raw_text_rules"
            and payload.get("status") == expected_status
        )
    else:
        result["benchmark_pass"] = (
            result["http_ok"]
            and result["strategy"] == "invoice_raw_text_rules"
            and critical_valid_count == len(critical_fields)
        )

    if save_responses_dir:
        save_responses_dir.mkdir(parents=True, exist_ok=True)
        response_path = save_responses_dir / f"{case['case_id']}.json"

        with response_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        result["response_file"] = str(response_path)

    return result


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "case_id",
        "file",
        "file_exists",
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
        "benchmark_pass",
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

    payload = {
        "summary": summary,
        "results": rows,
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    http_ok = sum(1 for row in rows if row.get("http_ok"))
    passed = sum(1 for row in rows if row.get("benchmark_pass"))
    strategy_ok = sum(1 for row in rows if row.get("strategy") == "invoice_raw_text_rules")

    strict_cases = [row for row in rows if row.get("expected_total", 0) > 0]
    strict_passed = sum(1 for row in strict_cases if row.get("benchmark_pass"))

    review_cases = [row for row in rows if row.get("expected_total", 0) == 0]
    review_passed = sum(1 for row in review_cases if row.get("benchmark_pass"))

    return {
        "total_cases": total,
        "http_ok": http_ok,
        "strategy_invoice_rules_ok": strategy_ok,
        "benchmark_passed": passed,
        "strict_expected_cases": len(strict_cases),
        "strict_expected_passed": strict_passed,
        "review_expected_cases": len(review_cases),
        "review_expected_passed": review_passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark invoice extraction through the /extract API."
    )

    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="API base URL.",
    )
    parser.add_argument(
        "--api-key",
        default="dev-key-123",
        help="API key.",
    )
    parser.add_argument(
        "--engine",
        default="paddle",
        help="OCR engine to use.",
    )
    parser.add_argument(
        "--processing-mode",
        default="balanced",
        choices=["fast", "balanced", "full"],
        help="Processing mode.",
    )
    parser.add_argument(
        "--out-csv",
        default="benchmark_invoice_results.csv",
        help="Output CSV path.",
    )
    parser.add_argument(
        "--out-json",
        default="benchmark_invoice_results.json",
        help="Output JSON path.",
    )
    parser.add_argument(
        "--save-responses-dir",
        default="benchmark_invoice_responses",
        help="Directory for full API responses. Use empty string to disable.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds.",
    )

    args = parser.parse_args()

    save_responses_dir = (
        Path(args.save_responses_dir)
        if args.save_responses_dir
        else None
    )

    rows: List[Dict[str, Any]] = []

    for case in DEFAULT_CASES:
        print(f"[RUN] {case['case_id']} -> {case['file']}")

        row = run_case(
            case=case,
            base_url=args.base_url,
            api_key=args.api_key,
            engine=args.engine,
            processing_mode=args.processing_mode,
            timeout=args.timeout,
            save_responses_dir=save_responses_dir,
        )

        rows.append(row)

        status = "PASS" if row.get("benchmark_pass") else "FAIL"
        print(
            f"[{status}] http={row.get('http_status')} "
            f"api={row.get('api_status')} "
            f"strategy={row.get('strategy')} "
            f"critical={row.get('critical_valid_count')}/{row.get('critical_total')} "
            f"expected={row.get('expected_match_count')}/{row.get('expected_total')}"
        )

        if row.get("error"):
            print(f"      error={row.get('error')}")

        if row.get("wrong_expected"):
            print(f"      wrong={row.get('wrong_expected')}")

        if row.get("missing_expected"):
            print(f"      missing={row.get('missing_expected')}")

    summary = build_summary(rows)

    write_csv(Path(args.out_csv), rows)
    write_json(Path(args.out_json), rows, summary)

    print()
    print("[SUMMARY]")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[OK] wrote {args.out_csv}")
    print(f"[OK] wrote {args.out_json}")

    return 0 if summary["benchmark_passed"] == summary["total_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())