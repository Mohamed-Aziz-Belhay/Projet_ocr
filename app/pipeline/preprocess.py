"""
app/pipeline/preprocess.py
Image preprocessing helpers.

Backward compatible with the original preprocess(image) -> image contract,
while adding a richer bundle used by the specialized CIN pipeline.
"""
from __future__ import annotations
import gc
from typing import Dict, List

import cv2
import numpy as np

MAX_DIM = 2000
MIN_DIM = 800


def load_image(path: str) -> np.ndarray:
    img = cv2.imread(path)
    if img is None:
        raise ValueError(f"Impossible de charger : {path}")
    return img


def load_pdf_pages(path: str, dpi: int = 150) -> List[np.ndarray]:
    try:
        import fitz
    except ImportError:
        raise ImportError("Installer pymupdf : pip install pymupdf")
    doc = fitz.open(path)
    pages = []
    for page in doc:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        pages.append(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    return pages


def _resize_safe(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    max_hw = max(h, w)
    if max_hw > MAX_DIM:
        scale = MAX_DIM / max_hw
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    min_hw = min(h, w)
    if min_hw < MIN_DIM:
        scale = MIN_DIM / min_hw
        if int(w * scale) * int(h * scale) < MAX_DIM * MAX_DIM:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    return img


def compute_quality_score(image: np.ndarray) -> Dict[str, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    lap = cv2.Laplacian(gray, cv2.CV_64F).var()
    brightness = float(gray.mean()) / 255.0
    contrast = float(gray.std()) / 128.0
    sharpness = min(1.0, lap / 400.0)
    score = max(0.0, min(1.0, 0.45 * sharpness + 0.30 * contrast + 0.25 * brightness))
    return {
        "sharpness": round(sharpness, 3),
        "brightness": round(brightness, 3),
        "contrast": round(min(1.0, contrast), 3),
        "quality_score": round(score, 3),
    }


def deskew_image(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh < 255))
    if len(coords) < 100:
        return image
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.4:
        return image
    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def enhance_contrast(gray: np.ndarray) -> np.ndarray:
    if len(gray.shape) == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def preprocess(
    image: np.ndarray,
    deskew: bool = False,
    denoise: bool = True,
    upscale: bool = True,
) -> np.ndarray:
    img = _resize_safe(image)
    if deskew:
        img = deskew_image(img)
    gc.collect()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
    gray = enhance_contrast(gray)
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 10,
    )
    if denoise:
        binary = cv2.fastNlMeansDenoising(binary, h=10)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def generate_variants(image: np.ndarray) -> Dict[str, np.ndarray]:
    base = _resize_safe(image)
    gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY) if len(base.shape) == 3 else base
    contrast = enhance_contrast(gray)
    binary = cv2.adaptiveThreshold(
        contrast, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 10,
    )
    up = cv2.resize(base, None, fx=1.25, fy=1.25, interpolation=cv2.INTER_CUBIC)
    return {
        "base": base,
        "gray": cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR),
        "contrast": cv2.cvtColor(contrast, cv2.COLOR_GRAY2BGR),
        "binary": cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR),
        "upscaled": up,
    }


def preprocess_for_pipeline(image: np.ndarray) -> Dict[str, object]:
    resized = _resize_safe(image)
    deskewed = deskew_image(resized)
    variants = generate_variants(deskewed)
    quality = compute_quality_score(deskewed)
    return {
        "base": variants["base"],
        "variants": variants,
        "quality": quality,
        "transforms": {"resized": True, "deskewed": True, "contrast": True},
    }

# ── Fonctions manquantes à ajouter à la fin de app/pipeline/preprocess.py ────
# Colle ce bloc à la fin du fichier existant

def to_gray(image: np.ndarray) -> np.ndarray:
    if len(image.shape) == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def gray_to_bgr(image: np.ndarray) -> np.ndarray:
    if len(image.shape) == 3:
        return image
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


def clahe(gray: np.ndarray) -> np.ndarray:
    c = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return c.apply(gray)


def otsu_bin(gray: np.ndarray) -> np.ndarray:
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def adaptive_bin(gray: np.ndarray) -> np.ndarray:
    return cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 10,
    )


def denoise_bilateral(gray: np.ndarray) -> np.ndarray:
    return cv2.bilateralFilter(gray, d=9, sigmaColor=75, sigmaSpace=75)


def denoise_nlm(gray: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(gray, h=10)


def dilate(gray: np.ndarray, kernel_size: int = 2) -> np.ndarray:
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    return cv2.dilate(gray, kernel, iterations=1)


def erode(gray: np.ndarray, kernel_size: int = 2) -> np.ndarray:
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    return cv2.erode(gray, kernel, iterations=1)


def morph_close(gray: np.ndarray, kernel_size: int = 3) -> np.ndarray:
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    return cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)


def upscale(image: np.ndarray, factor: float = 1.5) -> np.ndarray:
    h, w = image.shape[:2]
    return cv2.resize(image, (int(w * factor), int(h * factor)), interpolation=cv2.INTER_CUBIC)