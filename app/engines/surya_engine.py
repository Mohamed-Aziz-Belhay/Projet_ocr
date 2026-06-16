"""
app/engines/surya_engine.py
Optional Surya OCR engine.

Implementation strategy:
- Try the official Python API documented by Surya
- Fallback to the official CLI `surya_ocr`
- Never hard-fail import/application startup when Surya is absent
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.core.logging import get_logger
from app.engines.engine_factory import BaseOCREngine, OCRResult, OCRWord, register_engine

log = get_logger(__name__)

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None


@register_engine
class SuryaOCREngine(BaseOCREngine):
    name = "surya"
    _predictors: Dict[str, Any] = {}

    def is_available(self) -> bool:
        try:
            import surya  # noqa: F401
            return True
        except Exception:
            return shutil.which("surya_ocr") is not None

    def _get_predictors(self):
        key = "default"
        if key in self._predictors:
            return self._predictors[key]
        try:
            from surya.foundation import FoundationPredictor
            from surya.recognition import RecognitionPredictor
            from surya.detection import DetectionPredictor

            foundation_predictor = FoundationPredictor()
            recognition_predictor = RecognitionPredictor(foundation_predictor)
            detection_predictor = DetectionPredictor()
            self._predictors[key] = (recognition_predictor, detection_predictor)
            return self._predictors[key]
        except Exception as exc:
            log.warning("Surya Python API unavailable, will try CLI", extra={"error": str(exc)})
            return None

    def _np_to_pil(self, image: np.ndarray):
        if Image is None:
            raise RuntimeError("Pillow not installed")
        if image.ndim == 2:
            return Image.fromarray(image)
        if cv2 is not None:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            rgb = image[:, :, ::-1]
        return Image.fromarray(rgb)

    def _run_python_api(self, image: np.ndarray) -> OCRResult:
        predictors = self._get_predictors()
        if predictors is None:
            raise RuntimeError("Surya Python API unavailable")
        recognition_predictor, detection_predictor = predictors
        pil_img = self._np_to_pil(image)

        t0 = time.time()
        predictions = recognition_predictor([pil_img], det_predictor=detection_predictor)
        elapsed_ms = int((time.time() - t0) * 1000)
        pred = predictions[0] if isinstance(predictions, list) else predictions
        return self._normalize_prediction(pred, elapsed_ms)

    def _run_cli(self, image: np.ndarray) -> OCRResult:
        if cv2 is None:
            raise RuntimeError("OpenCV not installed for Surya CLI fallback")
        cli = shutil.which("surya_ocr")
        if not cli:
            raise RuntimeError("surya_ocr command not found")
        with tempfile.TemporaryDirectory(prefix="surya_ocr_") as tmp:
            img_path = Path(tmp) / "input.png"
            out_dir = Path(tmp) / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(img_path), image):
                raise RuntimeError("Failed to write temp image for Surya")
            env = os.environ.copy()
            env.setdefault("PYTHONIOENCODING", "utf-8")
            t0 = time.time()
            proc = subprocess.run(
                [cli, str(img_path), "--output_dir", str(out_dir)],
                capture_output=True,
                text=True,
                env=env,
                timeout=300,
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or "surya_ocr failed").strip())
            results_path = out_dir / "results.json"
            if not results_path.exists():
                raise RuntimeError("Surya results.json not found")
            data = json.loads(results_path.read_text(encoding="utf-8"))
            pred = next(iter(data.values()))[0]
            return self._normalize_prediction(pred, elapsed_ms)

    def _normalize_prediction(self, pred: Any, elapsed_ms: int) -> OCRResult:
        if hasattr(pred, "model_dump"):
            pred = pred.model_dump()
        elif hasattr(pred, "dict"):
            pred = pred.dict()

        lines = []
        words: List[OCRWord] = []
        text_lines = []
        if isinstance(pred, dict):
            text_lines = pred.get("text_lines") or pred.get("lines") or []
        elif hasattr(pred, "text_lines"):
            text_lines = getattr(pred, "text_lines")

        for line in text_lines or []:
            if hasattr(line, "model_dump"):
                line = line.model_dump()
            elif hasattr(line, "dict"):
                line = line.dict()
            text = ""
            conf = 0.0
            bbox = (0, 0, 0, 0)
            line_words = []
            if isinstance(line, dict):
                text = str(line.get("text") or "")
                conf = float(line.get("confidence") or 0.0)
                b = line.get("bbox") or [0, 0, 0, 0]
                if len(b) == 4:
                    x1, y1, x2, y2 = map(int, b)
                    bbox = (x1, y1, max(0, x2 - x1), max(0, y2 - y1))
                line_words = line.get("words") or []
            else:
                text = str(getattr(line, "text", "") or "")
                conf = float(getattr(line, "confidence", 0.0) or 0.0)
                b = getattr(line, "bbox", [0, 0, 0, 0]) or [0, 0, 0, 0]
                if len(b) == 4:
                    x1, y1, x2, y2 = map(int, b)
                    bbox = (x1, y1, max(0, x2 - x1), max(0, y2 - y1))
                line_words = getattr(line, "words", []) or []

            if text:
                lines.append(text)

            if line_words:
                for word in line_words:
                    if hasattr(word, "model_dump"):
                        word = word.model_dump()
                    elif hasattr(word, "dict"):
                        word = word.dict()
                    if isinstance(word, dict):
                        wtext = str(word.get("text") or "")
                        wconf = float(word.get("confidence") or conf or 0.0)
                        wb = word.get("bbox") or [bbox[0], bbox[1], bbox[0] + bbox[2], bbox[1] + bbox[3]]
                    else:
                        wtext = str(getattr(word, "text", "") or "")
                        wconf = float(getattr(word, "confidence", conf) or 0.0)
                        wb = getattr(word, "bbox", [bbox[0], bbox[1], bbox[0] + bbox[2], bbox[1] + bbox[3]])
                    if len(wb) == 4:
                        x1, y1, x2, y2 = map(int, wb)
                        words.append(OCRWord(text=wtext, confidence=wconf, bbox=(x1, y1, max(0, x2 - x1), max(0, y2 - y1))))
            elif text:
                words.append(OCRWord(text=text, confidence=conf, bbox=bbox))

        return OCRResult(
            full_text="\n".join(lines),
            words=words,
            engine=self.name,
            processing_time_ms=elapsed_ms,
            meta={"backend": "surya"},
        )

    def run(self, image: np.ndarray, language: Optional[str] = None) -> OCRResult:
        if not self.is_available():
            return OCRResult(full_text="", engine=self.name)
        try:
            return self._run_python_api(image)
        except Exception as api_exc:
            log.warning("Surya Python API failed, trying CLI", extra={"error": str(api_exc)})
            try:
                return self._run_cli(image)
            except Exception as cli_exc:
                log.error("Surya OCR failed", extra={"api_error": str(api_exc), "cli_error": str(cli_exc)})
                return OCRResult(full_text="", engine=self.name)