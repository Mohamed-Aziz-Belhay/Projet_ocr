"""
app/engines/tesseract_engine.py
Tesseract 5 engine — multi-PSM strategy for card/document images.
For card images (CIN, etc.), runs multiple PSM modes and combines
the best results to maximise text extraction.
"""
from __future__ import annotations
import time
import re
from typing import Optional, List

import numpy as np

from app.engines.engine_factory import BaseOCREngine, OCRResult, OCRWord, register_engine
from app.core.logging import get_logger

log = get_logger(__name__)

_LANG_MAP = {
    "ar":    "ara",
    "fra":   "fra",
    "fr":    "fra",
    "en":    "eng",
    "ar+fr": "ara+fra",
    "ara+fra": "ara+fra",
    "ara":   "ara",
}

# PSM modes to try for card/identity document images
_CARD_PSM_MODES = [6, 11, 12, 4]
# PSM modes for standard documents
_DOC_PSM_MODES  = [6]


@register_engine
class TesseractEngine(BaseOCREngine):
    name = "tesseract"

    def is_available(self) -> bool:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def run(self, image: np.ndarray, language: Optional[str] = None) -> OCRResult:
        if not self.is_available():
            return OCRResult(full_text="", engine=self.name)
        try:
            import pytesseract
        except ImportError:
            return OCRResult(full_text="", engine=self.name)

        tess_lang = _LANG_MAP.get(language or "en", "eng")

        # Detect if this looks like a card image (small, square-ish)
        h, w = image.shape[:2]
        is_card = (w < 1500 and h < 1200 and 0.5 < w/h < 2.5)
        psm_modes = _CARD_PSM_MODES if is_card else _DOC_PSM_MODES

        t0 = time.time()
        all_texts: List[str] = []
        words: List[OCRWord] = []

        for psm in psm_modes:
            try:
                config = f"--oem 3 --psm {psm}"
                text = pytesseract.image_to_string(image, lang=tess_lang, config=config)
                if text.strip():
                    all_texts.append(text.strip())
            except Exception as exc:
                log.debug("Tesseract PSM failed", extra={"psm": psm, "error": str(exc)})

        # Try word-level data from best PSM (6)
        try:
            data = pytesseract.image_to_data(
                image, lang=tess_lang, config="--oem 3 --psm 6",
                output_type=pytesseract.Output.DICT
            )
            n = len(data["text"])
            for i in range(n):
                txt = str(data["text"][i]).strip()
                conf = int(data["conf"][i])
                if txt and conf > 0:
                    x = data["left"][i]
                    y = data["top"][i]
                    w_ = data["width"][i]
                    h_ = data["height"][i]
                    words.append(OCRWord(
                        text=txt,
                        confidence=conf / 100.0,
                        bbox=(x, y, w_, h_)
                    ))
        except Exception:
            pass

        elapsed_ms = int((time.time() - t0) * 1000)
        full_text = self._merge_ocr_results(all_texts)

        return OCRResult(
            full_text=full_text,
            words=words,
            language=language,
            engine=self.name,
            processing_time_ms=elapsed_ms,
        )

    @staticmethod
    def _merge_ocr_results(texts: List[str]) -> str:
        """Merge multiple OCR results removing duplicates."""
        if not texts:
            return ""
        if len(texts) == 1:
            return texts[0]
        seen_normalized: set = set()
        merged_lines: List[str] = []
        for text in texts:
            for line in text.splitlines():
                line_ = line.strip()
                if not line_:
                    continue
                norm = re.sub(r"[\W\u0600-\u06FF]", "", line_.lower())
                if len(norm) < 2:
                    continue
                already_seen = False
                for seen in seen_normalized:
                    if norm in seen or seen in norm:
                        if len(norm) > len(seen):
                            seen_normalized.discard(seen)
                            merged_lines = [l for l in merged_lines
                                            if re.sub(r"[\W\u0600-\u06FF]", "", l.lower()) != seen]
                        else:
                            already_seen = True
                        break
                if not already_seen:
                    seen_normalized.add(norm)
                    merged_lines.append(line_)
        return "\n".join(merged_lines)