#document_detector/train_detector.py
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def train_detector(
    *,
    data_yaml: Path,
    out_dir: Path,
    model_name: str,
    epochs: int,
    imgsz: int,
    batch: int,
) -> None:
    try:
        from ultralytics import YOLO
    except Exception as exc:
        raise RuntimeError(
            "ultralytics is not installed. Run: pip install ultralytics==8.2.103"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(model_name)

    model.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        project=str(out_dir),
        name="train",
        exist_ok=True,
        patience=20,
        verbose=True,
    )

    best_src = out_dir / "train" / "weights" / "best.pt"

    if not best_src.exists():
        raise RuntimeError(f"YOLO training finished but best.pt was not found: {best_src}")

    best_dst = out_dir / "best.pt"
    shutil.copy2(best_src, best_dst)

    print("[DONE] detector trained")
    print(f"[OK] best checkpoint: {best_dst}")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--data-yaml",
        default="app/data/external/midv2020/detection_yolo/data.yaml",
    )

    parser.add_argument(
        "--out-dir",
        default="models/document_detector",
    )

    parser.add_argument(
        "--model-name",
        default="yolov8n.pt",
    )

    parser.add_argument(
        "--epochs",
        type=int,
        default=50,
    )

    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
    )

    parser.add_argument(
        "--batch",
        type=int,
        default=8,
    )

    args = parser.parse_args()

    train_detector(
        data_yaml=Path(args.data_yaml),
        out_dir=Path(args.out_dir),
        model_name=args.model_name,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
    )


if __name__ == "__main__":
    main()


    