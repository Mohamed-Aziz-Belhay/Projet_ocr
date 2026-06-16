"""
app/engines/easyocr_engine.py
EasyOCR adapter with normalized output and optional GPU acceleration.

Important:
- Reader instances are cached per language set + gpu flag.
- GPU is enabled only when settings.EASYOCR_GPU is true and torch.cuda.is_available().
- If CUDA/PyTorch is not configured, the engine falls back to CPU instead of crashing.
"""
from __future__ import annotations

import time
from typing import List, Optional

import numpy as np

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.engines.engine_factory import BaseOCREngine, OCRResult, OCRWord, register_engine

log = get_logger(__name__)
settings = get_settings()


def _torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


@register_engine
class EasyOCREngine(BaseOCREngine):
    name = "easyocr"
    _readers: dict[str, object] = {}

    def is_available(self) -> bool:
        try:
            import easyocr  # noqa: F401

            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_langs(lang: Optional[str]) -> List[str]:
        if not lang:
            return list(getattr(settings, "EASYOCR_LANGS", ["ar", "en"]))

        alias = {
            "ara": "ar",
            "arabic": "ar",
            "fra": "fr",
            "fre": "fr",
            "french": "fr",
            "eng": "en",
            "english": "en",
        }
        raw = [x.strip().lower() for x in str(lang).replace(",", "+").split("+") if x.strip()]
        langs: List[str] = []
        for item in raw:
            item = alias.get(item, item)
            if item not in langs:
                langs.append(item)

        if "ar" in langs:
            return ["ar", "en"]
        if "fr" in langs:
            return ["fr", "en"]
        return langs or ["en"]

    def _use_gpu(self) -> bool:
        return bool(getattr(settings, "EASYOCR_GPU", True) and _torch_cuda_available())

    def _get_reader(self, lang: Optional[str]):
        langs = self._normalize_langs(lang)
        use_gpu = self._use_gpu()
        cache_key = f"{'+'.join(langs)}|gpu={int(use_gpu)}"
        if cache_key in self._readers:
            return self._readers[cache_key]

        import easyocr

        reader = easyocr.Reader(langs, gpu=use_gpu, verbose=False)
        self._readers[cache_key] = reader
        log.info("EasyOCR reader initialized", extra={"langs": langs, "gpu": use_gpu})
        return reader

    @staticmethod
    def _to_words(results) -> tuple[list[OCRWord], list[str]]:
        words: list[OCRWord] = []
        lines: list[str] = []
        for item in results or []:
            try:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue
                bbox, text, conf = item[0], str(item[1] or "").strip(), float(item[2] or 0.0)
                if not text:
                    continue
                xs = [int(p[0]) for p in bbox]
                ys = [int(p[1]) for p in bbox]
                x1, y1 = min(xs), min(ys)
                x2, y2 = max(xs), max(ys)
                words.append(
                    OCRWord(
                        text=text,
                        confidence=conf,
                        bbox=(x1, y1, max(0, x2 - x1), max(0, y2 - y1)),
                    )
                )
                lines.append(text)
            except Exception:
                continue
        return words, lines

    def run(self, image: np.ndarray, language: Optional[str] = None) -> OCRResult:
        if not self.is_available():
            return OCRResult(full_text="", engine=self.name)
        try:
            reader = self._get_reader(language)
        except Exception as exc:
            log.error("EasyOCR init failed", extra={"error": str(exc), "lang": language})
            return OCRResult(full_text="", engine=self.name)

        t0 = time.time()
        try:
            results = reader.readtext(
                image,
                detail=1,
                paragraph=False,
                canvas_size=int(getattr(settings, "EASYOCR_CANVAS_SIZE", 1280)),
                mag_ratio=float(getattr(settings, "EASYOCR_MAG_RATIO", 1.0)),
            )
        except Exception as exc:
            log.error("EasyOCR failed", extra={"error": str(exc)})
            return OCRResult(full_text="", engine=self.name)

        elapsed_ms = int((time.time() - t0) * 1000)
        words, lines = self._to_words(results)
        return OCRResult(
            full_text="\n".join(lines),
            words=words,
            language=language,
            engine=self.name,
            processing_time_ms=elapsed_ms,
            meta={
                "reader_langs": self._normalize_langs(language),
                "gpu": self._use_gpu(),
                "canvas_size": int(getattr(settings, "EASYOCR_CANVAS_SIZE", 1280)),
                "mag_ratio": float(getattr(settings, "EASYOCR_MAG_RATIO", 1.0)),
            },
        )
