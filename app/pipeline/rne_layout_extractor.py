"""
app/pipeline/rne_layout_extractor.py

RNE / Registre de commerce tunisien layout extractor.

Objectif:
- Compléter l'extraction pleine page par une OCR ciblée sur des zones RNE.
- Améliorer les champs optionnels : adresse, activité, forme juridique, direction.
- Ne jamais remplacer les champs critiques déjà fiables sauf si une valeur est clairement meilleure.

Important:
- Ce module est expérimental.
- Il dépend d'une mise en page proche des extraits RNE observés.
- Les champs extraits restent optionnels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


RecognizeFn = Callable[[Any, np.ndarray, List[str]], Tuple[str, float]]
NormalizeFn = Callable[[str], str]


@dataclass(frozen=True)
class RNEZone:
    name: str
    x1: float
    y1: float
    x2: float
    y2: float


# Zones normalisées pour un extrait RNE portrait.
# Elles sont volontairement larges, car les scans peuvent varier.
RNE_ZONES: List[RNEZone] = [
    RNEZone("header", 0.02, 0.00, 0.98, 0.22),

    RNEZone("identity_block", 0.03, 0.22, 0.97, 0.40),
    RNEZone("company_block", 0.03, 0.34, 0.97, 0.55),
    RNEZone("activity_block", 0.03, 0.48, 0.97, 0.72),
    RNEZone("dates_block", 0.03, 0.62, 0.97, 0.82),
    RNEZone("direction_block", 0.03, 0.84, 0.97, 0.97),

    # Zones plus ciblées
    RNEZone("raison_sociale_zone", 0.03, 0.30, 0.97, 0.42),
    RNEZone("adresse_zone", 0.03, 0.36, 0.97, 0.50),
    RNEZone("capital_zone", 0.03, 0.42, 0.97, 0.55),
    RNEZone("activite_zone", 0.03, 0.48, 0.97, 0.66),
    RNEZone("forme_juridique_zone", 0.03, 0.58, 0.97, 0.72),
    RNEZone("direction_table_zone", 0.03, 0.84, 0.97, 0.97),
]


DATE_YMD_RE = re.compile(
    r"\b(20\d{2}|19\d{2})[\/\-.](\d{1,2})[\/\-.](\d{1,2})\b"
)

DATE_DMY_RE = re.compile(
    r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](20\d{2}|19\d{2})\b"
)

BAD_TOKENS = {
    "https",
    "registre-entreprises",
    "historiquequittancercc",
    "page",
    "1/2",
    "2/2",
    "qualite adresse nationalite",
    "nom&prenom",
    "nom & prenom",
}


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _crop_norm(image_bgr: np.ndarray, zone: RNEZone) -> Optional[np.ndarray]:
    if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
        return None

    h, w = image_bgr.shape[:2]

    x1 = int(max(0.0, min(1.0, zone.x1)) * w)
    y1 = int(max(0.0, min(1.0, zone.y1)) * h)
    x2 = int(max(0.0, min(1.0, zone.x2)) * w)
    y2 = int(max(0.0, min(1.0, zone.y2)) * h)

    if x2 <= x1 or y2 <= y1:
        return None

    roi = image_bgr[y1:y2, x1:x2].copy()

    if roi is None or getattr(roi, "size", 0) == 0:
        return None

    return roi


def _clean_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = _compact_spaces(value)
    value = value.strip(" :;,.|-")

    if not value:
        return None

    value = re.split(
        r"https?://|registre-entreprises|HistoriqueQuittance|1/2|2/2",
        value,
        flags=re.IGNORECASE,
    )[0]

    value = _compact_spaces(value)
    value = value.strip(" :;,.|-")

    if not value:
        return None

    return value


def _is_noisy(value: Optional[str], max_len: int = 160) -> bool:
    if value is None:
        return True

    value = _compact_spaces(value)

    if not value:
        return True

    if len(value) > max_len:
        return True

    low = value.lower()

    if any(tok in low for tok in BAD_TOKENS):
        return True

    letters = len(re.findall(r"[A-Za-zÀ-ÿ\u0600-\u06FF]", value))
    digits = len(re.findall(r"\d", value))

    if letters == 0 and digits > 0:
        return True

    return False


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    raw = raw.strip()

    m = DATE_YMD_RE.search(raw)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    m = DATE_DMY_RE.search(raw)
    if m:
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    return None


def _first_match(
    patterns: List[re.Pattern[str]],
    text: str,
    *,
    max_len: int = 160,
    allow_noisy: bool = False,
) -> Tuple[Optional[str], Optional[str]]:
    for pattern in patterns:
        m = pattern.search(text or "")

        if not m:
            continue

        raw = m.group(1) if m.lastindex else m.group(0)
        value = _clean_value(raw)

        if not value:
            return None, raw

        if not allow_noisy and _is_noisy(value, max_len=max_len):
            return None, raw

        return value, raw

    return None, None


def _extract_from_zone_texts(zone_texts: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """
    Extrait des champs optionnels à partir des textes OCR zonés.
    Retour:
      {
        field_name: {
          value,
          raw_text,
          confidence,
          source_zone
        }
      }
    """
    result: Dict[str, Dict[str, Any]] = {}

    all_text = _compact_spaces("\n".join(zone_texts.values()))

    company_text = _compact_spaces(
        zone_texts.get("company_block", "")
        + " "
        + zone_texts.get("raison_sociale_zone", "")
        + " "
        + zone_texts.get("adresse_zone", "")
        + " "
        + zone_texts.get("capital_zone", "")
    )

    activity_text = _compact_spaces(
        zone_texts.get("activity_block", "")
        + " "
        + zone_texts.get("activite_zone", "")
        + " "
        + zone_texts.get("forme_juridique_zone", "")
    )

    direction_text = _compact_spaces(
        zone_texts.get("direction_block", "")
        + " "
        + zone_texts.get("direction_table_zone", "")
    )

    # Nom commercial : uniquement si valeur claire après ":" ou "-".
    value, raw = _first_match(
        [
            re.compile(
                r"(?:Nom\s+Commercial|Nom\s+commercial|Nom\s+Commerc\w*)\s*[:\-]\s*(.+?)(?=\s+(?:Adresse|Capital|Activit|Forme|$))",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?:الاسم\s+التجاري|الإسم\s+التجاري)\s*[:\-]\s*([^\n]{3,100})",
                re.IGNORECASE,
            ),
        ],
        company_text,
        max_len=100,
    )

    if value:
        result["nom_commercial"] = {
            "value": value,
            "raw_text": raw,
            "confidence": 0.58,
            "source_zone": "company_block",
        }

    # Adresse : très prudente, uniquement avec séparateur clair.
    value, raw = _first_match(
        [
            re.compile(
                r"(?:Adresse\s+Sociale|Adresse\s+sociale|Si[eè]ge\s+social)\s*[:\-]\s*(.+?)(?=\s+(?:Capital|Activit[eé]|Forme|Date|$))",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?:المقر\s+الاجتماعي|المقر\s+الإجتماعي|العنوان)\s*[:\-]\s*([^\n]{5,160})",
                re.IGNORECASE,
            ),
        ],
        company_text,
        max_len=160,
    )

    if value:
        result["adresse_sociale"] = {
            "value": value,
            "raw_text": raw,
            "confidence": 0.60,
            "source_zone": "adresse_zone",
        }

    # Activité principale.
    value, raw = _first_match(
        [
            re.compile(
                r"(?:Activit[eé]\s+Principale|Activite\s+Principale)\s*[:\-]\s*(.+?)(?=\s+(?:Activit[eé]\s+Secondaire|Forme\s+Juridique|Date|$))",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?:النشاط\s+الأصلي|النشاط\s+الرئيسي)\s*[:\-]\s*([^\n]{5,180})",
                re.IGNORECASE,
            ),
        ],
        activity_text,
        max_len=180,
    )

    if value:
        result["activite_principale"] = {
            "value": value,
            "raw_text": raw,
            "confidence": 0.58,
            "source_zone": "activite_zone",
        }

    # Activité secondaire.
    value, raw = _first_match(
        [
            re.compile(
                r"(?:Activit[eé]\s+Secondaire|Activite\s+Secondaire)\s*[:\-]\s*(.+?)(?=\s+(?:Forme\s+Juridique|Date|$))",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?:النشاط\s+الثانوي)\s*[:\-]\s*([^\n]{5,180})",
                re.IGNORECASE,
            ),
        ],
        activity_text,
        max_len=180,
    )

    if value:
        result["activite_secondaire"] = {
            "value": value,
            "raw_text": raw,
            "confidence": 0.52,
            "source_zone": "activite_zone",
        }

    # Forme juridique.
    value, raw = _first_match(
        [
            re.compile(
                r"(?:Forme\s+Juridique|Forme\s+juridique)\s*[:\-]\s*(.+?)(?=\s+(?:Date|Informations|$))",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\b(SARL|SUARL|SA|S\.A\.|SNC|SCS|GIE)\b",
                re.IGNORECASE,
            ),
            re.compile(
                r"(?:النظام\s+القانوني)\s*[:\-]\s*([^\n]{3,100})",
                re.IGNORECASE,
            ),
        ],
        activity_text,
        max_len=100,
    )

    if value:
        result["forme_juridique"] = {
            "value": value,
            "raw_text": raw,
            "confidence": 0.58,
            "source_zone": "forme_juridique_zone",
        }

    # Direction : date de naissance.
    date_matches = list(DATE_YMD_RE.finditer(direction_text)) + list(DATE_DMY_RE.finditer(direction_text))
    date_matches = sorted(date_matches, key=lambda m: m.start())

    # Dans le bloc direction, la date de début d'activité apparaît souvent avant le tableau.
    # La date de naissance du dirigeant est généralement la dernière date du bloc.
    if date_matches:
        m = date_matches[-1]
        date_value = _normalize_date(m.group(0))

        if date_value:
            result["dirigeant_date_naissance"] = {
                "value": date_value,
                "raw_text": m.group(0),
                "confidence": 0.72,
                "source_zone": "direction_table_zone",
            }
    # Direction : nationalité.
    m = re.search(
        r"\b(Tunisienne|Tunisien|TUNISIENNE|TUNISIEN|تونسية|تونسي)\b",
        direction_text,
        re.IGNORECASE,
    )

    if m:
        result["dirigeant_nationalite"] = {
            "value": "Tunisienne",
            "raw_text": m.group(0),
            "confidence": 0.68,
            "source_zone": "direction_table_zone",
        }

    # Direction : qualité.
    m = re.search(
        r"\b(G[eé]rant|Directeur|Administrateur|وكيل|وكيله|مدير)\b",
        direction_text,
        re.IGNORECASE,
    )

    if m:
        value = m.group(1)
        result["dirigeant_qualite"] = {
            "value": value,
            "raw_text": value,
            "confidence": 0.58,
            "source_zone": "direction_table_zone",
        }

    # Direction : nom prénom.
    # Très prudent : seulement après une date de naissance, et pas trop long.
    if "dirigeant_date_naissance" in result:
        date_raw = result["dirigeant_date_naissance"]["raw_text"]
        idx = direction_text.find(date_raw)

        if idx >= 0:
            after = direction_text[idx + len(date_raw):]
            after = _clean_value(after)

            if after and not _is_noisy(after, max_len=80):
                result["dirigeant_nom_prenom"] = {
                    "value": after,
                    "raw_text": after,
                    "confidence": 0.48,
                    "source_zone": "direction_table_zone",
                }

    return result


def run_rne_layout_ocr(
    *,
    image_bgr: np.ndarray,
    engine: Any,
    engine_name: str,
    language_hints: List[str],
    recognize_fn: RecognizeFn,
    normalize_fn: NormalizeFn,
) -> Dict[str, Any]:
    """
    Exécute une OCR par zones sur un extrait RNE.

    Retourne:
      {
        "executed": true,
        "engine": "...",
        "zones": {...},
        "fields": {...},
        "zone_count": int,
        "non_empty_zone_count": int
      }
    """
    if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
        return {
            "executed": False,
            "engine": engine_name,
            "reason": "empty_image",
            "zones": {},
            "fields": {},
        }

    zone_texts: Dict[str, str] = {}
    zone_scores: Dict[str, float] = {}

    for zone in RNE_ZONES:
        roi = _crop_norm(image_bgr, zone)

        if roi is None or getattr(roi, "size", 0) == 0:
            zone_texts[zone.name] = ""
            zone_scores[zone.name] = 0.0
            continue

        try:
            text, score = recognize_fn(
                engine,
                roi,
                language_hints,
            )
            text = normalize_fn(text)
            text = _compact_spaces(text)

            zone_texts[zone.name] = text
            zone_scores[zone.name] = float(score or 0.0)

        except Exception as exc:
            zone_texts[zone.name] = ""
            zone_scores[zone.name] = 0.0

    fields = _extract_from_zone_texts(zone_texts)

    return {
        "executed": True,
        "engine": engine_name,
        "zone_count": len(RNE_ZONES),
        "non_empty_zone_count": sum(1 for v in zone_texts.values() if v.strip()),
        "zones": zone_texts,
        "zone_scores": zone_scores,
        "fields": fields,
        "policy": "rne_zone_ocr_optional_fields_only",
    }