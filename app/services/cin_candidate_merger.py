from __future__ import annotations
from typing import Any, Dict
from app.services.cin_text_normalizer import CINTextNormalizer
from app.services.cin_field_parsers import CINFieldParsers


class CINCandidateMerger:
    def __init__(self, normalizer: CINTextNormalizer | None = None, parsers: CINFieldParsers | None = None):
        self.norm = normalizer or CINTextNormalizer()
        self.parsers = parsers or CINFieldParsers(self.norm)

    def merge_label_spatial_into_spatial(self, spatial: Dict[str, Any], label_spatial: Dict[str, Any]) -> Dict[str, Any]:
        fields = dict(spatial.get("fields") or {})
        scores = dict(spatial.get("scores") or {})
        meta = dict(spatial.get("meta") or {})

        label_fields = label_spatial.get("fields") or {}
        label_scores = label_spatial.get("scores") or {}

        for key, value in label_fields.items():
            if not value:
                continue

            old = fields.get(key)
            should_replace = False

            if key == "family_name":
                if not old:
                    should_replace = True
                elif not self.parsers.looks_like_family_name(old):
                    should_replace = True
                else:
                    label_first = label_fields.get("first_name")
                    if label_first and self.norm.normalize_arabic_text(old) == self.norm.normalize_arabic_text(label_first):
                        should_replace = True

            elif key == "first_name":
                if not old:
                    should_replace = True
                elif not self.parsers.looks_like_first_name_phrase(old):
                    should_replace = True
                else:
                    label_family = label_fields.get("family_name")
                    if label_family and self.norm.normalize_arabic_text(old) == self.norm.normalize_arabic_text(label_family):
                        should_replace = True

            elif key == "date_of_birth":
                should_replace = not old

            elif key == "place_of_birth":
                old_norm = self.norm.normalize_arabic_text(old)
                new_norm = self.norm.normalize_arabic_text(value)
                should_replace = (
                    not old
                    or old_norm in {"الجم", "تونس"}
                    or (new_norm and new_norm != old_norm and len(new_norm) > len(old_norm))
                )

            if should_replace:
                fields[key] = value
                scores[key] = max(float(scores.get(key) or 0.0), float(label_scores.get(key) or 0.90))
                meta.setdefault("label_spatial_overrides", {})[key] = {"old": old, "new": value}
            else:
                meta.setdefault("label_spatial_kept_original", {})[key] = {"old": old, "candidate": value}

        meta["label_spatial"] = label_spatial.get("meta", {})
        return {**spatial, "fields": fields, "scores": scores, "meta": meta}