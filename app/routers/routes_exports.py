"""
app/routers/routes_exports.py

Export OCR results as JSON, CSV or PDF.

Add in app/main.py:
    "app.routers.routes_exports",

Routes:
- POST /exports/json
- POST /exports/csv
- POST /exports/pdf

The frontend mostly exports JSON/CSV locally, but PDF is generated here
to make the platform look more enterprise and reliable.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.core.security import decode_access_token

router = APIRouter(prefix="/exports", tags=["Exports"])


class ExportPayload(BaseModel):
    file_name: Optional[str] = None
    document_type: Optional[str] = None
    template_id: Optional[str] = None
    result: dict[str, Any]
    metadata: dict[str, Any] = {}


def _require_bearer(authorization: Optional[str]) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    return decode_access_token(token)


def _safe_filename(name: str, ext: str) -> str:
    base = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (name or "ocr_result"))
    base = base.rsplit(".", 1)[0] if "." in base else base
    return f"{base or 'ocr_result'}.{ext}"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _rows_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    fields = result.get("fields")
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            rows.append(
                {
                    "section": "fields",
                    "key": field.get("name") or field.get("field_name") or field.get("key") or "",
                    "value": _stringify(field.get("value")),
                    "confidence": field.get("confidence", ""),
                    "source": field.get("selected_source") or field.get("selected_engine") or "",
                    "validated": field.get("validated", ""),
                }
            )

    normalized = result.get("normalized_data") or result.get("normalizedData") or {}
    if isinstance(normalized, dict):
        for key, value in normalized.items():
            if isinstance(value, list):
                for i, item in enumerate(value):
                    rows.append(
                        {
                            "section": key,
                            "key": f"{key}[{i}]",
                            "value": _stringify(item),
                            "confidence": "",
                            "source": "normalized_data",
                            "validated": "",
                        }
                    )
            else:
                rows.append(
                    {
                        "section": "normalized_data",
                        "key": key,
                        "value": _stringify(value),
                        "confidence": "",
                        "source": "normalized_data",
                        "validated": "",
                    }
                )

    if not rows:
        for key, value in result.items():
            if not isinstance(value, (dict, list)):
                rows.append(
                    {
                        "section": "result",
                        "key": key,
                        "value": _stringify(value),
                        "confidence": "",
                        "source": "result",
                        "validated": "",
                    }
                )

    return rows


@router.post("/json")
async def export_json(
    payload: ExportPayload,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    _require_bearer(authorization)

    content = json.dumps(payload.result, ensure_ascii=False, indent=2)
    filename = _safe_filename(payload.file_name or "ocr_result", "json")

    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/csv")
async def export_csv(
    payload: ExportPayload,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    _require_bearer(authorization)

    output = io.StringIO()
    fieldnames = ["section", "key", "value", "confidence", "source", "validated"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for row in _rows_from_result(payload.result):
        writer.writerow(row)

    filename = _safe_filename(payload.file_name or "ocr_result", "csv")

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/pdf")
async def export_pdf(
    payload: ExportPayload,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    _require_bearer(authorization)

    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="PyMuPDF is required for PDF export. Install: pip install pymupdf",
        ) from exc

    result = payload.result or {}
    normalized = result.get("normalized_data") or result.get("normalizedData") or {}
    fields = result.get("fields") if isinstance(result.get("fields"), list) else []

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 points

    y = 54
    margin = 48
    line_h = 17

    def add_text(text: str, size: int = 10, bold: bool = False, color=(0, 0, 0)) -> None:
        nonlocal y, page
        if y > 790:
            page = doc.new_page(width=595, height=842)
            y = 54
        font = "helv" if not bold else "helv"
        page.insert_text((margin, y), text[:120], fontsize=size, fontname=font, color=color)
        y += line_h if size <= 11 else line_h + 4

    blue = (0.09, 0.36, 0.65)
    gray = (0.35, 0.42, 0.50)

    add_text("Rapport d'extraction OCR", size=20, bold=True, color=blue)
    add_text(f"Document : {payload.file_name or 'document'}", size=11)
    add_text(f"Type : {payload.document_type or result.get('document_type') or 'unknown'}", size=11)
    add_text(f"Template : {payload.template_id or result.get('template_id') or '-'}", size=11)
    add_text(f"Date export : {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}", size=10, color=gray)
    add_text("")

    add_text("Synthèse", size=14, bold=True, color=blue)
    add_text(f"Statut : {result.get('status', '-')}")
    add_text(f"Confiance globale : {result.get('global_confidence', '-')}")
    add_text(f"Temps traitement ms : {result.get('processing_time_ms', '-')}")
    add_text("")

    if isinstance(normalized, dict) and normalized:
        add_text("Données normalisées", size=14, bold=True, color=blue)
        for key, value in list(normalized.items())[:40]:
            if isinstance(value, list):
                add_text(f"{key}: {len(value)} ligne(s)")
                for i, item in enumerate(value[:8]):
                    add_text(f"  - {i + 1}: {_stringify(item)[:95]}", size=9)
            else:
                add_text(f"{key}: {_stringify(value)[:95]}", size=10)
        add_text("")

    if fields:
        add_text("Champs extraits", size=14, bold=True, color=blue)
        for field in fields[:60]:
            if not isinstance(field, dict):
                continue
            name = field.get("name") or field.get("field_name") or field.get("key") or "-"
            val = _stringify(field.get("value"))[:80]
            conf = field.get("confidence", "-")
            add_text(f"{name}: {val}  | conf: {conf}", size=9)

    raw = result.get("raw_text") or result.get("text")
    if raw:
        add_text("")
        add_text("Extrait du texte brut OCR", size=14, bold=True, color=blue)
        for line in str(raw).splitlines()[:18]:
            add_text(line[:110], size=8, color=gray)

    pdf_bytes = doc.tobytes()
    doc.close()

    filename = _safe_filename(payload.file_name or "ocr_report", "pdf")
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
