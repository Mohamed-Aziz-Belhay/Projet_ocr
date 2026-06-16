#document_detector/prepare_detection_dataset.py
from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
import yaml


KEEP_VARIANTS = {"scan_upright", "scan_rotated"}


def read_jsonl(path: Path) -> List[dict]:
    rows: List[dict] = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    return rows


def safe_name(item: dict) -> str:
    variant = item.get("variant", "unknown")
    class_name = item.get("class_name", "unknown")
    filename = Path(item.get("filename", "image.jpg")).stem
    return f"{variant}__{class_name}__{filename}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def bbox_to_yolo_line(
    bbox: Tuple[float, float, float, float],
    img_w: int,
    img_h: int,
) -> str:
    x1, y1, x2, y2 = bbox

    cx = ((x1 + x2) / 2.0) / img_w
    cy = ((y1 + y2) / 2.0) / img_h
    bw = (x2 - x1) / img_w
    bh = (y2 - y1) / img_h

    cx = clamp(cx, 0.0, 1.0)
    cy = clamp(cy, 0.0, 1.0)
    bw = clamp(bw, 0.0, 1.0)
    bh = clamp(bh, 0.0, 1.0)

    return f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def union_bbox_from_regions(regions: List[dict]) -> Optional[Tuple[float, float, float, float]]:
    boxes = []

    for region in regions:
        bbox = region.get("bbox_abs")

        if not bbox or len(bbox) != 4:
            continue

        x, y, w, h = [float(v) for v in bbox]

        if w <= 0 or h <= 0:
            continue

        boxes.append((x, y, x + w, y + h))

    if not boxes:
        return None

    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def expand_bbox(
    bbox: Tuple[float, float, float, float],
    img_w: int,
    img_h: int,
    pad_ratio: float,
) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox

    bw = max(1.0, x2 - x1)
    bh = max(1.0, y2 - y1)

    x1 -= bw * pad_ratio
    y1 -= bh * pad_ratio
    x2 += bw * pad_ratio
    y2 += bh * pad_ratio

    x1 = clamp(x1, 0, img_w - 1)
    y1 = clamp(y1, 0, img_h - 1)
    x2 = clamp(x2, 0, img_w - 1)
    y2 = clamp(y2, 0, img_h - 1)

    return x1, y1, x2, y2


def template_image_path_for_scan(
    raw_root: Path,
    class_name: str,
    filename: str,
) -> Optional[Path]:
    candidate = raw_root / "templates" / "images" / class_name / filename

    if candidate.exists():
        return candidate

    stem = Path(filename).stem

    for ext in [".jpg", ".jpeg", ".png", ".bmp"]:
        alt = raw_root / "templates" / "images" / class_name / f"{stem}{ext}"

        if alt.exists():
            return alt

    return None


def bbox_from_template_homography(
    *,
    template_img,
    scan_img,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Locate the template card inside scan image using ORB + homography.

    This is the cleanest option for MIDV because each scan has a matching
    template image of the same document instance.
    """

    if template_img is None or scan_img is None:
        return None

    if template_img.size == 0 or scan_img.size == 0:
        return None

    tpl_gray = cv2.cvtColor(template_img, cv2.COLOR_BGR2GRAY)
    scan_gray = cv2.cvtColor(scan_img, cv2.COLOR_BGR2GRAY)

    orb = cv2.ORB_create(
        nfeatures=4000,
        scaleFactor=1.2,
        nlevels=8,
        edgeThreshold=15,
        patchSize=31,
    )

    kp1, des1 = orb.detectAndCompute(tpl_gray, None)
    kp2, des2 = orb.detectAndCompute(scan_gray, None)

    if des1 is None or des2 is None:
        return None

    if len(kp1) < 10 or len(kp2) < 10:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

    matches = matcher.knnMatch(des1, des2, k=2)

    good = []

    for pair in matches:
        if len(pair) != 2:
            continue

        m, n = pair

        if m.distance < 0.75 * n.distance:
            good.append(m)

    if len(good) < 12:
        return None

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    H, mask = cv2.findHomography(
        src_pts,
        dst_pts,
        cv2.RANSAC,
        5.0,
    )

    if H is None or mask is None:
        return None

    inliers = int(mask.ravel().sum())

    if inliers < 10:
        return None

    th, tw = template_img.shape[:2]

    corners = np.float32(
        [
            [0, 0],
            [tw - 1, 0],
            [tw - 1, th - 1],
            [0, th - 1],
        ]
    ).reshape(-1, 1, 2)

    projected = cv2.perspectiveTransform(corners, H).reshape(-1, 2)

    xs = projected[:, 0]
    ys = projected[:, 1]

    img_h, img_w = scan_img.shape[:2]

    x1 = clamp(float(xs.min()), 0, img_w - 1)
    y1 = clamp(float(ys.min()), 0, img_h - 1)
    x2 = clamp(float(xs.max()), 0, img_w - 1)
    y2 = clamp(float(ys.max()), 0, img_h - 1)

    if x2 <= x1 or y2 <= y1:
        return None

    bw = x2 - x1
    bh = y2 - y1

    area_ratio = (bw * bh) / max(float(img_w * img_h), 1.0)
    aspect = max(bw, bh) / max(min(bw, bh), 1.0)

    # Sanity checks.
    if area_ratio < 0.005 or area_ratio > 0.35:
        return None

    if aspect < 1.05 or aspect > 3.50:
        return None

    # Small padding around detected card.
    return expand_bbox(
        (x1, y1, x2, y2),
        img_w=img_w,
        img_h=img_h,
        pad_ratio=0.035,
    )


def color_document_bbox(image) -> Optional[Tuple[float, float, float, float]]:
    """
    Fallback only.

    This may select the photo instead of the whole card, so it should not be
    the primary strategy.
    """

    if image is None or image.size == 0:
        return None

    img_h, img_w = image.shape[:2]
    image_area = float(img_h * img_w)

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _h, s, v = cv2.split(hsv)

    color_mask = ((s > 30) & (v > 45) & (v < 250)).astype(np.uint8) * 255
    dark_colored_mask = ((gray < 170) & (s > 10)).astype(np.uint8) * 255

    mask = cv2.bitwise_or(color_mask, dark_colored_mask)

    mask = cv2.medianBlur(mask, 5)

    open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, open_kernel, iterations=1)

    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, close_kernel, iterations=2)

    dilate_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 7))
    mask = cv2.dilate(mask, dilate_kernel, iterations=1)

    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    if not contours:
        return None

    candidates = []

    for contour in contours:
        contour_area = float(cv2.contourArea(contour))

        if contour_area <= 0:
            continue

        x, y, w, h = cv2.boundingRect(contour)

        if w <= 0 or h <= 0:
            continue

        bbox_area = float(w * h)
        area_ratio = bbox_area / max(image_area, 1.0)
        aspect = max(w, h) / max(min(w, h), 1)
        fill_ratio = contour_area / max(bbox_area, 1.0)

        touches_border = (
            x <= 5
            or y <= 5
            or (x + w) >= (img_w - 5)
            or (y + h) >= (img_h - 5)
        )

        if area_ratio < 0.008:
            continue

        if area_ratio > 0.20:
            continue

        if touches_border:
            continue

        if aspect < 1.20 or aspect > 3.20:
            continue

        if fill_ratio < 0.20:
            continue

        target_area_ratio = 0.07
        size_penalty = abs(area_ratio - target_area_ratio)

        target_aspect = 1.586
        aspect_penalty = abs(aspect - target_aspect) / target_aspect

        score = (
            1.20 * area_ratio
            + 0.30 * fill_ratio
            - 0.45 * size_penalty
            - 0.12 * aspect_penalty
        )

        candidates.append(
            {
                "score": score,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
            }
        )

    if not candidates:
        return None

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]

    x = best["x"]
    y = best["y"]
    w = best["w"]
    h = best["h"]

    return expand_bbox(
        (float(x), float(y), float(x + w), float(y + h)),
        img_w=img_w,
        img_h=img_h,
        pad_ratio=0.06,
    )


def fallback_field_bbox(
    regions: List[dict],
    img_w: int,
    img_h: int,
) -> Optional[Tuple[float, float, float, float]]:
    union = union_bbox_from_regions(regions)

    if union is None:
        return None

    return expand_bbox(
        union,
        img_w=img_w,
        img_h=img_h,
        pad_ratio=0.55,
    )


def prepare_split(
    *,
    split_name: str,
    items: List[dict],
    regions_by_image: Dict[str, List[dict]],
    raw_root: Path,
    out_dir: Path,
) -> int:
    images_dir = out_dir / "images" / split_name
    labels_dir = out_dir / "labels" / split_name

    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    skipped = 0
    used_homography = 0
    used_color = 0
    used_fallback = 0

    for item in items:
        variant = item.get("variant")

        if variant not in KEEP_VARIANTS:
            continue

        src_path = Path(item["image_path"])

        if not src_path.exists():
            skipped += 1
            continue

        scan_img = cv2.imread(str(src_path))

        if scan_img is None or scan_img.size == 0:
            skipped += 1
            continue

        img_h, img_w = scan_img.shape[:2]
        class_name = item.get("class_name", "")
        filename = item.get("filename", "")

        bbox = None

        tpl_path = template_image_path_for_scan(
            raw_root=raw_root,
            class_name=class_name,
            filename=filename,
        )

        if tpl_path is not None:
            template_img = cv2.imread(str(tpl_path))

            if template_img is not None and template_img.size > 0:
                bbox = bbox_from_template_homography(
                    template_img=template_img,
                    scan_img=scan_img,
                )

                if bbox is not None:
                    used_homography += 1

        if bbox is None:
            bbox = color_document_bbox(scan_img)

            if bbox is not None:
                used_color += 1

        if bbox is None:
            regions = regions_by_image.get(str(src_path), [])
            bbox = fallback_field_bbox(regions, img_w, img_h)

            if bbox is not None:
                used_fallback += 1

        if bbox is None:
            skipped += 1
            continue

        stem = safe_name(item)

        dst_image = images_dir / f"{stem}{src_path.suffix.lower()}"
        dst_label = labels_dir / f"{stem}.txt"

        shutil.copy2(src_path, dst_image)

        with dst_label.open("w", encoding="utf-8") as f:
            f.write(bbox_to_yolo_line(bbox, img_w, img_h) + "\n")

        count += 1

    print(
        f"[OK] split={split_name} count={count} skipped={skipped} "
        f"homography={used_homography} color={used_color} fallback={used_fallback}"
    )

    return count


def prepare_detection_dataset(
    *,
    prepared_dir: Path,
    raw_root: Path,
    out_dir: Path,
) -> None:
    train_items = read_jsonl(prepared_dir / "train.jsonl")
    val_items = read_jsonl(prepared_dir / "val.jsonl")
    regions = read_jsonl(prepared_dir / "regions.jsonl")

    regions_by_image: Dict[str, List[dict]] = defaultdict(list)

    for region in regions:
        image_path = region.get("image_path")

        if image_path:
            regions_by_image[str(Path(image_path))].append(region)

    if out_dir.exists():
        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    train_count = prepare_split(
        split_name="train",
        items=train_items,
        regions_by_image=regions_by_image,
        raw_root=raw_root,
        out_dir=out_dir,
    )

    val_count = prepare_split(
        split_name="val",
        items=val_items,
        regions_by_image=regions_by_image,
        raw_root=raw_root,
        out_dir=out_dir,
    )

    data_yaml = {
        "path": str(out_dir.resolve()).replace("\\", "/"),
        "train": "images/train",
        "val": "images/val",
        "names": {
            0: "document",
        },
    }

    with (out_dir / "data.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(data_yaml, f, sort_keys=False, allow_unicode=True)

    summary = {
        "prepared_dir": str(prepared_dir),
        "raw_root": str(raw_root),
        "out_dir": str(out_dir),
        "variants_used": sorted(KEEP_VARIANTS),
        "train_count": train_count,
        "val_count": val_count,
        "class_count": 1,
        "classes": ["document"],
        "label_strategy": "template_homography_first_color_fallback_field_fallback",
    }

    with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("[DONE] detection dataset prepared")
    print(f"[OK] data yaml: {out_dir / 'data.yaml'}")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--prepared-dir",
        default="app/data/external/midv2020/prepared/all_variants",
    )

    parser.add_argument(
        "--raw-root",
        default="app/data/external/midv2020/raw",
    )

    parser.add_argument(
        "--out-dir",
        default="app/data/external/midv2020/detection_yolo",
    )

    args = parser.parse_args()

    prepare_detection_dataset(
        prepared_dir=Path(args.prepared_dir),
        raw_root=Path(args.raw_root),
        out_dir=Path(args.out_dir),
    )


if __name__ == "__main__":
    main()