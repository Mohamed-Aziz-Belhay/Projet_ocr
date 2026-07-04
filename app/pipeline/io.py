"""
app/pipeline/io.py
Pipeline I/O helpers: load any supported file into numpy page list,
write debug images, and serialise pipeline artefacts.

MODIFICATION : ajout d'un paramètre optionnel `preprocess` à
load_file_as_pages(), désactivé par défaut. Quand activé, chaque page
chargée passe par app.pipeline.preprocessing.preprocess_for_ocr() avant
d'être retournée (deskew + débruitage/contraste conditionnels selon la
qualité estimée). Le comportement par défaut (preprocess=False) reste
strictement identique à l'original — le guard de type documentaire
(routes_extract.py), qui n'a besoin que de rapidité, continue de
l'appeler sans ce paramètre et n'est donc pas affecté.
"""
from __future__ import annotations
import base64
import json
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp"}


def load_file_as_pages(
    file_path: str,
    dpi: int = 200,
    preprocess: bool = False,
) -> List[np.ndarray]:
    """
    Universal loader: returns list of BGR numpy arrays (one per page).
    Raises ValueError for unsupported formats.

    Args:
        file_path: chemin du document à charger.
        dpi: résolution de rendu pour les PDF.
        preprocess: si True, applique le prétraitement qualité (deskew,
            débruitage et contraste conditionnels) à chaque page avant de
            la retourner. Désactivé par défaut pour ne pas modifier le
            comportement existant (notamment le guard de type documentaire,
            qui privilégie la rapidité et n'appelle jamais ce paramètre).
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file extension: '{ext}'")

    if ext == ".pdf":
        pages = _load_pdf(str(path), dpi=dpi)
    else:
        pages = [_load_image(str(path))]

    if preprocess:
        pages = _preprocess_pages(pages, file_path=str(path))

    return pages


def _preprocess_pages(pages: List[np.ndarray], *, file_path: str) -> List[np.ndarray]:
    """
    Applique preprocess_for_ocr() à chaque page, en journalisant le
    rapport de diagnostic (score de flou, étapes appliquées) pour chaque
    page traitée.
    """
    from app.pipeline.preprocessing import preprocess_for_ocr

    processed: List[np.ndarray] = []

    for idx, page in enumerate(pages):
        try:
            result, report = preprocess_for_ocr(page)
            processed.append(result)
            log.info(
                "Page preprocessed",
                extra={
                    "file_path": file_path,
                    "page_index": idx,
                    "blur_score": round(report.blur_score, 1),
                    "steps_applied": report.steps_applied,
                },
            )
        except Exception as exc:
            # En cas d'echec du pretraitement, on retombe sur la page
            # d'origine plutot que de faire echouer tout le chargement.
            log.warning(
                "Page preprocessing failed, using original page",
                extra={"file_path": file_path, "page_index": idx, "error": str(exc)},
            )
            processed.append(page)

    return processed


def _load_image(path: str) -> np.ndarray:
    import cv2

    file_path = Path(path)

    data = np.fromfile(str(file_path), dtype=np.uint8)

    if data.size == 0:
        raise IOError(f"Empty image file: {path}")

    img = cv2.imdecode(data, cv2.IMREAD_COLOR)

    if img is None:
        size = file_path.stat().st_size if file_path.exists() else "missing"
        raise IOError(
            f"cv2.imdecode failed for: {path} "
            f"(size={size} bytes, suffix={file_path.suffix.lower()})"
        )

    return img


def _load_pdf(path: str, dpi: int = 200) -> List[np.ndarray]:
    try:
        import fitz
    except ImportError:
        raise ImportError("pymupdf required for PDF support: pip install pymupdf")

    doc = fitz.open(path)
    pages = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        import cv2
        pages.append(cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
    return pages


def image_to_base64(image: np.ndarray, fmt: str = ".png") -> str:
    """Encode numpy BGR image to base64 string (for JSON embedding)."""
    import cv2
    ok, buf = cv2.imencode(fmt, image)
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def save_debug_image(image: np.ndarray, path: str) -> None:
    """Write a debug image to disk — only in DEBUG mode."""
    import cv2
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(path, image)
    log.debug("Debug image saved", extra={"path": path})


def get_image_info(image: np.ndarray) -> dict:
    """Return basic metadata about an image array."""
    h, w = image.shape[:2]
    channels = image.shape[2] if len(image.shape) == 3 else 1
    return {
        "width": w,
        "height": h,
        "channels": channels,
        "size_kb": round(image.nbytes / 1024, 1),
        "aspect_ratio": round(w / h, 3) if h > 0 else 0,
    }