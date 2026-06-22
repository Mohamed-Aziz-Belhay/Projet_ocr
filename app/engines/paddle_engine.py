"""
app/engines/paddle_engine.py
PaddleOCR engine — compatible PaddleOCR 2.x ET 3.x.

FIX: PaddleOCR 3.x a supprimé show_log, use_gpu, use_angle_cls.
     Détection de version + fallback pour assurer la compatibilité.

FIX (mémoire) : le cache du modèle PaddleOCR est maintenant un attribut DE
CLASSE (comme dans easyocr_engine.py), pas un attribut d'instance. Avant ce
correctif, si get_engine()/get_engine_instance() crée une nouvelle instance
de PaddleOCREngine à chaque requête HTTP, self._ocr repartait à None à
chaque fois et rechargeait un modèle PaddleOCR complet en mémoire sans
jamais libérer le précédent (PaddlePaddle gère sa mémoire côté C++, mal
libérée par le garbage collector Python) — fuite mémoire progressive
jusqu'au crash du process après suffisamment de requêtes.
"""
from __future__ import annotations
import time
from typing import Optional

import numpy as np

from app.engines.engine_factory import BaseOCREngine, OCRResult, OCRWord, register_engine
from app.core.logging import get_logger

log = get_logger(__name__)

_LANG_MAP_V2 = {"ar": "arabic", "fr": "french", "en": "en", "ch": "ch"}
_LANG_MAP_V3 = {"ar": "ar",     "fr": "fr",     "en": "en", "ch": "ch"}


def _get_paddle_major() -> int:
    try:
        import paddleocr as _poc
        return int(str(getattr(_poc, "__version__", "2.0.0")).split(".")[0])
    except Exception:
        return 2


@register_engine
class PaddleOCREngine(BaseOCREngine):
    name = "paddle"

    # Cache de classe : partagé par TOUTES les instances de PaddleOCREngine,
    # comme _readers dans EasyOCREngine. Clé = langue chargée.
    # C'est ce qui garantit qu'un modèle PaddleOCR n'est chargé qu'UNE FOIS
    # par langue pour toute la durée de vie du process, même si une nouvelle
    # instance Python de PaddleOCREngine est créée à chaque requête.
    _ocr_cache: dict[str, object] = {}

    def __init__(self):
        # Ne plus utiliser self._ocr comme cache - voir _ocr_cache (classe).
        self._loaded_lang: Optional[str] = None

    def _load(self, lang: str = "en") -> None:
        lang = (lang or "en").split("+")[0]

        if lang in self._ocr_cache:
            self._loaded_lang = lang
            return

        try:
            from paddleocr import PaddleOCR
            major = _get_paddle_major()
            if major >= 3:
                # PaddleOCR 3.x : show_log / use_gpu / use_angle_cls SUPPRIMES
                paddle_lang = _LANG_MAP_V3.get(lang, "en")
                try:
                    instance = PaddleOCR(lang=paddle_lang)
                except TypeError:
                    instance = PaddleOCR()
                log.info("PaddleOCR 3.x init (mise en cache classe)", extra={"lang": paddle_lang})
            else:
                # PaddleOCR 2.x
                paddle_lang = _LANG_MAP_V2.get(lang, "en")
                instance = PaddleOCR(
                    use_angle_cls=True, lang=paddle_lang,
                    show_log=False, use_gpu=False,
                )
                log.info("PaddleOCR 2.x init (mise en cache classe)", extra={"lang": paddle_lang})

            self._ocr_cache[lang] = instance
            self._loaded_lang = lang
        except ImportError:
            log.warning("paddleocr non installé")
        except Exception as exc:
            log.error("PaddleOCR init échoué", extra={"error": str(exc)})

    @property
    def _ocr(self):
        """Compat: retourne l'instance pour la langue actuellement chargée."""
        if self._loaded_lang is None:
            return None
        return self._ocr_cache.get(self._loaded_lang)

    def is_available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
            return True
        except ImportError:
            return False

    def run(self, image: np.ndarray, language: Optional[str] = None) -> OCRResult:
        lang = (language or "en").split("+")[0]
        self._load(lang)
        if self._ocr is None:
            return OCRResult(full_text="", engine=self.name)

        t0 = time.time()
        try:
            major = _get_paddle_major()
            raw = self._ocr.ocr(image) if major >= 3 else self._ocr.ocr(image, cls=True)
        except Exception as exc:
            log.error("PaddleOCR.ocr() échoué", extra={"error": str(exc)})
            return OCRResult(full_text="", engine=self.name)
        elapsed_ms = int((time.time() - t0) * 1000)
        words, lines = self._parse_result(raw)
        return OCRResult(
            full_text="\n".join(lines), words=words, language=language,
            engine=self.name, processing_time_ms=elapsed_ms,
        )

    def _parse_result(self, raw) -> tuple[list[OCRWord], list[str]]:
        words: list[OCRWord] = []
        lines: list[str] = []
        if not raw:
            return words, lines
        page = raw[0] if isinstance(raw, (list, tuple)) else raw
        if not page:
            return words, lines
        for item in page:
            if item is None:
                continue
            text, conf, box = None, 0.9, None
            try:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    box, text_info = item
                    if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                        text, conf = str(text_info[0]), float(text_info[1])
                    elif isinstance(text_info, str):
                        text, conf = text_info, 1.0
                    elif hasattr(item, "text") and hasattr(item, "score"):
                        text = str(item.text)
                        conf = float(item.score)
                    elif hasattr(item, "rec_text"):
                        text = str(item.rec_text)
                        conf = float(getattr(item, "rec_score", 0.9))
                        box = getattr(item, "dt_polys", None) or getattr(item, "dt_polys", None)
                if not text:
                    continue
                x, y, w, h = self._parse_box(box)
                words.append(OCRWord(text=text, confidence=conf, bbox=(x, y, w, h)))
                lines.append(text)
            except Exception as exc:
                log.debug("Item OCR ignoré", extra={"error": str(exc)})
                continue
        return words, lines

    @staticmethod
    def _parse_box(box) -> tuple[int, int, int, int]:
        try:
            if box is None:
                return 0, 0, 0, 0
            pts = list(box)
            if not pts:
                return 0, 0, 0, 0
            if isinstance(pts[0], (list, tuple, np.ndarray)):
                xs = [float(p[0]) for p in pts]
                ys = [float(p[1]) for p in pts]
            else:
                xs = [float(pts[0]), float(pts[2])]
                ys = [float(pts[1]), float(pts[3])]
            x, y = int(min(xs)), int(min(ys))
            return x, y, int(max(xs) - x), int(max(ys) - y)
        except Exception:
            return 0, 0, 0, 0