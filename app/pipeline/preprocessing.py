"""
app/pipeline/preprocessing.py

Prétraitement d'image pour améliorer la qualité OCR sur les documents
dégradés (scans mal éclairés, photos penchées, bruit numérique).

Principe : chaque étape est appliquée UNIQUEMENT si une estimation de
qualité indique qu'elle est nécessaire, afin de ne pas ralentir ni
dégrader inutilement les documents déjà propres (photocopies nettes,
scans haute résolution).

Étapes disponibles :
- estimate_blur_score()      : détection de flou (variance du Laplacien)
- deskew()                   : redressement de l'inclinaison du document
- denoise()                  : réduction du bruit numérique
- enhance_contrast_clahe()   : égalisation adaptative du contraste local
- preprocess_for_ocr()       : orchestre les trois étapes, avec métadonnées
                                de diagnostic (angle corrigé, score de flou,
                                étapes réellement appliquées) utiles pour
                                le logging et l'analyse de qualité.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import cv2
import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

# Seuils empiriques, à ajuster selon les retours terrain.
BLUR_THRESHOLD = 100.0          # variance du Laplacien en-dessous de laquelle le document est jugé flou
MAX_DESKEW_ANGLE = 15.0         # au-delà, on suppose une erreur de détection plutôt qu'une vraie inclinaison
MIN_DESKEW_ANGLE = 0.3          # en-dessous, la correction n'apporte rien de perceptible


@dataclass
class PreprocessingReport:
    """Métadonnées de diagnostic, utiles pour le logging et l'analyse Ch.5."""
    blur_score: float = 0.0
    was_blurry: bool = False
    deskew_angle_deg: float = 0.0
    was_deskewed: bool = False
    was_denoised: bool = False
    was_contrast_enhanced: bool = False
    steps_applied: List[str] = field(default_factory=list)


def estimate_blur_score(image: np.ndarray) -> float:
    """
    Variance du Laplacien : plus la valeur est basse, plus l'image est floue.
    Métrique standard, rapide (une convolution), sans dépendance lourde.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def deskew(image: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Détecte et corrige l'inclinaison du document via minAreaRect sur le
    masque des pixels de texte (après binarisation Otsu inversée).
    Retourne (image_corrigee, angle_detecte_en_degres).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)

    coords = np.column_stack(np.where(thresh > 0))
    if coords.shape[0] < 50:
        # Pas assez de pixels de texte détectés pour une estimation fiable.
        return image, 0.0

    angle = cv2.minAreaRect(coords)[-1]

    # cv2.minAreaRect retourne un angle dans [-90, 0) selon l'orientation
    # du rectangle englobant ; on le ramène à une correction dans [-45, 45].
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < MIN_DESKEW_ANGLE or abs(angle) > MAX_DESKEW_ANGLE:
        # Inclinaison négligeable, ou probable faux positif de détection.
        return image, angle

    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        image, matrix, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,  # évite les coins noirs qui gêneraient l'OCR
    )
    return rotated, angle


def denoise(image: np.ndarray, strength: int = 7) -> np.ndarray:
    """
    Réduction du bruit numérique (scans basse résolution, photos smartphone
    en faible luminosité). fastNlMeansDenoisingColored donne un bon
    résultat qualité/coût pour des documents texte.
    """
    return cv2.fastNlMeansDenoisingColored(
        image, None,
        h=strength, hColor=strength,
        templateWindowSize=7, searchWindowSize=21,
    )


def enhance_contrast_clahe(image: np.ndarray, clip_limit: float = 2.0) -> np.ndarray:
    """
    Égalisation adaptative du contraste (CLAHE) sur le canal de luminance
    (espace LAB), pour corriger un éclairage inégal ou une ombre portée
    sans sur-exposer les zones déjà bien éclairées.
    """
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)

    merged = cv2.merge((l_enhanced, a_channel, b_channel))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def preprocess_for_ocr(
    image: np.ndarray,
    *,
    enable_deskew: bool = True,
    enable_denoise: bool = True,
    enable_contrast: bool = True,
    force_all_steps: bool = False,
) -> tuple[np.ndarray, PreprocessingReport]:
    """
    Orchestre le prétraitement complet, en n'appliquant le débruitage et
    le contraste QUE si l'estimation de qualité les juge nécessaires
    (sauf si force_all_steps=True, utile pour des tests comparatifs).

    Le redressement (deskew) est peu coûteux et sans risque de dégradation
    visuelle notable : il reste appliqué par défaut dès qu'une inclinaison
    significative est détectée, indépendamment du score de flou.

    Retourne (image_traitee, rapport_diagnostic).
    """
    report = PreprocessingReport()
    result = image

    report.blur_score = estimate_blur_score(result)
    report.was_blurry = report.blur_score < BLUR_THRESHOLD

    if enable_deskew:
        result, angle = deskew(result)
        report.deskew_angle_deg = angle
        if abs(angle) >= MIN_DESKEW_ANGLE and abs(angle) <= MAX_DESKEW_ANGLE:
            report.was_deskewed = True
            report.steps_applied.append(f"deskew({angle:.1f}°)")

    if enable_denoise and (force_all_steps or report.was_blurry):
        result = denoise(result)
        report.was_denoised = True
        report.steps_applied.append("denoise")

    if enable_contrast and (force_all_steps or report.was_blurry):
        # Le flou n'est qu'un proxy imparfait pour "mauvaise qualité
        # générale" ; en pratique les documents mal éclairés ont souvent
        # aussi un score de flou dégradé (contours moins nets), donc ce
        # même déclencheur reste un choix pragmatique raisonnable ici.
        result = enhance_contrast_clahe(result)
        report.was_contrast_enhanced = True
        report.steps_applied.append("clahe")

    log.info(
        "Preprocessing report",
        extra={
            "blur_score": round(report.blur_score, 1),
            "was_blurry": report.was_blurry,
            "deskew_angle_deg": round(report.deskew_angle_deg, 2),
            "steps_applied": report.steps_applied,
        },
    )

    return result, report