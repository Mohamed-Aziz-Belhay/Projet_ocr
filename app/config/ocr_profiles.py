#config/ocr_profiles.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

import numpy as np

from app.core.settings import get_settings
from app.pipeline.preprocess import (
    adaptive_bin,
    clahe,
    denoise_bilateral,
    denoise_nlm,
    dilate,
    erode,
    gray_to_bgr,
    morph_close,
    otsu_bin,
    to_gray,
    upscale,
)

settings = get_settings()


@dataclass(frozen=True)
class PreprocessProfile:
    name: str
    steps: List[Callable[[np.ndarray], np.ndarray]]


def apply_profile(image_bgr: np.ndarray, profile: PreprocessProfile) -> np.ndarray:
    gray = to_gray(image_bgr)
    x = gray
    for step in profile.steps:
        x = step(x)
    return gray_to_bgr(x) if len(x.shape) == 2 else x


PROFILES: Dict[str, PreprocessProfile] = {
    "scan_dense": PreprocessProfile("scan_dense", [denoise_bilateral, clahe, otsu_bin]),
    "photo_id": PreprocessProfile("photo_id", [lambda g: upscale(g, 1.5), clahe, denoise_bilateral, otsu_bin]),
    "receipt": PreprocessProfile("receipt", [lambda g: upscale(g, 2), denoise_bilateral, adaptive_bin]),
    "id_card": PreprocessProfile("id_card", [lambda g: upscale(g, 1.5), clahe, denoise_bilateral, otsu_bin]),
    "arabic_text": PreprocessProfile("arabic_text", [lambda g: upscale(g, 2), clahe, adaptive_bin]),
    "scan_strong": PreprocessProfile("scan_strong", [lambda g: upscale(g, 2), denoise_nlm, clahe, otsu_bin, morph_close]),
    "text_thick": PreprocessProfile("text_thick", [clahe, adaptive_bin, dilate]),
    "text_thin": PreprocessProfile("text_thin", [clahe, adaptive_bin, erode]),
}


def default_engine_names() -> List[str]:
    return [e.strip() for e in (settings.ocr_engines_default or "").split(",") if e.strip()]


def get_adaptive_config(image: np.ndarray, doc_type: str, fast_mode: bool = True) -> dict:
    h, w = image.shape[:2]
    is_small = min(h, w) < 600

    cfg = {
        "lang": "fra+ara",
        "engines": default_engine_names(),
        "profiles": [],
        "psm_list": [],
        "try_rotate": True,
    }

    if doc_type == "cin":
        cfg["lang"] = "ara"
        cfg["try_rotate"] = False

        if fast_mode:
            # vrai fast mode : seulement les profils/moteurs utiles
            cfg["engines"] = ["paddle", "easyocr"]
            cfg["profiles"] = ["scan_dense", "receipt"]
            cfg["psm_list"] = [6]
        else:
            cfg["engines"] = ["paddle", "easyocr", "tesseract"]
            cfg["profiles"] = ["cin_roi", "scan_dense", "receipt", "id_card"]
            cfg["psm_list"] = [6, 3]

    elif doc_type == "registre_commerce":
        cfg["lang"] = "fra+ara"
        cfg["engines"] = ["paddle", "easyocr"] if fast_mode else ["paddle", "easyocr", "tesseract"]
        cfg["profiles"] = ["scan_strong", "receipt"]
        cfg["psm_list"] = [6]
        cfg["try_rotate"] = False

    elif doc_type == "invoice":
        cfg["lang"] = "fra+ara"
        cfg["engines"] = ["paddle", "easyocr"] if fast_mode else ["paddle", "easyocr", "tesseract"]
        cfg["profiles"] = ["receipt", "scan_dense"]
        cfg["psm_list"] = [6]
        cfg["try_rotate"] = False

    else:
        cfg["engines"] = ["paddle", "easyocr"] if fast_mode else ["paddle", "easyocr", "tesseract"]
        cfg["profiles"] = ["scan_strong", "scan_dense", "receipt"]
        cfg["psm_list"] = [6] if fast_mode else [3, 4, 6]
        if is_small:
            cfg["profiles"].insert(0, "receipt")

    return cfg

# ── Fonctions manquantes ──────────────────────────────────────

def to_dict_profile(p) -> dict:
    return {
        'name': p.name,
        'steps': [getattr(s, '__name__', str(s)) for s in p.steps],
    }

def get_profile(name: str):
    return PROFILES.get(name, list(PROFILES.values())[0])

def list_profiles():
    return [to_dict_profile(p) for p in PROFILES.values()]
