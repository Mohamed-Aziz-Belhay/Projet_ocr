"""
app/services/benchmark_service.py
Exposes benchmark functionality as a proper service class,
callable from both API routes and CLI.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.evaluation.benchmark_runner import run_benchmark
from app.core.logging import get_logger

log = get_logger(__name__)


class BenchmarkService:

    def run_from_file(
        self,
        cases_path: str,
        save_report: bool = True,
    ) -> Dict[str, Any]:
        """Run benchmark from a JSON cases file."""
        if not Path(cases_path).exists():
            raise FileNotFoundError(f"Benchmark cases file not found: {cases_path}")

        output_path = None
        if save_report:
            ts = int(time.time())
            output_path = f"app/evaluation/reports/report_{ts}.json"

        report = run_benchmark(cases_path, output_path=output_path)
        log.info(
            "Benchmark complete",
            extra={
                "cases": report["total_cases"],
                "global_f1": report["global_f1"],
                "report": output_path,
            },
        )
        return report

    def run_from_cases(
        self,
        cases: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Run benchmark from an in-memory list of cases."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(cases, f)
            tmp_path = f.name
        try:
            return self.run_from_file(tmp_path, save_report=False)
        finally:
            os.unlink(tmp_path)

    def list_reports(self) -> List[Dict[str, Any]]:
        """List all saved benchmark reports."""
        reports_dir = Path("app/evaluation/reports")
        if not reports_dir.exists():
            return []
        reports = []
        for p in sorted(reports_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                reports.append({
                    "file": p.name,
                    "total_cases": data.get("total_cases"),
                    "global_f1": data.get("global_f1"),
                })
            except Exception:
                pass
        return reports

    def get_report(self, filename: str) -> Optional[Dict[str, Any]]:
        path = Path("app/evaluation/reports") / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


_svc: Optional[BenchmarkService] = None


def get_benchmark_service() -> BenchmarkService:
    global _svc
    if _svc is None:
        _svc = BenchmarkService()
    return _svc