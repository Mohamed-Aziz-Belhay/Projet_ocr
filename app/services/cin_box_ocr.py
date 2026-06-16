from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List

import cv2
import numpy as np

from app.core.settings import get_settings

settings = get_settings()


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return default if x is None else float(x)
    except Exception:
        return default


def _torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


from functools import lru_cache


@lru_cache(maxsize=1)
def _get_paddle_reader():
    from paddleocr import PaddleOCR

    # PaddleOCR 2.x : accepte show_log/use_angle_cls/use_gpu.
    try:
        return PaddleOCR(
            lang="ar",
            use_angle_cls=False,
            use_gpu=False,
            show_log=False,
        )
    except TypeError:
        pass

    # PaddleOCR 3.x : certains paramètres sont supprimés.
    # Mais cette version ne doit normalement pas afficher le gros Namespace DEBUG.
    try:
        return PaddleOCR(
            lang="ar",
        )
    except TypeError:
        return PaddleOCR()


@lru_cache(maxsize=1)
def _get_easyocr_reader():
    import easyocr

    langs = list(getattr(settings, "EASYOCR_LANGS", ["ar", "en"]))
    use_gpu = bool(getattr(settings, "EASYOCR_GPU", True) and _torch_cuda_available())
    return easyocr.Reader(langs, gpu=use_gpu, verbose=False)


def paddle_boxes(image_bgr: np.ndarray) -> List[Dict[str, Any]]:
    reader = _get_paddle_reader()
    result = reader.ocr(image_bgr, cls=False)
    rows: List[Dict[str, Any]] = []
    if not result:
        return rows

    lines = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else result
    for item in lines or []:
        try:
            bbox = item[0]
            text = str(item[1][0]).strip()
            conf = _safe_float(item[1][1], 0.0)
            if text:
                rows.append({"text": text, "confidence": conf, "bbox": bbox, "engine": "paddle"})
        except Exception:
            continue
    return rows


def easyocr_boxes(image_bgr: np.ndarray) -> List[Dict[str, Any]]:
    reader = _get_easyocr_reader()
    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    result = reader.readtext(
        image_rgb,
        detail=1,
        paragraph=False,
        canvas_size=int(getattr(settings, "EASYOCR_CANVAS_SIZE", 1280)),
        mag_ratio=float(getattr(settings, "EASYOCR_MAG_RATIO", 1.0)),
    )
    rows: List[Dict[str, Any]] = []
    for item in result or []:
        try:
            bbox, text, conf = item
            text = str(text).strip()
            conf = _safe_float(conf, 0.0)
            if text:
                rows.append({"text": text, "confidence": conf, "bbox": bbox, "engine": "easyocr"})
        except Exception:
            continue
    return rows
