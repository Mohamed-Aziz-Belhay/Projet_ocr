"""
app/routers/routes_swin.py

Routes admin Swin :
- GET  /swin/status
- POST /swin/predict
- POST /swin/train
- GET  /swin/jobs
- GET  /swin/training-report
- POST /swin/reload

Objectif :
Permettre à l'admin d'entraîner un classificateur Swin sur de nouveaux
documents, générer un best.pt actif et produire un JSON exploitable ensuite.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.core.tenant import TenantDep

log = get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/swin", tags=["Swin Transformer"])

# Dossiers utilisés par la section admin Swin
SWIN_JOBS_DIR = Path("app/data/swin_jobs")
SWIN_UPLOADS_DIR = Path("app/data/swin_uploads")
SWIN_ACTIVE_MODEL_DIR = Path("models/swin_doc_classifier")
SWIN_ARCHIVE_MODELS_DIR = Path("models/swin_doc_classifier_archive")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


# ─────────────────────────────────────────────────────────────
# Helpers généraux
# ─────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def _safe_slug(value: str) -> str:
    value = str(value or "").strip().lower()
    value = value.replace("-", "_")
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_") or "custom_document"


def _json_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _json_read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_job(job_id: str, data: dict) -> None:
    SWIN_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _json_write(SWIN_JOBS_DIR / f"{job_id}.json", data)


def _read_jobs() -> List[dict]:
    SWIN_JOBS_DIR.mkdir(parents=True, exist_ok=True)

    items: List[dict] = []
    for path in sorted(SWIN_JOBS_DIR.glob("*.json"), reverse=True):
        item = _json_read(path)
        if item:
            items.append(item)

    return items


def _find_dataset_root(extracted_dir: Path) -> Path:
    """
    Accepte deux formats :
    1) zip/train/... et zip/val/...
    2) zip/mon_dataset/train/... et zip/mon_dataset/val/...
    """
    if (extracted_dir / "train").is_dir() and (extracted_dir / "val").is_dir():
        return extracted_dir

    children = [p for p in extracted_dir.iterdir() if p.is_dir()]

    if len(children) == 1:
        candidate = children[0]
        if (candidate / "train").is_dir() and (candidate / "val").is_dir():
            return candidate

    raise RuntimeError(
        "Structure dataset invalide. Le zip doit contenir train/ et val/, "
        "ou un dossier racine contenant train/ et val/."
    )


def _build_manifest_from_folders(dataset_root: Path, prepared_dir: Path) -> dict:
    """
    Transforme :
      train/classe_a/*.jpg
      val/classe_a/*.jpg

    en :
      train.jsonl
      val.jsonl
      labels.json

    Format attendu par app/models/swin/train_swin_doc_classifier.py.
    """
    prepared_dir.mkdir(parents=True, exist_ok=True)

    train_dir = dataset_root / "train"
    val_dir = dataset_root / "val"

    if not train_dir.is_dir():
        raise RuntimeError("Dossier train/ introuvable dans le dataset.")
    if not val_dir.is_dir():
        raise RuntimeError("Dossier val/ introuvable dans le dataset.")

    labels = sorted([p.name for p in train_dir.iterdir() if p.is_dir()])

    if not labels:
        raise RuntimeError("Aucune classe trouvée dans train/.")

    label_to_id = {label: idx for idx, label in enumerate(labels)}

    def write_split(split: str) -> int:
        split_dir = dataset_root / split
        out_path = prepared_dir / f"{split}.jsonl"
        count = 0

        with out_path.open("w", encoding="utf-8") as f:
            for label in labels:
                class_dir = split_dir / label
                if not class_dir.is_dir():
                    continue

                for img in sorted(class_dir.rglob("*")):
                    if img.suffix.lower() not in IMAGE_EXTS:
                        continue

                    row = {
                        "image_path": str(img.resolve()),
                        "class_name": label,
                        "document_type": label,
                        "filename": img.name,
                    }

                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1

        return count

    train_count = write_split("train")
    val_count = write_split("val")

    if train_count == 0:
        raise RuntimeError("Aucune image trouvée dans train/.")
    if val_count == 0:
        raise RuntimeError("Aucune image trouvée dans val/.")

    labels_json = {
        "labels": labels,
        "label_to_id": label_to_id,
        "id_to_label": {str(v): k for k, v in label_to_id.items()},
    }

    _json_write(prepared_dir / "labels.json", labels_json)

    return {
        "labels": labels,
        "label_to_id": label_to_id,
        "train_count": train_count,
        "val_count": val_count,
    }


def _active_checkpoint_path() -> Path:
    return SWIN_ACTIVE_MODEL_DIR / "best.pt"


def _load_active_labels() -> List[str]:
    labels_path = SWIN_ACTIVE_MODEL_DIR / "labels.json"

    if labels_path.exists():
        data = _json_read(labels_path)
        labels = data.get("labels")
        if isinstance(labels, list):
            return labels

    config_path = SWIN_ACTIVE_MODEL_DIR / "document_config.json"
    if config_path.exists():
        data = _json_read(config_path)
        labels = data.get("labels")
        if isinstance(labels, list):
            return labels

    ckpt_path = _active_checkpoint_path()
    if ckpt_path.exists():
        try:
            import torch

            ckpt = torch.load(ckpt_path, map_location="cpu")
            labels = ckpt.get("labels") or []
            if isinstance(labels, list):
                return labels
        except Exception:
            return []

    return []


def _threshold() -> float:
    return float(getattr(settings, "SWIN_CONFIDENCE_THRESHOLD", 0.65))


def _copy_model_to_active(out_dir: Path) -> None:
    """
    Copie le modèle entraîné vers models/swin_doc_classifier/
    pour que app.models.swin.predictor puisse le charger par défaut.
    """
    SWIN_ACTIVE_MODEL_DIR.mkdir(parents=True, exist_ok=True)

    best_src = out_dir / "best.pt"
    if not best_src.exists():
        raise RuntimeError(f"best.pt introuvable : {best_src}")

    shutil.copy2(best_src, SWIN_ACTIVE_MODEL_DIR / "best.pt")

    for name in ["labels.json", "document_config.json", "training_report.json"]:
        src = out_dir / name
        if src.exists():
            shutil.copy2(src, SWIN_ACTIVE_MODEL_DIR / name)


def _clear_swin_predictor_cache() -> None:
    """
    Recharge le singleton Swin basé sur app.models.swin.predictor.
    """
    try:
        from app.models.swin.predictor import get_swin_document_classifier

        get_swin_document_classifier.cache_clear()
    except Exception as exc:
        log.warning("Impossible de vider le cache Swin predictor", extra={"error": str(exc)})


def _document_type_for_label(label: str) -> str:
    """
    Mapping simple.
    Tu peux l'améliorer selon tes familles documentaires.
    """
    low = str(label or "").lower()

    if "passport" in low or "passeport" in low:
        return "passport"

    if "invoice" in low or "facture" in low:
        return "invoice"

    if "registre" in low or "commerce" in low or "rne" in low:
        return "registre_commerce"

    if "cin" in low:
        return "cin_tn"

    if "id" in low or "identity" in low or "carte" in low:
        return "id_document"

    return low or "custom"


def _template_id_for_label(label: str, registry: Optional[dict] = None) -> Optional[str]:
    """
    Cherche template_id dans document_config.json ou model_registry.json.
    Sinon fallback : label.
    """
    label = str(label or "").strip()

    if registry:
        templates = registry.get("template_by_label") or {}
        if label in templates:
            return templates[label]

    config_path = SWIN_ACTIVE_MODEL_DIR / "document_config.json"
    if config_path.exists():
        cfg = _json_read(config_path)
        mapping = cfg.get("template_by_label") or {}
        if label in mapping:
            return mapping[label]

        template_id = cfg.get("template_id")
        labels = cfg.get("labels") or []
        if template_id and label in labels and len(labels) == 1:
            return template_id

    return label or None


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@router.get("/status", summary="Statut du classificateur Swin")
def swin_status():
    ckpt_path = _active_checkpoint_path()
    labels = _load_active_labels()
    config_path = SWIN_ACTIVE_MODEL_DIR / "document_config.json"

    if not ckpt_path.exists():
        return {
            "active": False,
            "available": False,
            "reason": "Aucun checkpoint Swin actif trouvé.",
            "checkpoint_path": str(ckpt_path),
            "model_path": str(SWIN_ACTIVE_MODEL_DIR),
            "threshold": _threshold(),
            "labels": labels,
            "workflow": {
                "step_1": "Préparer un dataset.zip avec train/<classe>/*.jpg et val/<classe>/*.jpg",
                "step_2": "POST /swin/train depuis l'interface admin",
                "step_3": "Le backend crée models/swin_doc_classifier/best.pt",
                "step_4": "Tester avec POST /swin/predict",
            },
        }

    try:
        from app.models.swin.predictor import get_swin_document_classifier

        clf = get_swin_document_classifier()

        return {
            "active": bool(clf.available),
            "available": bool(clf.available),
            "model_path": str(SWIN_ACTIVE_MODEL_DIR),
            "checkpoint_path": str(ckpt_path),
            "config_path": str(config_path) if config_path.exists() else None,
            "labels": labels or getattr(clf, "labels", []),
            "threshold": _threshold(),
            "device": str(getattr(clf, "device", "unknown")),
            "image_size": getattr(clf, "image_size", None),
            "reason": None if clf.available else "Checkpoint présent mais modèle non chargé.",
        }

    except Exception as exc:
        return {
            "active": False,
            "available": False,
            "model_path": str(SWIN_ACTIVE_MODEL_DIR),
            "checkpoint_path": str(ckpt_path),
            "labels": labels,
            "threshold": _threshold(),
            "error": str(exc),
        }


@router.post("/predict", summary="Classifier un document par image")
async def swin_predict(
    tenant: TenantDep,
    file: UploadFile = File(..., description="Image du document"),
):
    ckpt_path = _active_checkpoint_path()

    if not ckpt_path.exists():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Aucun modèle Swin actif",
                "checkpoint_path": str(ckpt_path),
                "action": "Lancer d'abord POST /swin/train depuis l'admin.",
            },
        )

    content = await file.read()

    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(content, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        if image is None:
            raise ValueError("Image non décodable")

    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Image invalide : {exc}")

    try:
        from app.models.swin.predictor import get_swin_document_classifier

        clf = get_swin_document_classifier()
        pred = clf.predict_array(image)

        label = (
            pred.get("document_class")
            or pred.get("predicted_class")
            or pred.get("document_type")
        )

        confidence = float(pred.get("confidence") or 0.0)
        accepted = bool(pred.get("available")) and confidence >= _threshold()

        registry = _json_read(SWIN_ACTIVE_MODEL_DIR / "document_config.json")

        document_type = pred.get("document_type") or _document_type_for_label(label)
        template_id = pred.get("template_id") or _template_id_for_label(label, registry)

        return {
            "filename": file.filename,
            "available": bool(pred.get("available")),
            "document_class": label,
            "predicted_class": label,
            "document_type": document_type,
            "template_id": template_id,
            "confidence": confidence,
            "accepted": accepted,
            "threshold": _threshold(),
            "method": pred.get("method") or "swin_image_classifier",
            "raw": pred,
        }

    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Prédiction Swin impossible : {exc}")


@router.post("/train", summary="Lancer un entraînement Swin depuis un dataset zip")
async def train_swin(
    tenant: TenantDep,
    dataset: UploadFile = File(..., description="dataset.zip contenant train/ et val/"),
    document_type: str = Form(...),
    template_id: str = Form(""),
    dataset_name: str = Form(""),
    model_name: str = Form("swin_tiny_patch4_window7_224"),
    epochs: int = Form(8),
    batch_size: int = Form(8),
    image_size: int = Form(224),
    learning_rate: float = Form(0.00003),
    validation_split: float = Form(0.2),
    notes: str = Form(""),
):
    """
    Cette route entraîne Swin en synchrone.
    En local c'est simple, mais pour production il vaut mieux passer par Celery/RQ/BackgroundTasks.
    """
    if not dataset.filename or not dataset.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Le dataset doit être un fichier .zip.")

    doc_type = _safe_slug(document_type)
    dataset_slug = _safe_slug(dataset_name or doc_type)

    job_id = f"swin_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    work_dir = SWIN_UPLOADS_DIR / job_id
    zip_path = work_dir / "dataset.zip"
    extracted_dir = work_dir / "extracted"
    prepared_dir = work_dir / "prepared"

    archive_out_dir = SWIN_ARCHIVE_MODELS_DIR / job_id
    archive_out_dir.mkdir(parents=True, exist_ok=True)

    job = {
        "id": job_id,
        "job_id": job_id,
        "status": "running",
        "document_type": doc_type,
        "template_id": template_id or None,
        "dataset_name": dataset_slug,
        "model_name": model_name,
        "epochs": epochs,
        "batch_size": batch_size,
        "image_size": image_size,
        "learning_rate": learning_rate,
        "validation_split": validation_split,
        "notes": notes,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }

    _write_job(job_id, job)

    try:
        work_dir.mkdir(parents=True, exist_ok=True)

        content = await dataset.read()
        zip_path.write_bytes(content)

        if extracted_dir.exists():
            shutil.rmtree(extracted_dir)

        extracted_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extracted_dir)
        except zipfile.BadZipFile as exc:
            raise RuntimeError("Fichier ZIP invalide.") from exc

        dataset_root = _find_dataset_root(extracted_dir)

        manifest_info = _build_manifest_from_folders(
            dataset_root=dataset_root,
            prepared_dir=prepared_dir,
        )

        cmd = [
            "python",
            "app/models/swin/train_swin_doc_classifier.py",
            "--prepared-dir", str(prepared_dir),
            "--out-dir", str(archive_out_dir),
            "--model-name", model_name,
            "--image-size", str(image_size),
            "--epochs", str(epochs),
            "--batch-size", str(batch_size),
            "--lr", str(learning_rate),
        ]

        job["command"] = " ".join(cmd)
        job["prepared_dir"] = str(prepared_dir)
        job["archive_output_dir"] = str(archive_out_dir)
        job["updated_at"] = _utc_now()
        _write_job(job_id, job)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        job["stdout"] = proc.stdout[-6000:]
        job["stderr"] = proc.stderr[-6000:]
        job["returncode"] = proc.returncode
        job["updated_at"] = _utc_now()

        if proc.returncode != 0:
            job["status"] = "failed"
            job["error"] = proc.stderr or proc.stdout or "Échec entraînement Swin."
            _write_job(job_id, job)
            raise HTTPException(status_code=500, detail=job["error"])

        best_pt = archive_out_dir / "best.pt"

        if not best_pt.exists():
            raise RuntimeError(f"best.pt introuvable après entraînement : {best_pt}")

        labels = manifest_info["labels"]

        template_by_label = {}
        for label in labels:
            if template_id and label == doc_type:
                template_by_label[label] = template_id
            else:
                template_by_label[label] = label

        document_config = {
            "kind": "swin_document_classifier_config",
            "job_id": job_id,
            "document_type": doc_type,
            "template_id": template_id or doc_type,
            "dataset_name": dataset_slug,
            "model_name": model_name,
            "model_path": str(SWIN_ACTIVE_MODEL_DIR),
            "checkpoint_path": str(SWIN_ACTIVE_MODEL_DIR / "best.pt"),
            "archive_model_path": str(archive_out_dir),
            "archive_checkpoint_path": str(best_pt),
            "labels": labels,
            "label_to_id": manifest_info["label_to_id"],
            "template_by_label": template_by_label,
            "threshold": _threshold(),
            "train_count": manifest_info["train_count"],
            "val_count": manifest_info["val_count"],
            "created_at": _utc_now(),
            "created_by": "admin",
            "status": "active",
            "notes": notes,
        }

        _json_write(archive_out_dir / "document_config.json", document_config)

        # Active ce modèle pour /swin/predict
        _copy_model_to_active(archive_out_dir)
        _clear_swin_predictor_cache()

        job["status"] = "completed"
        job["message"] = "Entraînement Swin terminé."
        job["labels"] = labels
        job["train_count"] = manifest_info["train_count"]
        job["val_count"] = manifest_info["val_count"]
        job["output_dir"] = str(SWIN_ACTIVE_MODEL_DIR)
        job["archive_output_dir"] = str(archive_out_dir)
        job["checkpoint_path"] = str(SWIN_ACTIVE_MODEL_DIR / "best.pt")
        job["json_config"] = str(SWIN_ACTIVE_MODEL_DIR / "document_config.json")
        job["updated_at"] = _utc_now()

        _write_job(job_id, job)

        return {
            "ok": True,
            "job_id": job_id,
            "status": "completed",
            "message": "Entraînement Swin terminé.",
            "output_dir": str(SWIN_ACTIVE_MODEL_DIR),
            "archive_output_dir": str(archive_out_dir),
            "checkpoint_path": str(SWIN_ACTIVE_MODEL_DIR / "best.pt"),
            "json_config": str(SWIN_ACTIVE_MODEL_DIR / "document_config.json"),
            "labels": labels,
            "train_count": manifest_info["train_count"],
            "val_count": manifest_info["val_count"],
            "document_config": document_config,
        }

    except HTTPException:
        raise

    except Exception as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        job["updated_at"] = _utc_now()
        _write_job(job_id, job)

        log.exception("Échec entraînement Swin", extra={"job_id": job_id, "error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs", summary="Lister les jobs Swin")
def list_swin_jobs():
    return {"items": _read_jobs()}


@router.get("/training-report", summary="Rapport du dernier entraînement Swin")
def training_report():
    """
    Retourne un rapport si présent.
    Sinon retourne le dernier job Swin.
    """
    report_path = SWIN_ACTIVE_MODEL_DIR / "training_report.json"

    if report_path.exists():
        return _json_read(report_path)

    jobs = _read_jobs()
    if jobs:
        return {
            "note": "Aucun training_report.json trouvé. Dernier job retourné.",
            "latest_job": jobs[0],
        }

    return {
        "error": "Aucun rapport ni job Swin trouvé.",
        "path": str(report_path),
    }


@router.post("/reload", summary="Recharger le modèle Swin sans redémarrer")
async def reload_swin(tenant: TenantDep):
    _clear_swin_predictor_cache()

    try:
        from app.models.swin.predictor import get_swin_document_classifier

        clf = get_swin_document_classifier()

        return {
            "reloaded": bool(clf.available),
            "status": swin_status(),
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))