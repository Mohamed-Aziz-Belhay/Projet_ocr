"""
app/evaluation/benchmark_runner.py
Phase-D: evaluate extraction quality on labeled test cases.
Produces precision / recall / F1 per field per template.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.pipeline.runner import run_pipeline
from app.services.template_service import get_template_service
from app.core.logging import get_logger

log = get_logger(__name__)


def _normalize_val(v: Any) -> str:
    return str(v).strip().lower().replace(" ", "") if v is not None else ""


def run_benchmark(cases_path: str, output_path: Optional[str] = None) -> Dict:
    """
    cases_path: JSON file with list of:
      {
        "file": "path/to/image.png",
        "template_id": "invoice_generic",
        "expected": {"invoice_number": "F2024-001", ...}
      }
    """
    with open(cases_path, encoding="utf-8") as f:
        cases: List[dict] = json.load(f)

    templates = get_template_service()

    results = []
    field_stats: Dict[str, Dict] = {}   # field_name → {tp, fp, fn}

    for case in cases:
        template_id = case.get("template_id")
        expected: Dict[str, Any] = case.get("expected", {})
        template = templates.get(template_id) if template_id else None

        t0 = time.time()
        result = run_pipeline(
            file_path=case["file"],
            template=template,
        )
        elapsed_ms = int((time.time() - t0) * 1000)

        extracted = {f.name: f.value for f in result.fields}

        case_result = {
            "file": case["file"],
            "template": template_id,
            "elapsed_ms": elapsed_ms,
            "fields": {},
        }

        for field_name, exp_val in expected.items():
            got_val = extracted.get(field_name)
            match = _normalize_val(got_val) == _normalize_val(exp_val)

            stats = field_stats.setdefault(field_name, {"tp": 0, "fp": 0, "fn": 0})
            if match:
                stats["tp"] += 1
            elif got_val is not None:
                stats["fp"] += 1
            else:
                stats["fn"] += 1

            case_result["fields"][field_name] = {
                "expected": exp_val,
                "got": got_val,
                "match": match,
            }

        results.append(case_result)

    # Compute per-field metrics
    metrics: Dict[str, dict] = {}
    for field_name, stats in field_stats.items():
        tp, fp, fn = stats["tp"], stats["fp"], stats["fn"]
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        metrics[field_name] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "tp": tp, "fp": fp, "fn": fn,
        }

    # Global
    all_tp = sum(s["tp"] for s in field_stats.values())
    all_fp = sum(s["fp"] for s in field_stats.values())
    all_fn = sum(s["fn"] for s in field_stats.values())
    global_f1 = 2 * all_tp / (2 * all_tp + all_fp + all_fn) if (2 * all_tp + all_fp + all_fn) > 0 else 0.0

    report = {
        "total_cases": len(cases),
        "global_f1": round(global_f1, 3),
        "per_field": metrics,
        "cases": results,
    }

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        log.info("Benchmark saved", extra={"path": output_path, "global_f1": global_f1})

    return report


if __name__ == "__main__":
    import sys
    cases = sys.argv[1] if len(sys.argv) > 1 else "app/evaluation/example_benchmark_cases.json"
    out = "app/evaluation/reports/latest.json"
    report = run_benchmark(cases, out)
    print(f"Global F1: {report['global_f1']}")
    for field, m in report["per_field"].items():
        print(f"  {field:30s} P={m['precision']:.2f}  R={m['recall']:.2f}  F1={m['f1']:.2f}")