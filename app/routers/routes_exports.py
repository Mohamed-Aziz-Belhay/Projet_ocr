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
import html
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.core.security import decode_access_token

_ARABIC_RE = re.compile(r"[؀-ۿ]")

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

    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    def dir_style(text: str) -> str:
        return "direction:rtl;text-align:right;" if _ARABIC_RE.search(text or "") else ""

    def title(text: str) -> str:
        return f'<p style="font-size:20px;font-weight:bold;color:#17385f;">{esc(text)}</p>'

    def section(text: str) -> str:
        return f'<p style="font-size:14px;font-weight:bold;color:#17385f;margin-top:10px;">{esc(text)}</p>'

    def line(text: str, *, size: int = 11, color: str = "#000000") -> str:
        return f'<p style="font-size:{size}px;color:{color};{dir_style(text)}">{esc(text)}</p>'

    def key_value(key: str, value: str, *, size: int = 10, suffix: str = "") -> str:
        # Keep the row itself left-aligned (labels are always Latin) and
        # only let the value flow RTL inline, instead of right-aligning
        # the whole paragraph — avoids each row jumping to a different
        # side of the page depending on whether its value is Arabic.
        value_style = "direction:rtl;unicode-bidi:embed;" if _ARABIC_RE.search(value or "") else ""
        return (
            f'<p style="font-size:{size}px;">'
            f'<b>{esc(key)}</b>: <span style="{value_style}">{esc(value)}</span>{esc(suffix)}</p>'
        )

    gray = "#59697f"
    parts: list[str] = []

    parts.append(title("Rapport d'extraction OCR"))
    parts.append(line(f"Document : {payload.file_name or 'document'}"))
    parts.append(line(f"Type : {payload.document_type or result.get('document_type') or 'unknown'}"))
    parts.append(line(f"Template : {payload.template_id or result.get('template_id') or '-'}"))
    parts.append(line(
        f"Date export : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        size=10, color=gray,
    ))

    parts.append(section("Synthèse"))
    parts.append(line(f"Statut : {result.get('status', '-')}"))
    parts.append(line(f"Confiance globale : {result.get('global_confidence', '-')}"))
    parts.append(line(f"Temps traitement ms : {result.get('processing_time_ms', '-')}"))

    if isinstance(normalized, dict) and normalized:
        parts.append(section("Données normalisées"))
        for key, value in list(normalized.items())[:40]:
            if isinstance(value, list):
                parts.append(key_value(key, "", suffix=f"{len(value)} ligne(s)"))
                for i, item in enumerate(value[:8]):
                    parts.append(key_value(f"  - {i + 1}", _stringify(item)[:95], size=9))
            else:
                parts.append(key_value(key, _stringify(value)[:95]))

    if fields:
        parts.append(section("Champs extraits"))
        for field in fields[:60]:
            if not isinstance(field, dict):
                continue
            name = field.get("name") or field.get("field_name") or field.get("key") or "-"
            val = _stringify(field.get("value"))[:80]
            conf = field.get("confidence", "-")
            parts.append(key_value(name, val, size=9, suffix=f"  | conf: {conf}"))

    raw = result.get("raw_text") or result.get("text")
    if raw:
        parts.append(section("Extrait du texte brut OCR"))
        for text_line in str(raw).splitlines()[:18]:
            parts.append(line(text_line[:110], size=8, color=gray))

    # A single Story/DocumentWriter pass over the WHOLE report: PyMuPDF
    # embeds each font used (Latin base font + the Arabic fallback font)
    # exactly once for the entire document. Doing this per-line instead
    # (calling Page.insert_htmlbox once per field) was tried first and
    # re-embeds a full copy of the Arabic font on every single call,
    # ballooning a ~15-line report from a few hundred KB to several MB.
    page_rect = fitz.paper_rect("a4")
    content_rect = page_rect + (48, 54, -48, -40)
    story = fitz.Story(html="".join(parts))
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(content_rect)
        story.draw(device)
        writer.end_page()
    writer.close()
    pdf_bytes = buf.getvalue()

    filename = _safe_filename(payload.file_name or "ocr_report", "pdf")
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
