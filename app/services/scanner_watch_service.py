#scanner_watch_service.py
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger
from app.db.models.extraction_history import ExtractionHistory
from app.db.models.extraction_result import ExtractionResult
from app.db.session import AsyncSessionLocal
from app.schemas.ocr import ExtractionRequest
from app.services.document_orchestrator import get_orchestrator
from app.services.scanner_session_service import get_active_scanner_user

log = get_logger(__name__)


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".pdf",
    ".tif",
    ".tiff",
    ".webp",
}


class ScannerWatchService:
    def __init__(
        self,
        *,
        base_dir: str = r"C:\OCR_SCANS",
        document_type: str = "auto",
        template_id: Optional[str] = None,
        engine: str = "auto",
        processing_mode: str = "balanced",
        cin_mode: str = "balanced",
        language_hint: Optional[str] = None,
        include_diagnostics: bool = True,
        interval_s: float = 2.0,
    ):
        self.base_dir = Path(base_dir)
        self.input_dir = self.base_dir / "IN"
        self.processing_dir = self.base_dir / "PROCESSING"
        self.done_dir = self.base_dir / "DONE"
        self.error_dir = self.base_dir / "ERROR"
        self.output_dir = self.base_dir / "OUT"

        self.document_type = document_type
        self.template_id = template_id
        self.engine = engine
        self.processing_mode = processing_mode
        self.cin_mode = cin_mode
        self.language_hint = language_hint
        self.include_diagnostics = include_diagnostics
        self.interval_s = interval_s

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._seen: set[str] = set()

    def ensure_dirs(self) -> None:
        for folder in [
            self.input_dir,
            self.processing_dir,
            self.done_dir,
            self.error_dir,
            self.output_dir,
        ]:
            folder.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        if self._task and not self._task.done():
            log.info("Scanner watcher déjà actif")
            return

        self.ensure_dirs()
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())

        log.info(
            "Scanner watcher démarré",
            extra={
                "input_dir": str(self.input_dir),
                "output_dir": str(self.output_dir),
            },
        )

    async def stop(self) -> None:
        self._running = False

        if self._task and not self._task.done():
            self._task.cancel()

            try:
                await self._task
            except asyncio.CancelledError:
                pass

        log.info("Scanner watcher arrêté")

    async def _watch_loop(self) -> None:
        while self._running:
            try:
                await self._scan_once()
            except Exception as exc:
                log.exception(
                    "Erreur boucle scanner watcher",
                    extra={"error": str(exc)},
                )

            await asyncio.sleep(self.interval_s)

    async def _scan_once(self) -> None:
        self.ensure_dirs()

        files = [
            p for p in sorted(self.input_dir.iterdir())
            if self._is_supported_file(p)
        ]

        for path in files:
            key = str(path.resolve())

            if key in self._seen:
                continue

            self._seen.add(key)

            if not await self._wait_until_file_is_stable(path):
                log.warning(
                    "Fichier scanner ignoré car instable",
                    extra={"path": str(path)},
                )
                continue

            await self._process_file(path)

    def _is_supported_file(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS

    async def _wait_until_file_is_stable(
        self,
        path: Path,
        timeout_s: int = 30,
    ) -> bool:
        start = asyncio.get_event_loop().time()
        last_size = -1
        stable_count = 0

        while asyncio.get_event_loop().time() - start < timeout_s:
            if not path.exists():
                return False

            try:
                size = path.stat().st_size
            except OSError:
                await asyncio.sleep(1)
                continue

            if size > 0 and size == last_size:
                stable_count += 1
            else:
                stable_count = 0

            if stable_count >= 2:
                return True

            last_size = size
            await asyncio.sleep(1)

        return False

    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _safe_name(self, path: Path) -> str:
        stem = path.stem.replace(" ", "_")
        return f"{stem}_{self._stamp()}_{uuid.uuid4().hex[:8]}{path.suffix.lower()}"

    async def _process_file(self, path: Path) -> None:
        work_name = self._safe_name(path)
        work_path = self.processing_dir / work_name

        try:
            scanner_user = get_active_scanner_user()

            if not scanner_user:
                raise RuntimeError(
                    "Aucun utilisateur connecté associé au scanner. "
                    "Connectez-vous dans Angular pour activer la session scanner."
                )

            shutil.move(str(path), str(work_path))

            log.info(
                "Scan détecté, extraction OCR interne",
                extra={
                    "file": str(work_path),
                    "user_email": scanner_user.get("user_email"),
                },
            )

            result = await self._run_internal_extraction(
                file_path=work_path,
                scanner_user=scanner_user,
            )

            result_path = self.output_dir / f"{work_path.stem}.json"
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            final_path = self.done_dir / work_path.name
            shutil.move(str(work_path), str(final_path))

            log.info(
                "Scan OCR terminé",
                extra={
                    "file": str(final_path),
                    "json": str(result_path),
                    "user_email": scanner_user.get("user_email"),
                },
            )

        except Exception as exc:
            log.exception(
                "Échec traitement scan",
                extra={"file": str(path), "error": str(exc)},
            )

            error_payload = {
                "file": str(path),
                "error": str(exc),
                "failed_at": datetime.now().isoformat(),
            }

            error_path = self.output_dir / f"error_{self._stamp()}_{path.stem}.json"
            error_path.write_text(
                json.dumps(error_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if work_path.exists():
                shutil.move(str(work_path), str(self.error_dir / work_path.name))
            elif path.exists():
                shutil.move(str(path), str(self.error_dir / path.name))

    async def _run_internal_extraction(
        self,
        *,
        file_path: Path,
        scanner_user: dict,
    ) -> dict:
        job_id = f"scanner_{uuid.uuid4()}"

        request = ExtractionRequest(
            template_id=self.template_id,
            document_type=self.document_type,
            engine=self.engine,
            processing_mode=self.processing_mode,
            cin_mode=self.cin_mode,
            language_hint=self.language_hint,
            include_diagnostics=self.include_diagnostics,
        )

        result = await asyncio.to_thread(
            get_orchestrator().process,
            file_path=str(file_path),
            request=request,
            job_id=job_id,
        )

        result_dict = self._as_result_dict(result)

        result_dict["_scanner"] = {
            "source": "scanner_watch_service",
            "job_id": job_id,
            "file_name": file_path.name,
            "processed_at": datetime.now().isoformat(),
            "user_id": scanner_user.get("user_id"),
            "user_email": scanner_user.get("user_email"),
            "user_role": scanner_user.get("user_role"),
        }

        history_id = await self._save_scanner_history(
            job_id=job_id,
            file_name=file_path.name,
            request=request,
            result_dict=result_dict,
            scanner_user=scanner_user,
        )

        result_dict["_scanner"]["history_id"] = history_id

        return result_dict

    def _as_result_dict(self, result) -> dict:
        if result is None:
            return {}

        if isinstance(result, dict):
            return result

        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")

        if hasattr(result, "dict"):
            return result.dict()

        return {"result": str(result)}

    def _extract_raw_text(self, result_dict: dict) -> str | None:
        direct_keys = [
            "raw_text",
            "text",
            "ocr_text",
            "full_text",
            "extracted_text",
        ]

        for key in direct_keys:
            value = result_dict.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        lines: list[str] = []

        fields = result_dict.get("fields")
        if isinstance(fields, list):
            for field in fields:
                if isinstance(field, dict):
                    name = field.get("name") or field.get("field_name") or field.get("key")
                    value = field.get("value")
                    if value not in (None, ""):
                        lines.append(f"{name}: {value}" if name else str(value))

        normalized = result_dict.get("normalized_data")
        if isinstance(normalized, dict):
            for key, value in normalized.items():
                if value not in (None, ""):
                    lines.append(f"{key}: {value}")

        for key in ["ocr_results", "pages", "lines"]:
            value = result_dict.get(key)

            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        lines.append(item.strip())
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("value")
                        if isinstance(text, str) and text.strip():
                            lines.append(text.strip())

        return "\n".join(lines) if lines else None

    async def _save_scanner_history(
        self,
        *,
        job_id: str,
        file_name: str,
        request: ExtractionRequest,
        result_dict: dict,
        scanner_user: dict,
    ) -> str | None:
        try:
            user_id = scanner_user.get("user_id")
            user_email = scanner_user.get("user_email")
            user_role = scanner_user.get("user_role")
            organization_id = scanner_user.get("organization_id")

            async with AsyncSessionLocal() as db:
                history = ExtractionHistory(
                    user_id=str(user_id) if user_id else None,
                    user_email=user_email,
                    user_role=user_role,
                    organization_id=organization_id,
                    job_id=job_id,
                    file_name=file_name,
                    document_type=result_dict.get("document_type") or request.document_type,
                    template_id=result_dict.get("template_id") or request.template_id,
                    engine_used=result_dict.get("engine_used") or request.engine,
                    status=result_dict.get("status"),
                    global_confidence=result_dict.get("global_confidence"),
                    processing_time_ms=result_dict.get("processing_time_ms"),
                    field_count=len(result_dict.get("fields") or []),
                    result_json=json.dumps(result_dict, ensure_ascii=False, default=str),
                )

                db.add(history)
                await db.commit()
                await db.refresh(history)

                detail = ExtractionResult(
                    history_id=str(history.id),
                    job_id=job_id,
                    raw_text=self._extract_raw_text(result_dict),
                    result_json=result_dict or None,
                    fields_json=result_dict.get("fields") or result_dict.get("normalized_data"),
                    diagnostics_json=(
                        result_dict.get("diagnostics")
                        or result_dict.get("warnings")
                        or result_dict.get("errors")
                    ),
                )

                db.add(detail)
                await db.commit()

                return str(history.id)

        except Exception as exc:
            log.warning(
                "Scanner history save failed",
                extra={
                    "job_id": job_id,
                    "file_name": file_name,
                    "error": str(exc),
                },
            )
            return None


_scanner_service: Optional[ScannerWatchService] = None


def get_scanner_watch_service() -> ScannerWatchService:
    global _scanner_service

    if _scanner_service is None:
        _scanner_service = ScannerWatchService()

    return _scanner_service