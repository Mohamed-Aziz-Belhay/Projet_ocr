from __future__ import annotations

import argparse
from pathlib import Path

import cv2


def draw_label(image_path: Path, label_path: Path, out_path: Path) -> None:
    image = cv2.imread(str(image_path))

    if image is None:
        raise RuntimeError(f"Cannot read image: {image_path}")

    h, w = image.shape[:2]

    if not label_path.exists():
        raise RuntimeError(f"Label not found: {label_path}")

    text = label_path.read_text(encoding="utf-8").strip()

    if not text:
        raise RuntimeError(f"Label file is empty: {label_path}")

    lines = text.splitlines()

    for line in lines:
        parts = line.strip().split()

        if len(parts) != 5:
            print(f"[WARN] invalid YOLO line skipped: {line}")
            continue

        cls, cx, cy, bw, bh = parts

        cx = float(cx) * w
        cy = float(cy) * h
        bw = float(bw) * w
        bh = float(bh) * h

        x1 = int(round(cx - bw / 2))
        y1 = int(round(cy - bh / 2))
        x2 = int(round(cx + bw / 2))
        y2 = int(round(cy + bh / 2))

        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(0, min(w - 1, x2))
        y2 = max(0, min(h - 1, y2))

        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), 8)

        label = f"class={cls} x={x1}:{x2} y={y1}:{y2}"
        cv2.putText(
            image,
            label,
            (x1, max(40, y1 - 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.1,
            (0, 0, 255),
            3,
            cv2.LINE_AA,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), image)

    print(f"[OK] wrote {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--image", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", default="debug_yolo_label.jpg")

    args = parser.parse_args()

    draw_label(
        image_path=Path(args.image),
        label_path=Path(args.label),
        out_path=Path(args.out),
    )


if __name__ == "__main__":
    main()