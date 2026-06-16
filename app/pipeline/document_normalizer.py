from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class NormalizedDocument:
    image: np.ndarray
    candidates: List[Dict[str, Any]]
    diagnostics: Dict[str, Any]


class DocumentNormalizer:
    """
    Classical CV document normalizer.

    Goal:
    - detect an ID/passport/card inside a large scanned page
    - crop it
    - correct perspective when possible
    - return multiple candidates
    - let the ROI extractor choose the best candidate using OCR/field score

    This version is designed for MIDV-like images:
    - templates: document fills most of the image
    - scan_upright: document is small on a white page
    - scan_rotated: document is small and rotated

    Main strategies:
    1. sliding_color_window:
       searches for the most colorful card-like rectangle on a white page.
    2. contour candidates:
       uses foreground mask + contours.
    3. original fallback:
       used only when no crop candidate is found.
    """

    def __init__(self) -> None:
        self.normalizer_name = "classical_cv_multi_candidate_v6_strict_color_nms"

    def normalize(
        self,
        image: np.ndarray,
        mode: str = "balanced",
        enable_rotation_candidates: bool = True,
    ) -> NormalizedDocument:
        if image is None or image.size == 0:
            raise ValueError("Empty image passed to DocumentNormalizer")

        original = image.copy()
        mode = str(mode or "balanced").lower().strip()

        detection_img, scale = self._resize_for_detection(original)

        mask = self._build_foreground_mask(detection_img)
        contours = self._find_contours(mask)

        contour_candidates = self._contours_to_candidates(
            contours=contours,
            detection_shape=detection_img.shape[:2],
            scale=scale,
        )

        sliding_candidates = self._sliding_color_window_candidates(
            detection_img=detection_img,
            original_shape=original.shape[:2],
            scale=scale,
        )

        # Sliding candidates first because they search for the whole card.
        candidates = sliding_candidates + contour_candidates

        crops: List[Dict[str, Any]] = []

        rejected = {
            "too_small": 0,
            "too_large": 0,
            "bad_aspect": 0,
            "touches_border_large": 0,
            "invalid_crop": 0,
        }

        for cand in candidates:
            reason = cand.get("reject_reason")

            if reason:
                if reason in rejected:
                    rejected[reason] += 1
                continue

            crop = self._crop_candidate(
                original=original,
                candidate=cand,
            )

            if crop is None or crop.size == 0:
                rejected["invalid_crop"] += 1
                continue

            crops.append(
                {
                    "image": crop,
                    "candidate": cand,
                    "source": cand.get("source", "unknown"),
                }
            )

        if not crops:
            fallback = self._force_landscape(original)

            crops.append(
                {
                    "image": fallback,
                    "candidate": None,
                    "source": "original_fallback",
                }
            )

        max_candidates = 12 if mode == "full" else 8
        crops = crops[:max_candidates]

        final_candidates: List[Dict[str, Any]] = []

        for idx, item in enumerate(crops):
            base = self._force_landscape(item["image"])
            source = item["source"]
            cand = item["candidate"]

            angles = self._candidate_angles(
                base,
                mode=mode,
                enable_rotation_candidates=enable_rotation_candidates,
            )

            for angle in angles:
                rotated = self._rotate_image(base, angle)

                final_candidates.append(
                    {
                        "image": rotated,
                        "angle": angle,
                        "candidate_index": idx,
                        "source": source,
                        "candidate": cand,
                    }
                )

        diagnostics = {
            "normalizer": self.normalizer_name,
            "input_shape": list(original.shape[:2]),
            "detection_shape": list(detection_img.shape[:2]),
            "scale": scale,
            "mode": mode,
            "found_contour": any(c["source"] != "original_fallback" for c in crops),
            "contours_total": len(contours),
            "sliding_candidate_count": len(sliding_candidates),
            "contour_candidate_count": len(contour_candidates),
            "candidate_pool_count": len(candidates),
            "crop_candidate_count": len(crops),
            "final_candidate_count": len(final_candidates),
            "rejected": rejected,
            "selected_strategy": "multi_candidate_roi_score",
            "candidates_preview": [
                self._candidate_preview(c.get("candidate"))
                for c in crops
            ],
            "candidate_angles": [c["angle"] for c in final_candidates],
        }

        self._save_debug_if_enabled(
            original=original,
            detection_img=detection_img,
            mask=mask,
            crops=crops,
            final_candidates=final_candidates,
            diagnostics=diagnostics,
        )

        return NormalizedDocument(
            image=final_candidates[0]["image"],
            candidates=final_candidates,
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _resize_for_detection(
        self,
        image: np.ndarray,
        max_dim: int = 1200,
    ) -> Tuple[np.ndarray, float]:
        h, w = image.shape[:2]
        largest = max(h, w)

        if largest <= max_dim:
            return image.copy(), 1.0

        scale = max_dim / float(largest)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))

        resized = cv2.resize(
            image,
            (new_w, new_h),
            interpolation=cv2.INTER_AREA,
        )

        return resized, scale

    def _build_foreground_mask(self, image: np.ndarray) -> np.ndarray:
        """
        Foreground mask for contour candidates.

        It is intentionally conservative; the sliding window strategy is
        responsible for card search on mostly white scanned pages.
        """

        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        _h, s, v = cv2.split(hsv)

        # Colored document areas.
        sat_mask = cv2.inRange(s, 38, 255)

        # Dark but not pure gray/white paper.
        dark_colored = np.where((v < 140) & (s > 10), 255, 0).astype(np.uint8)

        mask = cv2.bitwise_or(sat_mask, dark_colored)

        mask = cv2.medianBlur(mask, 5)

        kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close, iterations=1)

        kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.dilate(mask, kernel_dilate, iterations=1)

        return mask

    def _find_contours(self, mask: np.ndarray) -> List[np.ndarray]:
        contours, _ = cv2.findContours(
            mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE,
        )

        return sorted(contours, key=cv2.contourArea, reverse=True)

    def _contours_to_candidates(
        self,
        *,
        contours: List[np.ndarray],
        detection_shape: Tuple[int, int],
        scale: float,
    ) -> List[Dict[str, Any]]:
        det_h, det_w = detection_shape
        img_area = float(det_h * det_w)

        out: List[Dict[str, Any]] = []

        for idx, contour in enumerate(contours[:40]):
            area = float(cv2.contourArea(contour))
            area_ratio = area / max(img_area, 1.0)

            x, y, w, h = cv2.boundingRect(contour)

            touches_border = (
                x <= 3
                or y <= 3
                or x + w >= det_w - 3
                or y + h >= det_h - 3
            )

            rect = cv2.minAreaRect(contour)
            (_cx, _cy), (rw, rh), rect_angle = rect

            long_side = max(rw, rh)
            short_side = min(rw, rh)
            aspect = long_side / max(short_side, 1.0)

            target_aspect = 1.586
            aspect_penalty = abs(aspect - target_aspect)

            reject_reason: Optional[str] = None

            if area_ratio < 0.002:
                reject_reason = "too_small"

            elif area_ratio > 0.35:
                reject_reason = "too_large"

            elif aspect < 1.05 or aspect > 3.20:
                reject_reason = "bad_aspect"

            elif touches_border and area_ratio > 0.22:
                reject_reason = "touches_border_large"

            box = cv2.boxPoints(rect)
            box = np.array(box, dtype=np.float32)

            if scale > 0:
                box_original = box / scale
            else:
                box_original = box

            candidate = {
                "index": idx,
                "source": "contour_perspective",
                "score": None,
                "area_ratio": round(float(area_ratio), 6),
                "bbox_detection": [int(x), int(y), int(w), int(h)],
                "aspect": round(float(aspect), 4),
                "aspect_penalty": round(float(aspect_penalty), 4),
                "touches_border": bool(touches_border),
                "rect_angle": float(rect_angle),
                "box_original": box_original.tolist(),
                "reject_reason": reject_reason,
            }

            out.append(candidate)

            if reject_reason is None:
                out.append(
                    {
                        "index": idx,
                        "source": "contour_bbox",
                        "score": None,
                        "area_ratio": round(float(area_ratio), 6),
                        "bbox_detection": [int(x), int(y), int(w), int(h)],
                        "aspect": round(float(max(w, h) / max(min(w, h), 1)), 4),
                        "aspect_penalty": round(float(aspect_penalty), 4),
                        "touches_border": bool(touches_border),
                        "rect_angle": 0.0,
                        "box_original": self._bbox_to_box_original(
                            x=x,
                            y=y,
                            w=w,
                            h=h,
                            scale=scale,
                        ),
                        "reject_reason": None,
                    }
                )

        out.sort(
            key=lambda c: (
                c.get("reject_reason") is not None,
                float(c.get("aspect_penalty") or 999.0),
                -float(c.get("area_ratio") or 0.0),
            )
        )

        return out

    # ------------------------------------------------------------------
    # Sliding color-window strategy
    # ------------------------------------------------------------------

    def _sliding_color_window_candidates(
        self,
        *,
        detection_img: np.ndarray,
        original_shape: Tuple[int, int],
        scale: float,
    ) -> List[Dict[str, Any]]:
        """
        Find card-like windows on a mostly white scanned page.

        This version avoids treating gray/white paper as foreground.
        It scores:
        - foreground density
        - mean saturation
        - brightness variation
        - center foreground/saturation
        and applies NMS to avoid many duplicate windows.
        """

        det_h, det_w = detection_img.shape[:2]

        hsv = cv2.cvtColor(detection_img, cv2.COLOR_BGR2HSV)
        _h, s, v = cv2.split(hsv)

        # Strict foreground:
        # colored card regions + dark colored regions.
        color_mask = ((s > 38) & (v > 45) & (v < 252))
        dark_colored_mask = ((v < 130) & (s > 12))

        mask = np.logical_or(color_mask, dark_colored_mask).astype(np.uint8)

        # If too much foreground is detected, thresholds were too permissive.
        fg_ratio = float(mask.mean())

        if fg_ratio > 0.35:
            color_mask = ((s > 55) & (v > 55) & (v < 245))
            dark_colored_mask = ((v < 100) & (s > 20))
            mask = np.logical_or(color_mask, dark_colored_mask).astype(np.uint8)

        mask = cv2.medianBlur(mask * 255, 5)
        mask = (mask > 0).astype(np.uint8)

        integral_mask = cv2.integral(mask)
        integral_s = cv2.integral(s.astype(np.float32) / 255.0)
        integral_v = cv2.integral(v.astype(np.float32) / 255.0)
        integral_v2 = cv2.integral((v.astype(np.float32) / 255.0) ** 2)

        def isum(ii, x1: int, y1: int, x2: int, y2: int) -> float:
            return float(
                ii[y2, x2]
                - ii[y1, x2]
                - ii[y2, x1]
                + ii[y1, x1]
            )

        target_aspect = 1.586
        candidates: List[Dict[str, Any]] = []

        min_card_h = max(60, int(det_h * 0.06))
        max_card_h = min(int(det_h * 0.38), int(det_w / target_aspect))

        if max_card_h <= min_card_h:
            return []

        step_h = max(12, int(det_h * 0.018))
        stride = max(10, int(det_w * 0.018))

        for card_h in range(min_card_h, max_card_h + 1, step_h):
            card_w = int(round(card_h * target_aspect))

            if card_w <= 0 or card_w > det_w:
                continue

            max_y = max(1, det_h - card_h)
            max_x = max(1, det_w - card_w)

            for y in range(0, max_y, stride):
                for x in range(0, max_x, stride):
                    x2 = x + card_w
                    y2 = y + card_h

                    area = card_w * card_h

                    fg = isum(integral_mask, x, y, x2, y2)
                    density = fg / max(area, 1)

                    if density < 0.010:
                        continue

                    sat_sum = isum(integral_s, x, y, x2, y2)
                    mean_s = sat_sum / max(area, 1)

                    v_sum = isum(integral_v, x, y, x2, y2)
                    v2_sum = isum(integral_v2, x, y, x2, y2)

                    mean_v = v_sum / max(area, 1)
                    var_v = max((v2_sum / max(area, 1)) - (mean_v * mean_v), 0.0)
                    std_v = float(np.sqrt(var_v))

                    # Reject windows that are almost plain paper.
                    if mean_s < 0.035 and std_v < 0.045:
                        continue

                    center_x1 = x + int(card_w * 0.15)
                    center_y1 = y + int(card_h * 0.15)
                    center_x2 = x + int(card_w * 0.85)
                    center_y2 = y + int(card_h * 0.85)

                    center_area = max(
                        (center_x2 - center_x1) * (center_y2 - center_y1),
                        1,
                    )

                    center_fg = isum(
                        integral_mask,
                        center_x1,
                        center_y1,
                        center_x2,
                        center_y2,
                    )
                    center_density = center_fg / center_area

                    center_sat = isum(
                        integral_s,
                        center_x1,
                        center_y1,
                        center_x2,
                        center_y2,
                    ) / center_area

                    area_ratio = area / max(det_w * det_h, 1)

                    # Card is usually small/medium on MIDV scan images.
                    size_penalty = abs(area_ratio - 0.07)

                    border_penalty = 0.0

                    if x <= 5 or y <= 5 or x2 >= det_w - 5 or y2 >= det_h - 5:
                        border_penalty = 0.08

                    score = (
                        0.40 * density
                        + 0.55 * mean_s
                        + 0.25 * std_v
                        + 0.20 * center_density
                        + 0.35 * center_sat
                        - border_penalty
                        - size_penalty
                    )

                    candidates.append(
                        {
                            "index": len(candidates),
                            "source": "sliding_color_window",
                            "score": round(float(score), 6),
                            "density": round(float(density), 6),
                            "mean_saturation": round(float(mean_s), 6),
                            "std_value": round(float(std_v), 6),
                            "center_density": round(float(center_density), 6),
                            "center_saturation": round(float(center_sat), 6),
                            "area_ratio": round(float(area_ratio), 6),
                            "bbox_detection": [int(x), int(y), int(card_w), int(card_h)],
                            "aspect": round(float(card_w / max(card_h, 1)), 4),
                            "aspect_penalty": round(
                                abs((card_w / max(card_h, 1)) - target_aspect),
                                4,
                            ),
                            "touches_border": bool(border_penalty > 0),
                            "reject_reason": None,
                            "box_original": self._bbox_to_box_original(
                                x=x,
                                y=y,
                                w=card_w,
                                h=card_h,
                                scale=scale,
                            ),
                        }
                    )

        candidates.sort(
            key=lambda c: float(c.get("score", 0.0)),
            reverse=True,
        )

        return self._nms_window_candidates(
            candidates,
            iou_threshold=0.45,
            top_k=8,
        )

    def _nms_window_candidates(
        self,
        candidates: List[Dict[str, Any]],
        iou_threshold: float = 0.45,
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []

        for cand in candidates:
            if len(selected) >= top_k:
                break

            keep = True

            for old in selected:
                if self._bbox_iou(
                    cand.get("bbox_detection"),
                    old.get("bbox_detection"),
                ) >= iou_threshold:
                    keep = False
                    break

            if keep:
                selected.append(cand)

        return selected

    def _bbox_iou(
        self,
        a: Optional[List[int]],
        b: Optional[List[int]],
    ) -> float:
        if not a or not b or len(a) != 4 or len(b) != 4:
            return 0.0

        ax, ay, aw, ah = [float(x) for x in a]
        bx, by, bw, bh = [float(x) for x in b]

        ax2 = ax + aw
        ay2 = ay + ah
        bx2 = bx + bw
        by2 = by + bh

        ix1 = max(ax, bx)
        iy1 = max(ay, by)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)

        iw = max(0.0, ix2 - ix1)
        ih = max(0.0, iy2 - iy1)

        inter = iw * ih
        area_a = max(aw * ah, 1.0)
        area_b = max(bw * bh, 1.0)

        return inter / max(area_a + area_b - inter, 1.0)

    def _bbox_to_box_original(
        self,
        *,
        x: int,
        y: int,
        w: int,
        h: int,
        scale: float,
    ) -> List[List[float]]:
        if scale <= 0:
            scale = 1.0

        x1 = x / scale
        y1 = y / scale
        x2 = (x + w) / scale
        y2 = (y + h) / scale

        return [
            [float(x1), float(y1)],
            [float(x2), float(y1)],
            [float(x2), float(y2)],
            [float(x1), float(y2)],
        ]

    # ------------------------------------------------------------------
    # Crop / transform helpers
    # ------------------------------------------------------------------

    def _crop_candidate(
        self,
        *,
        original: np.ndarray,
        candidate: Dict[str, Any],
    ) -> Optional[np.ndarray]:
        box = np.array(candidate["box_original"], dtype=np.float32)

        if box.shape != (4, 2):
            return None

        ordered = self._order_points(box)

        width_a = np.linalg.norm(ordered[2] - ordered[3])
        width_b = np.linalg.norm(ordered[1] - ordered[0])
        max_width = int(round(max(width_a, width_b)))

        height_a = np.linalg.norm(ordered[1] - ordered[2])
        height_b = np.linalg.norm(ordered[0] - ordered[3])
        max_height = int(round(max(height_a, height_b)))

        if max_width < 40 or max_height < 25:
            return None

        dst = np.array(
            [
                [0, 0],
                [max_width - 1, 0],
                [max_width - 1, max_height - 1],
                [0, max_height - 1],
            ],
            dtype=np.float32,
        )

        matrix = cv2.getPerspectiveTransform(ordered, dst)
        warped = cv2.warpPerspective(
            original,
            matrix,
            (max_width, max_height),
        )

        if warped is None or warped.size == 0:
            return None

        return self._force_landscape(warped)

    def _order_points(
        self,
        pts: np.ndarray,
    ) -> np.ndarray:
        rect = np.zeros((4, 2), dtype=np.float32)

        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]

        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]

        return rect

    def _force_landscape(
        self,
        image: np.ndarray,
    ) -> np.ndarray:
        h, w = image.shape[:2]

        if h > w:
            return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)

        return image

    def _candidate_angles(
        self,
        image: np.ndarray,
        mode: str,
        enable_rotation_candidates: bool,
    ) -> List[int]:
        if not enable_rotation_candidates:
            return [0]

        mode = str(mode or "balanced").lower().strip()

        if mode == "full":
            return [0, 180, 90, 270]

        return [0, 180]

    def _rotate_image(
        self,
        image: np.ndarray,
        angle: int,
    ) -> np.ndarray:
        angle = int(angle) % 360

        if angle == 0:
            return image

        if angle == 90:
            return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)

        if angle == 180:
            return cv2.rotate(image, cv2.ROTATE_180)

        if angle == 270:
            return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)

        return image

    # ------------------------------------------------------------------
    # Diagnostics / debug
    # ------------------------------------------------------------------

    def _candidate_preview(
        self,
        candidate: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not candidate:
            return None

        return {
            "source": candidate.get("source"),
            "index": candidate.get("index"),
            "score": candidate.get("score"),
            "density": candidate.get("density"),
            "mean_saturation": candidate.get("mean_saturation"),
            "std_value": candidate.get("std_value"),
            "center_density": candidate.get("center_density"),
            "center_saturation": candidate.get("center_saturation"),
            "area_ratio": candidate.get("area_ratio"),
            "bbox_detection": candidate.get("bbox_detection"),
            "aspect": candidate.get("aspect"),
            "aspect_penalty": candidate.get("aspect_penalty"),
            "touches_border": candidate.get("touches_border"),
            "reject_reason": candidate.get("reject_reason"),
        }

    def _save_debug_if_enabled(
        self,
        *,
        original: np.ndarray,
        detection_img: np.ndarray,
        mask: np.ndarray,
        crops: List[Dict[str, Any]],
        final_candidates: List[Dict[str, Any]],
        diagnostics: Dict[str, Any],
    ) -> None:
        if os.getenv("SAVE_NORMALIZER_DEBUG", "0").lower() not in {"1", "true", "yes"}:
            return

        try:
            debug_root = Path(os.getenv("NORMALIZER_DEBUG_DIR", "debug_normalizer"))
            run_dir = debug_root / f"run_{int(time.time() * 1000)}"
            run_dir.mkdir(parents=True, exist_ok=True)

            cv2.imwrite(str(run_dir / "00_original.jpg"), original)
            cv2.imwrite(str(run_dir / "01_detection.jpg"), detection_img)
            cv2.imwrite(str(run_dir / "02_mask.jpg"), mask)

            for idx, item in enumerate(crops):
                crop = item.get("image")
                cand = item.get("candidate") or {}
                source = item.get("source", "unknown")
                area_ratio = cand.get("area_ratio", "na")

                if crop is not None and crop.size > 0:
                    cv2.imwrite(
                        str(run_dir / f"crop_{idx:02d}_{source}_area_{area_ratio}.jpg"),
                        crop,
                    )

            for idx, item in enumerate(final_candidates[:30]):
                cand_img = item.get("image")
                angle = item.get("angle")
                source = item.get("source")

                if cand_img is not None and cand_img.size > 0:
                    cv2.imwrite(
                        str(run_dir / f"candidate_{idx:02d}_{source}_angle_{angle}.jpg"),
                        cand_img,
                    )

            import json

            with (run_dir / "diagnostics.json").open("w", encoding="utf-8") as f:
                json.dump(
                    diagnostics,
                    f,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )

        except Exception:
            # Debug saving must never break the OCR pipeline.
            pass