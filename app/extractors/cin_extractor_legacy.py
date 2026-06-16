#!/usr/bin/env python3
"""
Document OCR + field extraction + classification pipeline.

Supports:
- OCR models: EasyOCR, Tesseract, PaddleOCR
- Classification: Swin (Hugging Face Transformers)
- Doc types: CIN, Facture, Registre de commerce

Output:
- Extracted text and fields per model
- Model config variable names and values
- Accuracy per model (if ground truth JSON is provided)
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image


# -----------------------------
# Config Dataclasses
# -----------------------------
@dataclass
class EasyOCRConfig:
    languages: Tuple[str, ...] = ("fr", "ar", "en")
    gpu: bool = False
    decoder: str = "greedy"
    paragraph: bool = True


@dataclass
class TesseractConfig:
    lang: str = "fra+ara+eng"
    psm: int = 6
    oem: int = 3
    tessdata_dir: Optional[str] = None


@dataclass
class PaddleOCRConfig:
    lang: str = "fr"
    use_angle_cls: bool = True
    use_gpu: bool = False
    show_log: bool = False


@dataclass
class SwinConfig:
    model_name: str = "microsoft/swin-tiny-patch4-window7-224"
    # If you have a finetuned checkpoint for CIN/FACTURE/REGISTRE_COMMERCE, set it here:
    finetuned_checkpoint: Optional[str] = None
    image_size: int = 224


# -----------------------------
# Helpers
# -----------------------------
def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def safe_open_image(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def text_confidence_mean(scores: List[float]) -> Optional[float]:
    valid = [s for s in scores if s is not None]
    return float(np.mean(valid)) if valid else None


# -----------------------------
# OCR Engines
# -----------------------------
class OCREngine:
    name: str = "base"

    def run(self, image_path: Path) -> Dict:
        raise NotImplementedError

    def model_config(self) -> Dict:
        raise NotImplementedError


class EasyOCREngine(OCREngine):
    name = "EasyOCR"

    def __init__(self, config: EasyOCRConfig):
        self.config = config
        try:
            import easyocr  # type: ignore
        except Exception as exc:
            raise ImportError("easyocr not installed. pip install easyocr") from exc
        self.reader = easyocr.Reader(list(config.languages), gpu=config.gpu)

    def run(self, image_path: Path) -> Dict:
        results = self.reader.readtext(
            str(image_path),
            decoder=self.config.decoder,
            paragraph=self.config.paragraph,
        )
        texts = [x[1] for x in results]
        confs = [float(x[2]) for x in results]
        return {
            "full_text": "\n".join(texts),
            "lines": texts,
            "confidence_mean": text_confidence_mean(confs),
        }

    def model_config(self) -> Dict:
        return asdict(self.config)


class TesseractEngine(OCREngine):
    name = "Tesseract"

    def __init__(self, config: TesseractConfig):
        self.config = config
        try:
            import pytesseract  # type: ignore
        except Exception as exc:
            raise ImportError("pytesseract not installed. pip install pytesseract") from exc
        self.pytesseract = pytesseract

    def run(self, image_path: Path) -> Dict:
        cfg = f"--oem {self.config.oem} --psm {self.config.psm}"
        if self.config.tessdata_dir:
            cfg += f" --tessdata-dir \"{self.config.tessdata_dir}\""
        text = self.pytesseract.image_to_string(
            str(image_path),
            lang=self.config.lang,
            config=cfg,
        )
        data = self.pytesseract.image_to_data(
            str(image_path),
            lang=self.config.lang,
            config=cfg,
            output_type=self.pytesseract.Output.DICT,
        )
        confs = []
        for c in data.get("conf", []):
            try:
                v = float(c)
                if v >= 0:
                    confs.append(v / 100.0)
            except Exception:
                pass
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return {
            "full_text": text.strip(),
            "lines": lines,
            "confidence_mean": text_confidence_mean(confs),
        }

    def model_config(self) -> Dict:
        return asdict(self.config)


class PaddleOCREngine(OCREngine):
    name = "PaddleOCR"

    def __init__(self, config: PaddleOCRConfig):
        self.config = config
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception as exc:
            raise ImportError("paddleocr not installed. pip install paddleocr") from exc
        self.ocr = PaddleOCR(
            use_angle_cls=config.use_angle_cls,
            lang=config.lang,
            use_gpu=config.use_gpu,
            show_log=config.show_log,
        )

    def run(self, image_path: Path) -> Dict:
        raw = self.ocr.ocr(str(image_path), cls=self.config.use_angle_cls)
        lines: List[str] = []
        confs: List[float] = []
        for page in raw:
            if not page:
                continue
            for item in page:
                # item = [bbox, (text, conf)]
                txt, conf = item[1][0], float(item[1][1])
                lines.append(txt)
                confs.append(conf)
        return {
            "full_text": "\n".join(lines),
            "lines": lines,
            "confidence_mean": text_confidence_mean(confs),
        }

    def model_config(self) -> Dict:
        return asdict(self.config)


# -----------------------------
# Swin Classifier
# -----------------------------
class SwinDocumentClassifier:
    """
    Uses Swin for classification. For best results, provide finetuned checkpoint
    trained on your 3 classes: CIN, FACTURE, REGISTRE_COMMERCE.
    """

    def __init__(self, config: SwinConfig):
        self.config = config
        try:
            from transformers import AutoImageProcessor, AutoModelForImageClassification  # type: ignore
        except Exception as exc:
            raise ImportError("transformers not installed. pip install transformers") from exc

        model_path = config.finetuned_checkpoint or config.model_name
        self.processor = AutoImageProcessor.from_pretrained(model_path)
        self.model = AutoModelForImageClassification.from_pretrained(model_path)
        self.id2label = getattr(self.model.config, "id2label", {})

    def predict(self, image_path: Path) -> Dict:
        import torch

        image = safe_open_image(image_path)
        inputs = self.processor(images=image, return_tensors="pt")
        with torch.no_grad():
            logits = self.model(**inputs).logits
        probs = torch.softmax(logits, dim=-1)[0].cpu().numpy()
        idx = int(np.argmax(probs))
        label = self.id2label.get(idx, f"label_{idx}")
        return {
            "predicted_label": label,
            "predicted_confidence": float(probs[idx]),
            "all_scores": {
                self.id2label.get(i, f"label_{i}"): float(p)
                for i, p in enumerate(probs.tolist())
            },
        }

    def model_config(self) -> Dict:
        return asdict(self.config)


# -----------------------------
# Field extraction rules
# -----------------------------
def extract_fields_cin(text: str) -> Dict[str, Optional[str]]:
    t = text
    return {
        "cin_number": first_match(
            t,
            [
                r"\b([A-Z]{1,2}\d{4,8})\b",
                r"\bCIN[:\s\-]*([A-Z0-9]{5,12})\b",
            ],
        ),
        "full_name": first_match(
            t,
            [r"(?:Nom|Name)[:\s\-]+([A-Z][A-Za-z\-\s]{3,60})"],
        ),
        "birth_date": first_match(
            t,
            [r"\b(\d{2}[\/\-\._]\d{2}[\/\-\._]\d{4})\b"],
        ),
        "address": first_match(
            t,
            [r"(?:Adresse|Address)[:\s\-]+(.{5,100})"],
        ),
    }


def extract_fields_facture(text: str) -> Dict[str, Optional[str]]:
    t = text
    return {
        "invoice_number": first_match(
            t,
            [r"(?:Facture|Invoice)\s*(?:N[°o]|No|#)?[:\s\-]*([A-Z0-9\-\/]{3,30})"],
        ),
        "invoice_date": first_match(
            t,
            [r"(?:Date)[:\s\-]*([0-3]?\d[\/\-\._][01]?\d[\/\-\._]\d{2,4})"],
        ),
        "total_ttc": first_match(
            t,
            [r"(?:Total\s*TTC|Montant\s*Total)[:\s\-]*([0-9][0-9\.,\s]{1,20})"],
        ),
        "ice": first_match(
            t,
            [r"\bICE[:\s\-]*([0-9]{8,20})\b"],
        ),
    }


def extract_fields_registre(text: str) -> Dict[str, Optional[str]]:
    t = text
    return {
        "rc_number": first_match(
            t,
            [r"(?:RC|Registre\s*de\s*commerce)[:\s\-]*([A-Z0-9\-\/]{3,30})"],
        ),
        "company_name": first_match(
            t,
            [r"(?:Raison\s*sociale|D[eé]nomination)[:\s\-]+(.{3,80})"],
        ),
        "if_number": first_match(
            t,
            [r"\bIF[:\s\-]*([0-9]{4,20})\b"],
        ),
        "ice": first_match(
            t,
            [r"\bICE[:\s\-]*([0-9]{8,20})\b"],
        ),
    }


def first_match(text: str, patterns: List[str]) -> Optional[str]:
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def extract_fields_by_doc_type(doc_type: str, text: str) -> Dict[str, Optional[str]]:
    doc_type = doc_type.upper()
    if doc_type == "CIN":
        return extract_fields_cin(text)
    if doc_type == "FACTURE":
        return extract_fields_facture(text)
    if doc_type in {"REGISTRE_COMMERCE", "REGISTRE"}:
        return extract_fields_registre(text)
    return {}


# -----------------------------
# Accuracy
# -----------------------------
def compute_field_accuracy(pred: Dict[str, Optional[str]], truth: Dict[str, str]) -> Dict:
    if not truth:
        return {"field_accuracy": None, "field_details": {}}

    details = {}
    total = 0
    correct = 0
    for field_name, gt in truth.items():
        pv = pred.get(field_name)
        ok = normalize_text(str(pv or "")) == normalize_text(str(gt or ""))
        details[field_name] = {
            "predicted": pv,
            "ground_truth": gt,
            "correct": ok,
        }
        total += 1
        if ok:
            correct += 1

    return {
        "field_accuracy": (correct / total) if total else None,
        "field_details": details,
    }


# -----------------------------
# Main pipeline
# -----------------------------
def build_ocr_engines(
    easy_cfg: EasyOCRConfig,
    tess_cfg: TesseractConfig,
    paddle_cfg: PaddleOCRConfig,
) -> List[OCREngine]:
    engines: List[OCREngine] = []
    for engine_cls, cfg in [
        (EasyOCREngine, easy_cfg),
        (TesseractEngine, tess_cfg),
        (PaddleOCREngine, paddle_cfg),
    ]:
        try:
            engines.append(engine_cls(cfg))
        except Exception as exc:
            print(f"[WARN] {engine_cls.__name__} unavailable: {exc}")
    return engines


def classify_with_keyword_fallback(text: str) -> str:
    t = normalize_text(text)
    if any(k in t for k in ["facture", "invoice", "ttc", "montant total"]):
        return "FACTURE"
    if any(k in t for k in ["registre de commerce", "raison sociale", "rc", "denomination"]):
        return "REGISTRE_COMMERCE"
    return "CIN"


def process_document(
    image_path: Path,
    output_dir: Path,
    target_doc_type: Optional[str],
    ground_truth_map: Dict,
    easy_cfg: EasyOCRConfig,
    tess_cfg: TesseractConfig,
    paddle_cfg: PaddleOCRConfig,
    swin_cfg: SwinConfig,
) -> Dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_key = image_path.name

    # Swin classification
    swin_output = {}
    predicted_doc_type = target_doc_type
    try:
        clf = SwinDocumentClassifier(swin_cfg)
        swin_output = clf.predict(image_path)
        if not predicted_doc_type:
            predicted_doc_type = str(swin_output["predicted_label"]).upper()
    except Exception as exc:
        swin_output = {"warning": f"Swin unavailable: {exc}"}

    # OCR
    engines = build_ocr_engines(easy_cfg, tess_cfg, paddle_cfg)
    if not engines:
        raise RuntimeError("No OCR engine available. Install at least one: easyocr/pytesseract/paddleocr.")

    doc_result = {
        "file": str(image_path),
        "doc_type_input": target_doc_type,
        "doc_type_used_for_extraction": predicted_doc_type or "CIN",
        "swin_classification": swin_output,
        "ocr_models": {},
        "model_configs": {
            "EasyOCRConfig": asdict(easy_cfg),
            "TesseractConfig": asdict(tess_cfg),
            "PaddleOCRConfig": asdict(paddle_cfg),
            "SwinConfig": asdict(swin_cfg),
        },
    }

    truth_item = ground_truth_map.get(file_key, {})
    truth_doc_type = truth_item.get("doc_type")
    truth_fields = truth_item.get("fields", {})

    for engine in engines:
        ocr_out = engine.run(image_path)
        full_text = ocr_out.get("full_text", "")
        effective_doc_type = (predicted_doc_type or classify_with_keyword_fallback(full_text)).upper()
        fields = extract_fields_by_doc_type(effective_doc_type, full_text)
        acc = compute_field_accuracy(fields, truth_fields)

        doc_result["ocr_models"][engine.name] = {
            "full_text": full_text,
            "lines": ocr_out.get("lines", []),
            "confidence_mean": ocr_out.get("confidence_mean"),
            "extracted_fields": fields,
            "accuracy": {
                "field_accuracy": acc["field_accuracy"],
                "field_details": acc["field_details"],
                "doc_type_match": (
                    normalize_text(str(effective_doc_type)) == normalize_text(str(truth_doc_type))
                    if truth_doc_type
                    else None
                ),
            },
            "config": engine.model_config(),
        }

    out_file = output_dir / f"{image_path.stem}_result.json"
    out_file.write_text(json.dumps(doc_result, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc_result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OCR extraction for CIN/Facture/Registre de commerce.")
    p.add_argument("--input", required=True, help="Image file path or folder containing images.")
    p.add_argument("--output", default="ocr_results", help="Output folder.")
    p.add_argument("--doc-type", default=None, choices=["CIN", "FACTURE", "REGISTRE_COMMERCE"], help="Force document type for field extraction.")
    p.add_argument("--ground-truth", default=None, help="Optional JSON file for accuracy calculation.")
    p.add_argument("--swin-checkpoint", default=None, help="Optional finetuned Swin checkpoint path.")
    p.add_argument("--easy-gpu", action="store_true", help="Use GPU for EasyOCR.")
    p.add_argument("--paddle-gpu", action="store_true", help="Use GPU for PaddleOCR.")
    p.add_argument("--tessdata-dir", default=None, help="Optional tessdata directory.")
    return p.parse_args()


def discover_images(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
    return [p for p in input_path.rglob("*") if p.suffix.lower() in exts]


def load_ground_truth(path: Optional[str]) -> Dict:
    if not path:
        return {}
    gt_path = Path(path)
    if not gt_path.exists():
        raise FileNotFoundError(f"ground truth file not found: {gt_path}")
    return json.loads(gt_path.read_text(encoding="utf-8"))


def summarize(global_results: List[Dict]) -> Dict:
    summary = {"documents": len(global_results), "per_model_accuracy_mean": {}}
    model_scores: Dict[str, List[float]] = {}
    for doc in global_results:
        for model_name, m in doc.get("ocr_models", {}).items():
            acc = m.get("accuracy", {}).get("field_accuracy")
            if acc is not None:
                model_scores.setdefault(model_name, []).append(float(acc))
    for model_name, vals in model_scores.items():
        summary["per_model_accuracy_mean"][model_name] = float(np.mean(vals)) if vals else None
    return summary


def main() -> None:
    args = parse_args()

    easy_cfg = EasyOCRConfig(gpu=args.easy_gpu)
    tess_cfg = TesseractConfig(tessdata_dir=args.tessdata_dir)
    paddle_cfg = PaddleOCRConfig(use_gpu=args.paddle_gpu)
    swin_cfg = SwinConfig(finetuned_checkpoint=args.swin_checkpoint)

    input_path = Path(args.input)
    output_dir = Path(args.output)
    ground_truth = load_ground_truth(args.ground_truth)

    images = discover_images(input_path)
    if not images:
        raise FileNotFoundError(f"No image found at: {input_path}")

    results = []
    for img in images:
        print(f"[INFO] Processing: {img}")
        res = process_document(
            image_path=img,
            output_dir=output_dir,
            target_doc_type=args.doc_type,
            ground_truth_map=ground_truth,
            easy_cfg=easy_cfg,
            tess_cfg=tess_cfg,
            paddle_cfg=paddle_cfg,
            swin_cfg=swin_cfg,
        )
        results.append(res)

    summary = summarize(results)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "all_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nDone.")
    print(f"Output folder: {output_dir.resolve()}")
    print("Summary:", json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
