from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


DEFAULT_CLASSES = [
    "aze_passport",
    "srb_passport",
    "svk_id",
]


def infer_expected_document_type(class_name: str) -> str:
    low = class_name.lower()

    if "passport" in low:
        return "passport"

    if low == "cin_tn" or "cin" in low:
        return "cin_tn"

    return "id_document"


def infer_expected_template(class_name: str) -> str:
    if class_name == "cin_tn":
        return "cin_tn"

    return f"midv_{class_name}"


def critical_fields_for(class_name: str, expected_document_type: str) -> List[str]:
    low = class_name.lower()

    if expected_document_type == "passport" or "passport" in low:
        return [
            "document_number",
            "surname",
            "given_names",
            "nationality",
            "birth_date",
            "expiry_date",
            "mrz",
        ]

    if expected_document_type == "cin_tn" or "cin" in low:
        return [
            "id_number",
            "last_name",
            "first_name",
            "birth_date",
        ]

    # MIDV ID card generic fields.
    return [
        "number",
        "id_number",
        "birth_date",
        "expiry_date",
        "name",
        "surname",
    ]


def discover_cases(
    *,
    raw_root: Path,
    classes: List[str],
    variants: List[str],
    max_per_class: int,
) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []

    for variant in variants:
        images_root = raw_root / variant / "images"

        if not images_root.exists():
            continue

        for class_name in classes:
            class_dir = images_root / class_name

            if not class_dir.exists():
                continue

            image_paths = sorted(
                list(class_dir.glob("*.jpg"))
                + list(class_dir.glob("*.jpeg"))
                + list(class_dir.glob("*.png"))
            )

            if max_per_class > 0:
                image_paths = image_paths[:max_per_class]

            for image_path in image_paths:
                expected_document_type = infer_expected_document_type(class_name)
                expected_template = infer_expected_template(class_name)

                cases.append(
                    {
                        "case_name": f"{variant}__{class_name}__{image_path.stem}",
                        "variant": variant,
                        "class_name": class_name,
                        "image_path": str(image_path),
                        "expected_document_type": expected_document_type,
                        "expected_template": expected_template,
                        "critical_fields": critical_fields_for(
                            class_name,
                            expected_document_type,
                        ),
                    }
                )

    return cases


def field_map(response_json: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(field.get("name")): field
        for field in response_json.get("fields", []) or []
        if field.get("name")
    }


def get_nested(data: Dict[str, Any], path: List[str], default=None):
    cur = data

    for key in path:
        if not isinstance(cur, dict):
            return default

        cur = cur.get(key)

        if cur is None:
            return default

    return cur


def selected_angle_from_diagnostics(diagnostics: Dict[str, Any]) -> Optional[Any]:
    passport = diagnostics.get("passport_extraction") or {}

    if isinstance(passport, dict) and passport.get("selected_angle") is not None:
        return passport.get("selected_angle")

    roi = diagnostics.get("roi_extraction") or {}

    if isinstance(roi, dict) and roi.get("selected_angle") is not None:
        return roi.get("selected_angle")

    return None


def localizer_method_from_diagnostics(diagnostics: Dict[str, Any]) -> Optional[str]:
    localizer = diagnostics.get("document_localizer") or {}

    if isinstance(localizer, dict):
        return localizer.get("method")

    return None


def summarize_response(
    *,
    case: Dict[str, Any],
    response_json: Dict[str, Any],
    status_code: int,
    elapsed_ms: int,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    fmap = field_map(response_json)

    critical_fields = case["critical_fields"]
    critical_missing = []
    critical_values: Dict[str, Any] = {}

    for name in critical_fields:
        field = fmap.get(name)
        value = field.get("value") if field else None
        valid = bool(field.get("validated")) if field else False
        err = field.get("error") if field else "missing_field"

        critical_values[name] = {
            "value": value,
            "valid": valid,
            "error": err,
        }

        if not valid:
            critical_missing.append(name)

    template_id = response_json.get("template_id")
    document_type = response_json.get("document_type")
    diagnostics = response_json.get("diagnostics") or {}
    routing = response_json.get("routing") or {}

    expected_template = case.get("expected_template")
    expected_document_type = case.get("expected_document_type")

    template_ok = (
        not expected_template
        or template_id == expected_template
    )

    document_type_ok = (
        not expected_document_type
        or document_type == expected_document_type
    )

    fields = response_json.get("fields", []) or []
    valid_fields = sum(1 for f in fields if f.get("validated"))
    present_fields = sum(1 for f in fields if f.get("value") not in (None, "", []))

    summary = {
        "case_name": case["case_name"],
        "variant": case["variant"],
        "class_name": case["class_name"],
        "image_path": case["image_path"],
        "status_code": status_code,
        "http_ok": 200 <= status_code < 300,
        "api_status": response_json.get("status"),
        "expected_template": expected_template,
        "template_id": template_id,
        "template_ok": template_ok,
        "expected_document_type": expected_document_type,
        "document_type": document_type,
        "document_type_ok": document_type_ok,
        "engine_used": response_json.get("engine_used"),
        "processing_time_ms": response_json.get("processing_time_ms"),
        "client_elapsed_ms": elapsed_ms,
        "global_confidence": response_json.get("global_confidence"),
        "routing_method": routing.get("method"),
        "routing_confidence": routing.get("confidence"),
        "localizer_method": localizer_method_from_diagnostics(diagnostics),
        "selected_angle": selected_angle_from_diagnostics(diagnostics),
        "field_count": len(fields),
        "valid_field_count": valid_fields,
        "present_field_count": present_fields,
        "critical_total": len(critical_fields),
        "critical_valid_count": len(critical_fields) - len(critical_missing),
        "critical_missing": critical_missing,
        "critical_pass": len(critical_missing) == 0,
        "warnings_count": len(response_json.get("warnings", []) or []),
        "review_reasons_count": len(response_json.get("review_reasons", []) or []),
        "critical_values": critical_values,
        "error": error,
    }

    # Global pass for benchmark.
    summary["benchmark_pass"] = bool(
        summary["http_ok"]
        and template_ok
        and document_type_ok
        and summary["api_status"] in {"success", "review_required", "partial"}
        and summary["critical_pass"]
    )

    return summary


def post_extract(
    *,
    client: httpx.Client,
    base_url: str,
    api_key: str,
    image_path: Path,
    engine: str,
    processing_mode: str,
    include_diagnostics: bool,
    timeout_s: float,
) -> tuple[int, Dict[str, Any], int, Optional[str]]:
    url = base_url.rstrip("/") + "/extract"

    data = {
        "document_type": "auto",
        "engine": engine,
        "processing_mode": processing_mode,
        "include_diagnostics": "true" if include_diagnostics else "false",
    }

    headers = {
        "X-API-Key": api_key,
        "accept": "application/json",
    }

    started = time.perf_counter()

    try:
        with image_path.open("rb") as f:
            files = {
                "file": (
                    image_path.name,
                    f,
                    "image/jpeg",
                )
            }

            response = client.post(
                url,
                data=data,
                files=files,
                headers=headers,
                timeout=timeout_s,
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)

        try:
            payload = response.json()
        except Exception:
            payload = {
                "status": "failed",
                "error": response.text[:1000],
            }

        return response.status_code, payload, elapsed_ms, None

    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        return 0, {}, elapsed_ms, f"{type(exc).__name__}: {exc}"


def write_csv(path: Path, summaries: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "case_name",
        "variant",
        "class_name",
        "image_path",
        "status_code",
        "http_ok",
        "api_status",
        "expected_template",
        "template_id",
        "template_ok",
        "expected_document_type",
        "document_type",
        "document_type_ok",
        "engine_used",
        "processing_time_ms",
        "client_elapsed_ms",
        "global_confidence",
        "routing_method",
        "routing_confidence",
        "localizer_method",
        "selected_angle",
        "field_count",
        "valid_field_count",
        "present_field_count",
        "critical_total",
        "critical_valid_count",
        "critical_missing",
        "critical_pass",
        "warnings_count",
        "review_reasons_count",
        "benchmark_pass",
        "critical_values",
        "error",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in summaries:
            row_out = dict(row)
            row_out["critical_missing"] = json.dumps(
                row.get("critical_missing", []),
                ensure_ascii=False,
            )
            row_out["critical_values"] = json.dumps(
                row.get("critical_values", {}),
                ensure_ascii=False,
            )

            writer.writerow(row_out)


def write_json(path: Path, data: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


def print_summary(summaries: List[Dict[str, Any]]) -> None:
    total = len(summaries)

    if total == 0:
        print("[WARN] No benchmark cases found.")
        return

    passed = sum(1 for s in summaries if s.get("benchmark_pass"))
    http_ok = sum(1 for s in summaries if s.get("http_ok"))
    critical_ok = sum(1 for s in summaries if s.get("critical_pass"))

    print()
    print("========== BENCHMARK SUMMARY ==========")
    print(f"Total cases       : {total}")
    print(f"HTTP OK           : {http_ok}/{total}")
    print(f"Critical fields OK: {critical_ok}/{total}")
    print(f"Benchmark pass    : {passed}/{total}")
    print()

    for s in summaries:
        mark = "OK" if s.get("benchmark_pass") else "FAIL"

        print(
            f"[{mark}] {s['case_name']} | "
            f"status={s.get('api_status')} | "
            f"template={s.get('template_id')} | "
            f"doc={s.get('document_type')} | "
            f"engine={s.get('engine_used')} | "
            f"crit={s.get('critical_valid_count')}/{s.get('critical_total')} | "
            f"missing={s.get('critical_missing')} | "
            f"time={s.get('processing_time_ms')}ms"
        )

    print("=======================================")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark OCR /extract endpoint on MIDV and project documents."
    )

    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="FastAPI base URL.",
    )

    parser.add_argument(
        "--api-key",
        default="dev-key-123",
        help="API key for X-API-Key header.",
    )

    parser.add_argument(
        "--raw-root",
        default="app/data/external/midv2020/raw",
        help="MIDV raw dataset root.",
    )

    parser.add_argument(
        "--classes",
        default=",".join(DEFAULT_CLASSES),
        help="Comma-separated class names to test.",
    )

    parser.add_argument(
        "--variants",
        default="scan_upright,scan_rotated",
        help="Comma-separated variants to test.",
    )

    parser.add_argument(
        "--max-per-class",
        type=int,
        default=2,
        help="Max images per class and variant. Use 0 for all.",
    )

    parser.add_argument(
        "--engine",
        default="easyocr",
        help="Engine value sent to API.",
    )

    parser.add_argument(
        "--processing-mode",
        default="fast",
        choices=["fast", "balanced", "full"],
        help="Processing mode sent to API.",
    )

    parser.add_argument(
        "--include-diagnostics",
        action="store_true",
        default=True,
        help="Include diagnostics in API response.",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="HTTP timeout seconds per image.",
    )

    parser.add_argument(
        "--out-csv",
        default="benchmark_results.csv",
        help="Output CSV file.",
    )

    parser.add_argument(
        "--out-json",
        default="benchmark_results.json",
        help="Output JSON file.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    raw_root = Path(args.raw_root)
    classes = [
        item.strip()
        for item in args.classes.split(",")
        if item.strip()
    ]
    variants = [
        item.strip()
        for item in args.variants.split(",")
        if item.strip()
    ]

    cases = discover_cases(
        raw_root=raw_root,
        classes=classes,
        variants=variants,
        max_per_class=args.max_per_class,
    )

    print(f"[INFO] Found {len(cases)} benchmark cases.")

    if not cases:
        print("[ERROR] No images found. Check --raw-root, --classes and --variants.")
        return 2

    summaries: List[Dict[str, Any]] = []

    with httpx.Client() as client:
        for idx, case in enumerate(cases, start=1):
            image_path = Path(case["image_path"])

            print(f"[{idx}/{len(cases)}] Testing {case['case_name']}")

            status_code, payload, elapsed_ms, error = post_extract(
                client=client,
                base_url=args.base_url,
                api_key=args.api_key,
                image_path=image_path,
                engine=args.engine,
                processing_mode=args.processing_mode,
                include_diagnostics=args.include_diagnostics,
                timeout_s=args.timeout,
            )

            summary = summarize_response(
                case=case,
                response_json=payload,
                status_code=status_code,
                elapsed_ms=elapsed_ms,
                error=error,
            )

            summaries.append(summary)

    write_csv(Path(args.out_csv), summaries)
    write_json(Path(args.out_json), summaries)

    print_summary(summaries)

    print(f"[OK] CSV written : {args.out_csv}")
    print(f"[OK] JSON written: {args.out_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())