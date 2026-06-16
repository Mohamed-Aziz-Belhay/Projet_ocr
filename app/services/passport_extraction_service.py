from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.pipeline.common import call_recognize_document, get_engine_adapter, normalize_text
from app.services.mrz_parser import MRZParseResult, parse_td3_passport_mrz


_TD3_LEN = 44

# Positions numériques dans la ligne 2 TD3 :
# 0-8 document number, 9 check digit,
# 10-12 nationality,
# 13-18 birth date, 19 check digit,
# 20 sex,
# 21-26 expiry date, 27 check digit,
# 28-41 optional data,
# 42 optional check, 43 composite check.
_NUMERIC_POSITIONS_LINE2 = set(list(range(13, 20)) + list(range(21, 28)) + [9, 42, 43])

_NUMERIC_OCR_FIX = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "|": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
}


class PassportExtractionService:
    """
    Passport extraction service.

    Strategy:
    1. Extract MRZ from the lower part of the passport.
    2. Apply MRZ-specific preprocessing.
    3. Reconstruct two TD3 lines when OCR joins/splits the MRZ badly.
    4. Parse and validate TD3 checksums.
    5. Return structured fields only when MRZ validation is reliable.

    Important:
    - The caller still suppresses invalid MRZ values.
    - This service tries to improve OCR/reconstruction, not to bypass validation.
    """

    # ---------------------------------------------------------------------
    # Image preparation
    # ---------------------------------------------------------------------

    def _resize_for_mrz(self, crop: np.ndarray) -> np.ndarray:
        if crop is None or crop.size == 0:
            return crop

        h, w = crop.shape[:2]

        min_h = 180
        min_w = 1000

        scale_h = min_h / max(h, 1)
        scale_w = min_w / max(w, 1)
        scale = max(scale_h, scale_w, 1.0)

        if scale > 1.0:
            crop = cv2.resize(
                crop,
                (int(round(w * scale)), int(round(h * scale))),
                interpolation=cv2.INTER_CUBIC,
            )

        return crop

    def _prepare_mrz_variants(self, crop: np.ndarray) -> List[Tuple[str, np.ndarray]]:
        """
        Returns several OCR-ready versions of the same MRZ crop.

        This improves cases where:
        - MRZ is low contrast,
        - document is photographed from a screen,
        - MRZ is blurred,
        - EasyOCR confuses '<', digits and letters.
        """
        if crop is None or crop.size == 0:
            return []

        crop = self._resize_for_mrz(crop)

        if len(crop.shape) == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop

        variants: List[Tuple[str, np.ndarray]] = []

        # 1) CLAHE contrast enhancement.
        clahe = cv2.createCLAHE(clipLimit=2.8, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        variants.append(("clahe", cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)))

        # 2) Sharpened CLAHE.
        blurred = cv2.GaussianBlur(enhanced, (0, 0), 1.0)
        sharp = cv2.addWeighted(enhanced, 1.7, blurred, -0.7, 0)
        variants.append(("sharp", cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)))

        # 3) Binary threshold; useful for MRZ black characters.
        _, binary = cv2.threshold(
            enhanced,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )
        variants.append(("binary", cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)))

        return variants

    def _crop_norm(
        self,
        image: np.ndarray,
        bbox_norm: List[float],
    ) -> Optional[np.ndarray]:
        if image is None or image.size == 0:
            return None

        h, w = image.shape[:2]

        try:
            x, y, bw, bh = [float(v) for v in bbox_norm]
        except Exception:
            return None

        if bw <= 0 or bh <= 0:
            return None

        x1 = int(max(0, x * w))
        y1 = int(max(0, y * h))
        x2 = int(min(w, (x + bw) * w))
        y2 = int(min(h, (y + bh) * h))

        if x2 <= x1 or y2 <= y1:
            return None

        crop = image[y1:y2, x1:x2]

        if crop is None or crop.size == 0:
            return None

        return crop

    def _candidate_mrz_crops(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """
        Generic bottom MRZ candidates.

        TD3 passport MRZ is normally in the bottom part.
        We keep a few bands only to avoid too many OCR calls.
        """
        candidates = [
            ("bottom_38", [0.02, 0.58, 0.96, 0.40]),
            ("bottom_35", [0.03, 0.62, 0.94, 0.35]),
            ("bottom_30", [0.03, 0.67, 0.94, 0.30]),
            ("bottom_25", [0.03, 0.72, 0.94, 0.25]),
            ("bottom_22", [0.03, 0.76, 0.94, 0.22]),
            ("lower_half", [0.02, 0.50, 0.96, 0.48]),
        ]

        output: List[Dict[str, Any]] = []

        for name, bbox in candidates:
            raw_crop = self._crop_norm(image, bbox)

            if raw_crop is None or raw_crop.size == 0:
                continue

            for variant_name, prepared in self._prepare_mrz_variants(raw_crop):
                output.append(
                    {
                        "name": f"{name}_{variant_name}",
                        "base_name": name,
                        "variant": variant_name,
                        "bbox_norm": bbox,
                        "crop": prepared,
                    }
                )

        return output

    def _debug_save_crop(
        self,
        name: str,
        crop: Optional[np.ndarray],
    ) -> None:
        if crop is None or crop.size == 0:
            return

        try:
            from pathlib import Path

            debug_dir = Path("debug_passport")
            debug_dir.mkdir(exist_ok=True)
            cv2.imwrite(str(debug_dir / f"{name}.jpg"), crop)
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # MRZ text repair / reconstruction
    # ---------------------------------------------------------------------

    def _mrz_clean_token(self, text: str) -> str:
        """
        Keep only MRZ-compatible characters.
        """
        if not text:
            return ""

        text = str(text).upper()

        replacements = {
            "«": "<",
            "»": "<",
            "‹": "<",
            "›": "<",
            "≤": "<",
            "≥": "<",
            ">": "<",
            " ": "",
            "\t": "",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        return "".join(ch for ch in text if ch.isalnum() or ch == "<")

    def _pad_or_trim_td3(self, line: str) -> str:
        line = self._mrz_clean_token(line)

        if len(line) >= _TD3_LEN:
            return line[:_TD3_LEN]

        return line.ljust(_TD3_LEN, "<")

    def _normalize_line2_numeric_slots(self, line2: str) -> str:
        line2 = self._pad_or_trim_td3(line2)
        chars = list(line2)

        for idx in _NUMERIC_POSITIONS_LINE2:
            if idx >= len(chars):
                continue

            chars[idx] = _NUMERIC_OCR_FIX.get(chars[idx], chars[idx])

        return "".join(chars)

    def _line2_variants(self, line2_seed: str, issuing_country: str) -> List[str]:
        """
        Generate conservative TD3 line2 variants.

        This does not validate by itself. It only proposes OCR corrections.
        parse_td3_passport_mrz remains the authority because it checks checksums.
        """
        base = self._pad_or_trim_td3(line2_seed)
        normalized = self._normalize_line2_numeric_slots(base)

        variants: List[str] = []
        seen = set()

        def add(value: str) -> None:
            value = self._pad_or_trim_td3(value)
            if value in seen:
                return
            seen.add(value)
            variants.append(value)

        add(base)
        add(normalized)

        # Tunisian passports often start with W / R / Y.
        # OCR may read W as 4, or miss the first letter.
        country = (issuing_country or "").upper()

        if country == "TUN":
            for candidate in list(variants):
                if not candidate:
                    continue

                if candidate[0] in {"4", "0", "8", "W", "Y", "R"}:
                    for prefix in ("W", "R", "Y"):
                        add(prefix + candidate[1:])

        # Position 20 is sex. OCR sometimes reads it as 0 / O / I / 1.
        for candidate in list(variants):
            if len(candidate) <= 20:
                continue

            if candidate[20] not in {"M", "F", "<"}:
                for sex in ("M", "F", "<"):
                    chars = list(candidate)
                    chars[20] = sex
                    add("".join(chars))

        return variants
    

    def _looks_like_mrz_candidate(self, text: str) -> bool:
        text = (text or "").upper()

        # Une MRZ passeport TD3 contient normalement P< sur la première ligne
        # et beaucoup de caractères '<'.
        if "P<" in text:
            return True

        if text.count("<") >= 8:
            return True

        # Fallback faible : présence d'un code pays + longue séquence alphanumérique.
        cleaned = "".join(ch for ch in text if ch.isalnum() or ch == "<")
        if len(cleaned) >= 35 and any(code in cleaned for code in ("TUN", "FRA", "LBY", "DZA", "MAR")):
            return True

        return False

    def _candidate_mrz_texts(self, raw_text: str) -> List[Dict[str, str]]:
        """
        Build possible two-line TD3 MRZ texts from noisy OCR.

        Handles cases like:
        - both lines returned as one line,
        - first line too short because '<' fillers are missed,
        - second line glued after the first line,
        - garbage before 'P<'.
        """
        raw_text = raw_text or ""

        candidates: List[Dict[str, str]] = []
        seen = set()

        def add(text: str, method: str) -> None:
            text = text or ""
            if not text.strip():
                return

            key = text.strip()
            if key in seen:
                return

            seen.add(key)
            candidates.append({"text": key, "method": method})

        # Always try original OCR first.
        add(raw_text, "raw_ocr_text")

        # Split by visible lines.
        raw_lines = [
            self._mrz_clean_token(line)
            for line in str(raw_text).upper().splitlines()
        ]
        raw_lines = [line for line in raw_lines if len(line) >= 8]

        for i in range(len(raw_lines) - 1):
            l1 = raw_lines[i]
            l2 = raw_lines[i + 1]

            if "P<" in l1:
                l1 = l1[l1.find("P<") :]
                issuing_country = l1[2:5] if len(l1) >= 5 else ""

                for line2 in self._line2_variants(l2, issuing_country):
                    add(
                        f"{self._pad_or_trim_td3(l1)}\n{line2}",
                        "two_visible_lines",
                    )

        # Token-based reconstruction.
        text_up = str(raw_text).upper()

        for old, new in {
            "«": "<",
            "»": "<",
            "‹": "<",
            "›": "<",
            "≤": "<",
            "≥": "<",
            ">": "<",
        }.items():
            text_up = text_up.replace(old, new)

        tokens = []
        current = []

        for ch in text_up:
            if ch.isalnum() or ch == "<":
                current.append(ch)
            else:
                token = "".join(current)
                if len(token) >= 5:
                    tokens.append(token)
                current = []

        token = "".join(current)
        if len(token) >= 5:
            tokens.append(token)

        tokens = [self._mrz_clean_token(t) for t in tokens if self._mrz_clean_token(t)]

        for i, token in enumerate(tokens):
            p_idx = token.find("P<")

            if p_idx < 0:
                continue

            after_p = token[p_idx:]

            # Case 1: line1 and line2 are glued inside the same token.
            if len(after_p) > _TD3_LEN:
                l1_seed = after_p[:_TD3_LEN]
                l2_seed = after_p[_TD3_LEN:] + "".join(tokens[i + 1 :])
            else:
                # Case 2: OCR split line1 and line2 into separate tokens.
                l1_seed = after_p
                l2_seed = "".join(tokens[i + 1 :])

            if len(l1_seed) < 12 or len(l2_seed) < 12:
                continue

            issuing_country = l1_seed[2:5] if len(l1_seed) >= 5 else ""

            # If line1 is short, pad it with '<' instead of stealing chars from line2.
            l1 = self._pad_or_trim_td3(l1_seed)

            for line2 in self._line2_variants(l2_seed, issuing_country):
                add(
                    f"{l1}\n{line2}",
                    "token_reconstruction",
                )

        return candidates

    def _parse_best_mrz_text(
        self,
        raw_text: str,
    ) -> Tuple[MRZParseResult, str, List[Dict[str, Any]]]:
        """
        Try raw OCR + repaired candidates and keep the best parse.
        """
        candidates = self._candidate_mrz_texts(raw_text)

        best_parsed: Optional[MRZParseResult] = None
        best_text = raw_text or ""
        debug: List[Dict[str, Any]] = []

        for candidate in candidates:
            text = candidate["text"]
            method = candidate["method"]
            parsed = parse_td3_passport_mrz(text)
            score = self._score_result(parsed)

            debug.append(
                {
                    "method": method,
                    "text": text,
                    "score": score,
                    "valid": parsed.valid,
                    "parsed": parsed.to_dict(),
                }
            )

            if best_parsed is None or score > self._score_result(best_parsed):
                best_parsed = parsed
                best_text = text

            if parsed.valid:
                return parsed, text, debug

        if best_parsed is None:
            best_parsed = parse_td3_passport_mrz(raw_text or "")

        return best_parsed, best_text, debug

    # ---------------------------------------------------------------------
    # Scoring / fields
    # ---------------------------------------------------------------------

    def _score_result(self, parsed: MRZParseResult) -> float:
        if parsed is None:
            return -100.0

        score = 0.0

        if parsed.document_number:
            score += 6

        if parsed.surname:
            score += 6

        if parsed.given_names:
            score += 4

        if parsed.issuing_country:
            score += 3

        if parsed.nationality:
            score += 3

        if parsed.birth_date:
            score += 5

        if parsed.expiry_date:
            score += 5

        if parsed.gender in {"M", "F"}:
            score += 2

        if parsed.mrz_lines:
            score += min(len(parsed.mrz_lines), 2) * 2

        checks = parsed.checks or {}

        for ok in checks.values():
            if ok:
                score += 2.0
            else:
                score -= 1.0

        if parsed.valid:
            score += 40.0
        else:
            score -= 10.0

        score -= len(parsed.errors or []) * 1.2

        return round(score, 4)

    def _field(
        self,
        name: str,
        value: Optional[str],
        valid: bool,
        raw_text: Optional[str],
        error: Optional[str],
        confidence: float,
    ) -> Dict[str, Any]:
        return {
            "name": name,
            "value": value,
            "confidence": round(confidence, 3),
            "validated": bool(valid),
            "raw_text": raw_text,
            "raw_template_field": name,
            "error": error,
            "selected_engine": "passport_mrz",
            "selected_source": "mrz",
            "review_required": bool(
                not valid
                and name
                in {
                    "document_number",
                    "surname",
                    "birth_date",
                    "expiry_date",
                    "nationality",
                }
            ),
            "reasons": ["selected_from:mrz"] if value else ["field unresolved"],
        }

    def _fields_from_mrz(
        self,
        parsed: MRZParseResult,
        raw_text: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
        fields: List[Dict[str, Any]] = []
        normalized: Dict[str, Any] = {}
        warnings: List[str] = []

        mrz_valid = parsed.valid
        base_conf = 0.94 if mrz_valid else 0.0

        mapping = [
            ("document_number", parsed.document_number, bool(parsed.document_number) and mrz_valid),
            ("surname", parsed.surname, bool(parsed.surname) and mrz_valid),
            ("given_names", parsed.given_names, bool(parsed.given_names) and mrz_valid),
            ("nationality", parsed.nationality, bool(parsed.nationality) and mrz_valid),
            ("birth_date", parsed.birth_date, bool(parsed.birth_date) and mrz_valid),
            ("gender", parsed.gender, parsed.gender in {"M", "F"} and mrz_valid),
            ("expiry_date", parsed.expiry_date, bool(parsed.expiry_date) and mrz_valid),
            ("issuing_country", parsed.issuing_country, bool(parsed.issuing_country) and mrz_valid),
            ("personal_number", parsed.personal_number, bool(parsed.personal_number) and mrz_valid),
        ]

        for name, value, valid in mapping:
            err = None if valid else f"invalid_{name}"

            fields.append(
                self._field(
                    name=name,
                    value=value if valid else None,
                    valid=valid,
                    raw_text=raw_text,
                    error=err,
                    confidence=base_conf if valid else 0.0,
                )
            )

            if valid and value is not None:
                normalized[name] = value

            if not valid and name in {
                "document_number",
                "surname",
                "birth_date",
                "expiry_date",
                "nationality",
            }:
                warnings.append(f"Required passport MRZ field '{name}' missing or invalid")

        mrz_text = "\n".join(parsed.mrz_lines)

        fields.append(
            self._field(
                name="mrz",
                value=mrz_text,
                valid=parsed.valid,
                raw_text=raw_text,
                error=None if parsed.valid else "invalid_mrz",
                confidence=0.97 if parsed.valid else 0.0,
            )
        )

        if parsed.valid:
            normalized["mrz"] = mrz_text

        return fields, normalized, warnings

    # ---------------------------------------------------------------------
    # Public extraction
    # ---------------------------------------------------------------------

    def extract(
        self,
        *,
        image: np.ndarray,
        engine_name: str = "easyocr",
        language_hints: Optional[List[str]] = None,
        debug_prefix: str = "passport",
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
        if image is None or image.size == 0:
            return [], {}, {"passport_extraction": "empty_image"}, ["Passport image is empty"]

        engine = get_engine_adapter(engine_name)
        language_hints = language_hints or ["en"]

        candidates = self._candidate_mrz_crops(image)

        best_item: Optional[Dict[str, Any]] = None
        debug_candidates: List[Dict[str, Any]] = []

        for idx, item in enumerate(candidates):
            crop = item["crop"]
            name = item["name"]

            self._debug_save_crop(f"{debug_prefix}_{idx}_{name}", crop)

            raw_text, raw_score = call_recognize_document(engine, crop, language_hints)
            raw_text = normalize_text(raw_text or "")
            
            if not self._looks_like_mrz_candidate(raw_text):
                debug_candidates.append(
                    {
                        "candidate": name,
                        "base_candidate": item.get("base_name"),
                        "variant": item.get("variant"),
                        "bbox_norm": item["bbox_norm"],
                        "raw_text": raw_text,
                        "ocr_score": raw_score,
                        "parsed": parse_td3_passport_mrz("").to_dict(),
                        "repair_candidates": [],
                        "score": -25.0,
                        "skipped_reason": "no_mrz_anchor_detected",
                    }
                )

                # En mode normal, on continue un peu.
                # Mais si plusieurs premiers candidats ne contiennent aucune MRZ,
                # on évite de perdre trop de temps.
                if idx >= 5:
                    break

                continue

            parsed, selected_text, repair_debug = self._parse_best_mrz_text(raw_text)
            parsed_score = self._score_result(parsed)

            debug_item = {
                "candidate": name,
                "base_candidate": item.get("base_name"),
                "variant": item.get("variant"),
                "bbox_norm": item["bbox_norm"],
                "raw_text": raw_text,
                "selected_text": selected_text,
                "ocr_score": raw_score,
                "parsed": parsed.to_dict(),
                "repair_candidates": repair_debug,
                "score": parsed_score,
            }

            debug_candidates.append(debug_item)

            current_item = {
                "candidate": name,
                "base_candidate": item.get("base_name"),
                "variant": item.get("variant"),
                "bbox_norm": item["bbox_norm"],
                "raw_text": raw_text,
                "selected_text": selected_text,
                "ocr_score": raw_score,
                "parsed": parsed,
                "score": parsed_score,
                "repair_candidates": repair_debug,
            }

            if best_item is None or parsed_score > best_item["score"]:
                best_item = current_item

            # Early stop inside this service.
            # generic_runner also has an early stop across candidates.
            if parsed.valid:
                best_item = current_item
                break

        if best_item is None:
            return [], {}, {
                "passport_extraction": "no_mrz_candidate",
                "candidates": debug_candidates,
            }, ["No MRZ candidate crop found"]

        parsed = best_item["parsed"]
        raw_text = best_item["selected_text"] or best_item["raw_text"]

        fields, normalized, warnings = self._fields_from_mrz(parsed, raw_text)

        debug = {
            "passport_extraction": "passport_mrz_first_v2_mrz_preprocess_repair",
            "engine": engine_name,
            "language_hints": language_hints,
            "selected_candidate": best_item["candidate"],
            "selected_base_candidate": best_item.get("base_candidate"),
            "selected_variant": best_item.get("variant"),
            "selected_bbox_norm": best_item["bbox_norm"],
            "selected_score": best_item["score"],
            "selected_raw_text": best_item["raw_text"],
            "selected_repaired_text": best_item["selected_text"],
            "selected_parsed": parsed.to_dict(),
            "selected_repair_candidates": best_item.get("repair_candidates", []),
            "candidates": debug_candidates,
        }

        if not parsed.valid:
            warnings.append("Passport MRZ parsed but validation is incomplete or failed")

        return fields, normalized, debug, warnings


@lru_cache(maxsize=1)
def get_passport_extraction_service() -> PassportExtractionService:
    return PassportExtractionService()