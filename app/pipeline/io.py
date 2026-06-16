"""
app/pipeline/io.py
Pipeline I/O helpers: load any supported file into numpy page list,
write debug images, and serialise pipeline artefacts.
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


def load_file_as_pages(file_path: str, dpi: int = 200) -> List[np.ndarray]:
    """
    Universal loader: returns list of BGR numpy arrays (one per page).
    Raises ValueError for unsupported formats.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file extension: '{ext}'")

    if ext == ".pdf":
        return _load_pdf(str(path), dpi=dpi)
    else:
        return [_load_image(str(path))]


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
