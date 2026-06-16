#app/models/swin/prepare_midv2020.py
from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml
from PIL import Image


ALLOWED_VARIANTS = {"scan_upright", "scan_rotated", "templates"}
VISUAL_FIELDS = {"photo", "face", "signature"}

DATE_FIELD_NAMES = {
    "birth_date",
    "issue_date",
    "expiry_date",
    "birth_date_2",
    "birth_date_22",
}

TEXT_FIELD_ALIASES = {
    "number": "documentNumber",
    "number2": "documentNumber2",
    "number3": "documentNumber3",
    "id_number": "personalNumber",
    "name": "givenNames",
    "name_eng": "givenNamesLatin",
    "surname": "surname",
    "surname_eng": "surnameLatin",
    "surname_second": "surnameSecond",
    "nationality": "nationality",
    "gender": "gender",
    "birth_date": "birthDate",
    "birth_date_2": "birthDateShort",
    "birth_date_22": "birthDateShortRotated",
    "birth_place": "birthPlace",
    "birth_place_eng": "birthPlaceLatin",
    "issue_date": "issueDate",
    "expiry_date": "expiryDate",
    "authority": "issuingAuthority",
    "authority_eng": "issuingAuthorityLatin",
    "mrz_line0": "mrzLine0",
    "mrz_line1": "mrzLine1",
    "type": "documentTypeCode",
    "code": "countryCode",
    "height": "height",
}


def safe_slug(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("-", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def infer_class_name(json_path: Path, data: Dict[str, Any]) -> str:
    project_name = (
        data.get("_via_settings", {})
        .get("project", {})
        .get("name")
    )

    if project_name:
        project_name = project_name.replace("midv2020-", "")
        project_name = project_name.replace("midv2020_", "")
        return safe_slug(project_name)

    return safe_slug(json_path.stem)


def filename_stem(filename: str) -> str:
    return Path(filename).stem


def get_group_id(class_name: str, filename: str) -> str:
    return f"{class_name}/{filename_stem(filename)}"


def get_document_type(class_name: str) -> str:
    if "passport" in class_name:
        return "passport"

    return "id_document"


def iter_via_regions(data: Dict[str, Any]) -> Iterable[Tuple[str, List[Dict[str, Any]]]]:
    metadata = data.get("_via_img_metadata", {}) or {}

    for item in metadata.values():
        filename = item.get("filename")
        regions = item.get("regions", []) or []

        if filename:
            yield filename, regions


def get_image_size(image_path: Path) -> Optional[Tuple[int, int]]:
    try:
        with Image.open(image_path) as img:
            return img.size
    except Exception:
        return None


def find_image_path(
    *,
    raw_root: Path,
    variant: str,
    class_name: str,
    filename: str,
) -> Path:
    return raw_root / variant / "images" / class_name / filename


def find_annotation_files(raw_root: Path, variant: str) -> List[Path]:
    annotation_dir = raw_root / variant / "annotations"

    if not annotation_dir.exists():
        return []

    return sorted(annotation_dir.glob("*.json"))


def region_to_record(
    *,
    variant: str,
    class_name: str,
    filename: str,
    image_path: Path,
    image_size: Optional[Tuple[int, int]],
    region: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    shape = region.get("shape_attributes", {}) or {}
    attrs = region.get("region_attributes", {}) or {}

    if shape.get("name") != "rect":
        return None

    field_name = attrs.get("field_name")
    if not field_name:
        return None

    try:
        x = float(shape.get("x", 0))
        y = float(shape.get("y", 0))
        w = float(shape.get("width", 0))
        h = float(shape.get("height", 0))
    except Exception:
        return None

    if w <= 0 or h <= 0:
        return None

    bbox_abs = [x, y, w, h]
    bbox_norm = None

    if image_size:
        img_w, img_h = image_size

        if img_w > 0 and img_h > 0:
            bbox_norm = [
                round(x / img_w, 6),
                round(y / img_h, 6),
                round(w / img_w, 6),
                round(h / img_h, 6),
            ]

    value = attrs.get("value", "")
    orientation = attrs.get("orientation", "0")
    features = attrs.get("features", {}) or {}

    return {
        "variant": variant,
        "class_name": class_name,
        "group_id": get_group_id(class_name, filename),
        "filename": filename,
        "image_path": str(image_path),
        "image_width": image_size[0] if image_size else None,
        "image_height": image_size[1] if image_size else None,
        "field_name": field_name,
        "value": value,
        "bbox_abs": bbox_abs,
        "bbox_norm": bbox_norm,
        "orientation": orientation,
        "features": features,
        "is_visual_field": field_name in VISUAL_FIELDS,
    }


def infer_field_type(field_name: str, values: List[str]) -> str:
    if field_name in VISUAL_FIELDS:
        return "image"

    if field_name in DATE_FIELD_NAMES or field_name.endswith("_date"):
        return "date"

    if field_name.startswith("mrz_line"):
        return "mrz"

    if field_name == "gender":
        return "enum"

    if field_name == "height":
        return "number"

    return "text"


def average_bbox_norm(records: List[Dict[str, Any]]) -> Optional[List[float]]:
    boxes = [r.get("bbox_norm") for r in records if r.get("bbox_norm")]

    if not boxes:
        return None

    n = len(boxes)

    return [
        round(sum(box[i] for box in boxes) / n, 6)
        for i in range(4)
    ]


def is_required_field(field_name: str) -> bool:
    return field_name in {
        "number",
        "id_number",
        "surname",
        "name",
        "birth_date",
        "expiry_date",
        "mrz_line0",
        "mrz_line1",
    }


def build_template_yaml(
    *,
    class_name: str,
    records: List[Dict[str, Any]],
    template_source_variant: str,
) -> Dict[str, Any]:
    by_field: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for rec in records:
        if rec.get("variant") != template_source_variant:
            continue

        by_field[rec["field_name"]].append(rec)

    fields = []
    roi_fields = []
    output_mapping = {}

    for field_name in sorted(by_field):
        field_records = by_field[field_name]
        values = [
            str(r.get("value", ""))
            for r in field_records
            if r.get("value") not in (None, "")
        ]

        field_type = infer_field_type(field_name, values)
        output_key = TEXT_FIELD_ALIASES.get(field_name, field_name)
        required = is_required_field(field_name)

        field_cfg: Dict[str, Any] = {
            "name": field_name,
            "type": field_type,
            "required": required,
            "output_key": output_key,
        }

        if field_type == "date":
            field_cfg["patterns"] = [
                r"(\d{1,2}[.\-/ ]+\d{1,2}[.\-/ ]+\d{2,4})",
                r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})",
            ]

        elif field_type == "mrz":
            field_cfg["validation"] = {
                "min_length": 20,
                "max_length": 60,
            }

        elif field_name in {"number", "id_number"}:
            field_cfg["validation"] = {
                "min_length": 3,
                "max_length": 40,
            }

        fields.append(field_cfg)
        output_mapping[field_name] = output_key

        bbox_norm = average_bbox_norm(field_records)

        if bbox_norm:
            roi_fields.append(
                {
                    "name": field_name,
                    "type": field_type,
                    "output_key": output_key,
                    "bbox_norm": bbox_norm,
                    "required": required,
                    "orientation": field_records[0].get("orientation", "0"),
                    "visual": field_name in VISUAL_FIELDS,
                }
            )

    return {
        "id": f"midv_{class_name}",
        "name": f"MIDV-2020 {class_name}",
        "document_type": get_document_type(class_name),
        "dataset": "midv2020",
        "source_class": class_name,
        "template_source_variant": template_source_variant,
        "language": "auto",
        "preferred_engine": "paddle",
        "engines": {
            "primary": "paddle",
            "secondary": "easyocr",
        },
        "fields": fields,
        "roi_fields": roi_fields,
        "output_mapping": output_mapping,
    }


def collect_variant_data(
    *,
    raw_root: Path,
    variant: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    image_items: List[Dict[str, Any]] = []
    region_records: List[Dict[str, Any]] = []

    json_files = find_annotation_files(raw_root, variant)

    if not json_files:
        print(f"[WARN] no annotations found for variant={variant}")
        return image_items, region_records

    for json_path in json_files:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        class_name = infer_class_name(json_path, data)

        for filename, regions in iter_via_regions(data):
            image_path = find_image_path(
                raw_root=raw_root,
                variant=variant,
                class_name=class_name,
                filename=filename,
            )

            image_size = get_image_size(image_path)

            image_items.append(
                {
                    "variant": variant,
                    "image_path": str(image_path),
                    "filename": filename,
                    "class_name": class_name,
                    "group_id": get_group_id(class_name, filename),
                    "document_type": get_document_type(class_name),
                    "json_file": str(json_path),
                    "exists": image_path.exists(),
                }
            )

            for region in regions:
                rec = region_to_record(
                    variant=variant,
                    class_name=class_name,
                    filename=filename,
                    image_path=image_path,
                    image_size=image_size,
                    region=region,
                )

                if rec:
                    region_records.append(rec)

    return image_items, region_records


def group_split(
    *,
    image_items: List[Dict[str, Any]],
    val_ratio: float,
    seed: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for item in image_items:
        groups[item["group_id"]].append(item)

    group_ids = sorted(groups.keys())

    rng = random.Random(seed)
    rng.shuffle(group_ids)

    val_count = int(round(len(group_ids) * val_ratio))
    val_count = max(1, val_count) if group_ids else 0

    val_groups = set(group_ids[:val_count])

    train_items: List[Dict[str, Any]] = []
    val_items: List[Dict[str, Any]] = []

    for group_id in group_ids:
        if group_id in val_groups:
            val_items.extend(groups[group_id])
        else:
            train_items.extend(groups[group_id])

    return train_items, val_items


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare_midv2020(
    *,
    raw_root: Path,
    variants: List[str],
    template_source_variant: str,
    out_dir: Path,
    val_ratio: float,
    seed: int,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    templates_dir = out_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)

    all_image_items: List[Dict[str, Any]] = []
    all_region_records: List[Dict[str, Any]] = []

    for variant in variants:
        if variant not in ALLOWED_VARIANTS:
            raise ValueError(f"Unsupported variant: {variant}")

        image_items, region_records = collect_variant_data(
            raw_root=raw_root,
            variant=variant,
        )

        all_image_items.extend(image_items)
        all_region_records.extend(region_records)

        print(
            f"[OK] variant={variant} "
            f"images={len(image_items)} "
            f"regions={len(region_records)}"
        )

    if not all_image_items:
        raise RuntimeError("No images collected. Check --raw-root and variants.")

    if template_source_variant not in variants:
        raise ValueError(
            f"template_source_variant={template_source_variant} must be included in variants={variants}"
        )

    train_items, val_items = group_split(
        image_items=all_image_items,
        val_ratio=val_ratio,
        seed=seed,
    )

    write_jsonl(out_dir / "midv2020_manifest.jsonl", all_image_items)
    write_jsonl(out_dir / "train.jsonl", train_items)
    write_jsonl(out_dir / "val.jsonl", val_items)
    write_jsonl(out_dir / "regions.jsonl", all_region_records)

    labels = sorted({item["class_name"] for item in all_image_items})
    label_to_id = {label: idx for idx, label in enumerate(labels)}

    with (out_dir / "labels.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "labels": labels,
                "label_to_id": label_to_id,
                "id_to_label": {str(v): k for k, v in label_to_id.items()},
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    by_class_records: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for rec in all_region_records:
        by_class_records[rec["class_name"]].append(rec)

    template_count = 0

    for class_name, records in sorted(by_class_records.items()):
        template = build_template_yaml(
            class_name=class_name,
            records=records,
            template_source_variant=template_source_variant,
        )

        template_path = templates_dir / f"midv_{class_name}.yaml"

        with template_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(
                template,
                f,
                sort_keys=False,
                allow_unicode=True,
            )

        template_count += 1

    train_groups = {item["group_id"] for item in train_items}
    val_groups = {item["group_id"] for item in val_items}
    overlap = sorted(train_groups.intersection(val_groups))

    summary = {
        "raw_root": str(raw_root),
        "variants": variants,
        "template_source_variant": template_source_variant,
        "val_ratio": val_ratio,
        "seed": seed,
        "image_count": len(all_image_items),
        "region_count": len(all_region_records),
        "class_count": len(labels),
        "classes": labels,
        "train_image_count": len(train_items),
        "val_image_count": len(val_items),
        "train_group_count": len(train_groups),
        "val_group_count": len(val_groups),
        "group_overlap_count": len(overlap),
        "group_overlap": overlap[:20],
        "template_count": template_count,
    }

    with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("[DONE]")
    print(f"[OK] out_dir: {out_dir}")
    print(f"[OK] images: {len(all_image_items)}")
    print(f"[OK] regions: {len(all_region_records)}")
    print(f"[OK] classes: {len(labels)}")
    print(f"[OK] train images: {len(train_items)}")
    print(f"[OK] val images: {len(val_items)}")
    print(f"[OK] train groups: {len(train_groups)}")
    print(f"[OK] val groups: {len(val_groups)}")
    print(f"[OK] group overlap: {len(overlap)}")
    print(f"[OK] templates: {template_count}")

    if overlap:
        print("[WARN] group leakage detected. This should be 0.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--raw-root",
        default="app/data/external/midv2020/raw",
        help="MIDV raw root containing scan_upright, scan_rotated and templates.",
    )

    parser.add_argument(
        "--variant",
        choices=["scan_upright", "scan_rotated", "templates", "all"],
        default=None,
        help="Backward-compatible single variant option.",
    )

    parser.add_argument(
        "--variants",
        nargs="+",
        choices=["scan_upright", "scan_rotated", "templates"],
        default=None,
        help="One or more variants to include.",
    )

    parser.add_argument(
        "--template-source-variant",
        choices=["scan_upright", "scan_rotated", "templates"],
        default="templates",
        help="Variant used to generate stable ROI templates.",
    )

    parser.add_argument(
        "--out-dir",
        default="app/data/external/midv2020/prepared/all_variants",
    )

    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.3,
        help="Validation ratio. 0.3 means train=70%, val=30%.",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.variants:
        variants = args.variants

    elif args.variant == "all":
        variants = ["scan_upright", "scan_rotated", "templates"]

    elif args.variant:
        variants = [args.variant]

    else:
        variants = ["scan_upright", "scan_rotated", "templates"]

    prepare_midv2020(
        raw_root=Path(args.raw_root),
        variants=variants,
        template_source_variant=args.template_source_variant,
        out_dir=Path(args.out_dir),
        val_ratio=args.val_ratio,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()