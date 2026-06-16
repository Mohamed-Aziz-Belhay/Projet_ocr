"""
app/extractors/cin_extractor.py
Specialized Tunisian CIN extractor.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional

from app.extractors.base import BaseExtractor, FieldOutput
from app.services.field_resolver import FieldCandidate
from app.utils.date_validation import parse_and_normalize
from app.utils.rtl_text import cleanup_arabic_text, normalize_arabic_digits, plausible_arabic_phrase

_8_DIGITS_RE = re.compile(r"\b([0-9]{8})\b")
_DATE_NUM_RE = re.compile(r"\b(\d{2}[./\-]\d{2}[./\-]\d{4})\b")
_TEXT_DATE_RE = re.compile(
    r"(\d{1,2})\s+ýÿýÿýÿýÿýÿ|Ø¬Ø§ÙÙÙýÿýÿýÿýÿýÿ|ÙØ¨Ø±Ø§ÙØ±ýÿýÿýÿýÿ³|Ø£ÙØ±ÙÙýÿýÿýÿýÿýÿ|ÙØ§Ùýÿýÿýÿýÿ|Ø¬ÙÙÙÙØ©ýÿýÿýÿýÿýÿýÿ|Ø£ÙØªýÿýÿýÿª|Ø³Ø¨ØªÙØ¨Ø±ýÿýÿýÿýÿýÿýÿ±|Ø§ÙØªÙØ¨Ø±ýÿýÿýÿýÿýÿýÿ±|Ø¯ÙØ³ÙØ¨Ø±)\s+(\d{4})",
    re.UNICODE,
)

_NAME_ANCHORS = {
    "last_name": ["Ø§ÙÙÙØ¨"],
    "first_name": ["Ø§ÙØ§Ø³Ù", "Ø§ÙØ§Ù", "Ø§ÙÙØ³Ù"],
}
_PLACE_ANCHORS = [ýÿýÿýÿýÿýÿýÿ§", ýÿýÿýÿýÿ", ýÿýÿýÿýÿýÿýÿýÿ©"]


class CINExtractor(BaseExtractor):
    doc_family = "id_document"
    variant_id = "cin_tn"

    def extract(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[FieldOutput]:
        metadata = metadata or {}
        field_candidates = self.build_field_candidates(
            full_text=text,
            zone_texts=metadata.get("zone_texts") or {},
            engine=metadata.get("engine") or "unknown",
            zone_scores=metadata.get("zone_scores") or {},
            full_text_score=metadata.get("full_text_score", 0.5),
        )
        outputs: List[FieldOutput] = []
        error_map = {
            "id_number": "CIN introuvable",
            "last_name": "Nom introuvable",
            "first_name": "PrÃ©nom introuvable",
            "birth_date": "Date introuvable",
            "birth_place": "Lieu introuvable",
        }
        for field_name, candidates in field_candidates.items():
            valid = [c for c in candidates if c.value]
            best = valid[0] if valid else None
            if best:
                outputs.append(FieldOutput(
                    name=field_name,
                    value=best.value,
                    confidence=round(best.ocr_confidence, 3),
                    validated=True,
                    raw_text=best.raw_text,
                    error=None,
                ))
            else:
                outputs.append(FieldOutput(
                    name=field_name,
                    value=None,
                    confidence=0.0,
                    validated=False,
                    raw_text=None,
                    error=error_map[field_name],
                ))
        return outputs

    def build_field_candidates(
        self,
        *,
        full_text: str,
        zone_texts: Dict[str, str],
        engine: str,
        zone_scores: Optional[Dict[str, float]] = None,
        full_text_score: float = 0.5,
    ) -> Dict[str, List[FieldCandidate]]:
        zone_scores = zone_scores or {}
        out = {"id_number": [], "last_name": [], "first_name": [], "birth_date": [], "birth_place": []}

        out["id_number"].extend(self.extract_id_candidates(full_text, engine, "full_text", full_text_score))
        out["last_name"].extend(self.extract_name_candidates("last_name", full_text, engine, "full_text", full_text_score))
        out["first_name"].extend(self.extract_name_candidates("first_name", full_text, engine, "full_text", full_text_score))
        out["birth_date"].extend(self.extract_date_candidates(full_text, engine, "full_text", full_text_score))
        out["birth_place"].extend(self.extract_place_candidates(full_text, engine, "full_text", full_text_score))

        for zone_name, zone_text in zone_texts.items():
            score = zone_scores.get(zone_name, 0.6)
            source = f"zone:{zone_name}" if zone_name != "right_text" else "zone:right_text"
            if zone_name == "id_number":
                out["id_number"].extend(self.extract_id_candidates(zone_text, engine, source, score))
            elif zone_name == "last_name":
                out["last_name"].extend(self.extract_name_candidates("last_name", zone_text, engine, source, score))
            elif zone_name == "first_name":
                out["first_name"].extend(self.extract_name_candidates("first_name", zone_text, engine, source, score))
            elif zone_name == "birth_date":
                out["birth_date"].extend(self.extract_date_candidates(zone_text, engine, source, score))
            elif zone_name == "birth_place":
                out["birth_place"].extend(self.extract_place_candidates(zone_text, engine, source, score))
            elif zone_name == "right_text":
                out["last_name"].extend(self.extract_name_candidates("last_name", zone_text, engine, source, score))
                out["first_name"].extend(self.extract_name_candidates("first_name", zone_text, engine, source, score))
                out["birth_place"].extend(self.extract_place_candidates(zone_text, engine, source, score))
        return out

    def extract_id_candidates(self, text: str, engine: str, source: str, score: float) -> List[FieldCandidate]:
        if not text:
            return []
        cleaned = normalize_arabic_digits(text)
        candidates: List[FieldCandidate] = []
        for value in _8_DIGITS_RE.findall(cleaned):
            candidates.append(FieldCandidate(
                field_name="id_number", engine=engine, source=source, value=value, raw_text=text, ocr_confidence=score,
                meta={"exact_digits": True},
            ))
        if candidates:
            return candidates
        digit_groups = re.findall(r"[0-9]{1,8}", cleaned)
        joined = "".join(digit_groups)
        if source == "zone:id_number" and 8 <= len(joined) <= 9:
            candidates.append(FieldCandidate(
                field_name="id_number", engine=engine, source=source, value=joined[:8], raw_text=text,
                ocr_confidence=max(0.52, score), reasons=["digit groups merged"], meta={"reconstructed": True},
            ))
        return candidates

    def extract_name_candidates(self, field_name: str, text: str, engine: str, source: str, score: float) -> List[FieldCandidate]:
        if not text:
            return []
        candidates: List[FieldCandidate] = []
        anchors = _NAME_ANCHORS[field_name]
        lines = [cleanup_arabic_text(ln) for ln in text.splitlines() if ln.strip()]

        for i, line in enumerate(lines):
            for anchor in anchors:
                if anchor not in line:
                    continue
                same_line = line.replace(anchor, " ").strip()
                phrase = plausible_arabic_phrase(same_line, max_tokens=2)
                if phrase:
                    candidates.append(FieldCandidate(
                        field_name=field_name,
                        engine=engine,
                        source=source,
                        value=phrase,
                        raw_text=line,
                        ocr_confidence=score,
                        meta={"anchor_hit": True, "same_line_anchor": True, "anchor": anchor},
                    ))
                if i + 1 < len(lines):
                    next_phrase = plausible_arabic_phrase(lines[i + 1], max_tokens=2)
                    if next_phrase:
                        candidates.append(FieldCandidate(
                            field_name=field_name,
                            engine=engine,
                            source=source,
                            value=next_phrase,
                            raw_text=lines[i + 1],
                            ocr_confidence=max(0.45, score - 0.10),
                            meta={"anchor_hit": True, "same_line_anchor": False, "anchor": anchor},
                        ))

        if not candidates and source in {"zone:last_name", "zone:first_name"}:
            phrase = plausible_arabic_phrase(cleanup_arabic_text(text), max_tokens=2)
            if phrase:
                candidates.append(FieldCandidate(
                    field_name=field_name,
                    engine=engine,
                    source=source,
                    value=phrase,
                    raw_text=text,
                    ocr_confidence=max(0.40, score - 0.12),
                    meta={"anchor_hit": False},
                ))
        return candidates

    def extract_date_candidates(self, text: str, engine: str, source: str, score: float) -> List[FieldCandidate]:
        if not text:
            return []
        normalized = normalize_arabic_digits(cleanup_arabic_text(text))
        out: List[FieldCandidate] = []
        for m in _DATE_NUM_RE.findall(normalized):
            out.append(FieldCandidate(
                field_name="birth_date", engine=engine, source=source, value=m, raw_text=text, ocr_confidence=score,
                meta={"pattern": "numeric"},
            ))
        for m in _TEXT_DATE_RE.findall(normalized):
            raw = f"{m[0]} {m[1]} {m[2]}"
            out.append(FieldCandidate(
                field_name="birth_date", engine=engine, source=source, value=parse_and_normalize(raw), raw_text=raw,
                ocr_confidence=score, meta={"pattern": "textual"},
            ))
        return out

    def extract_place_candidates(self, text: str, engine: str, source: str, score: float) -> List[FieldCandidate]:
        if not text:
            return []
        lines = [cleanup_arabic_text(ln) for ln in text.splitlines() if ln.strip()]
        out: List[FieldCandidate] = []
        for i, line in enumerate(lines):
            if any(anchor in line for anchor in _PLACE_ANCHORS):
                phrase = plausible_arabic_phrase(line, max_tokens=2)
                if phrase:
                    out.append(FieldCandidate(
                        field_name="birth_place", engine=engine, source=source, value=phrase, raw_text=line,
                        ocr_confidence=score, meta={"anchor_hit": True},
                    ))
                if i + 1 < len(lines):
                    phrase = plausible_arabic_phrase(lines[i + 1], max_tokens=2)
                    if phrase:
                        out.append(FieldCandidate(
                            field_name="birth_place", engine=engine, source=source, value=phrase, raw_text=lines[i + 1],
                            ocr_confidence=max(0.45, score - 0.10), meta={"anchor_hit": True},
                        ))
        if not out and source == "zone:birth_place":
            phrase = plausible_arabic_phrase(text, max_tokens=2)
            if phrase:
                out.append(FieldCandidate(
                    field_name="birth_place", engine=engine, source=source, value=phrase, raw_text=text,
                    ocr_confidence=max(0.40, score - 0.10), meta={"anchor_hit": False},
                ))
        return out
