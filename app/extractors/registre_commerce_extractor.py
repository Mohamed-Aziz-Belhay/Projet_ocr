"""
app/extractors/registre_commerce_extractor.py

Extracteur spécialisé pour les extraits du Registre National des Entreprises
et anciens registres de commerce tunisiens.

Version stabilisée :
- RNE moderne : champs critiques fiables + champs optionnels prudents ;
- registre legacy : fallback sur noms de société latins et numéros D/B/G ;
- support fusion PaddleOCR / EasyOCR champ par champ ;
- ne concatène jamais les textes OCR Paddle + EasyOCR.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from app.extractors.base import BaseExtractor, FieldOutput


# ---------------------------------------------------------------------
# Regex générales
# ---------------------------------------------------------------------

_DATE_YMD_RE = re.compile(
    r"\b(20\d{2}|19\d{2})[\/\-.](\d{1,2})[\/\-.](\d{1,2})\b"
)

_DATE_DMY_RE = re.compile(
    r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](20\d{2}|19\d{2})\b"
)

_IDENTIFIANT_UNIQUE_RE = re.compile(
    r"(?:Identifiant\s+Unique|Identifiant\s+unique|المعرف\s+الموحد|المعرف\s+الوحيد)"
    r"\s*[:\-]?\s*([0-9]{5,12}[A-Z])",
    re.IGNORECASE,
)

_IDENTIFIANT_FALLBACK_RE = re.compile(
    r"\b([0-9]{5,12}[A-Z])\b",
    re.IGNORECASE,
)

_RAISON_SOCIALE_RE = re.compile(
    r"(?:Raison\s+sociale|Raison\s+Sociale|الإسم\s+الإجتماعي|الاسم\s+الإجتماعي|الاسم\s+الاجتماعي)"
    r"\s*[:\-]?\s*"
    r"(.+?)"
    r"(?=\s+(?:Nom\s+Commerc\w*|Adresse\s+Sociale|Capital|Activit[eé]|Forme\s+Juridique|$))",
    re.IGNORECASE | re.DOTALL,
)

_NOM_COMMERCIAL_RE = re.compile(
    r"(?:Nom\s+Commercial|Nom\s+commercial|الإسم\s+التجاري|الاسم\s+التجاري)"
    r"\s*[:\-]\s*"
    r"(.+?)"
    r"(?=\s+(?:Adresse\s+Sociale|Capital|Activit[eé]|Forme\s+Juridique|$))",
    re.IGNORECASE | re.DOTALL,
)

_ADRESSE_RE = re.compile(
    r"(?:Adresse\s+Sociale|Adresse\s+sociale|Si[eè]ge\s+social|المقر\s+الإجتماعي|المقر\s+الاجتماعي)"
    r"\s*[:\-]\s*"
    r"(.+?)"
    r"(?=\s+(?:Capital|رأس\s+المال|Activit[eé]\s+Principale|النشاط\s+الأصلي|Forme\s+Juridique|$))",
    re.IGNORECASE | re.DOTALL,
)

_CAPITAL_RE = re.compile(
    r"(?:Capital|رأس\s+المال)"
    r"\s*[:\-]?\s*"
    r"([0-9][0-9\s.,]{2,30})",
    re.IGNORECASE,
)

_ACTIVITE_PRINCIPALE_RE = re.compile(
    r"(?:Activit[eé]\s+Principale|النشاط\s+الأصلي|النشاط\s+الرئيسي)"
    r"\s*[:\-]\s*"
    r"(.+?)"
    r"(?=\s+(?:Activit[eé]\s+Secondaire|النشاط\s+الثانوي|Forme\s+Juridique|النظام\s+القانوني|Date\s+de\s+Publication|$))",
    re.IGNORECASE | re.DOTALL,
)

_ACTIVITE_SECONDAIRE_RE = re.compile(
    r"(?:Activit[eé]\s+Secondaire|النشاط\s+الثانوي)"
    r"\s*[:\-]\s*"
    r"(.+?)"
    r"(?=\s+(?:Forme\s+Juridique|النظام\s+القانوني|Date\s+de\s+Publication|تاريخ\s+النشر|$))",
    re.IGNORECASE | re.DOTALL,
)

_FORME_JURIDIQUE_RE = re.compile(
    r"(?:Forme\s+Juridique|Forme\s+juridique|النظام\s+القانوني)"
    r"\s*[:\-]\s*"
    r"(.+?)"
    r"(?=\s+(?:Date\s+de\s+Publication|تاريخ\s+النشر|Date\s+de\s+d[eé]but|تاريخ\s+بداية|Informations|$))",
    re.IGNORECASE | re.DOTALL,
)

_FORME_JURIDIQUE_SHORT_RE = re.compile(
    r"\b(SARL|SUARL|SA|S\.A\.|SNC|SCS|GIE)\b",
    re.IGNORECASE,
)

_DATE_PUBLICATION_RE = re.compile(
    r"(?:Date\s+de\s+Pub\s*lication|Date\s+de\s+Publication|تاريخ\s+النشر)"
    r"\s*[:\-]?\s*"
    r"((?:20\d{2}|19\d{2})[\/\-.]\d{1,2}[\/\-.]\d{1,2}|\d{1,2}[\/\-.]\d{1,2}[\/\-.](?:20\d{2}|19\d{2}))",
    re.IGNORECASE,
)

_DATE_DEBUT_ACTIVITE_RE = re.compile(
    r"(?:Date\s+de\s+d[eé]but\s+d['’]activit[eé]|Date\s+de\s+debut\s+d['’]activite|تاريخ\s+بداية\s+النشاط)"
    r"\s*[:\-]?\s*"
    r"((?:20\d{2}|19\d{2})[\/\-.]\d{1,2}[\/\-.]\d{1,2}|\d{1,2}[\/\-.]\d{1,2}[\/\-.](?:20\d{2}|19\d{2}))",
    re.IGNORECASE,
)

_DIRECTION_BLOCK_RE = re.compile(
    r"(?:Informations\s+relatives\s+[aà]\s+la\s+Direction|معلومات\s+تخص\s+الإدارة)"
    r"(.+)$",
    re.IGNORECASE | re.DOTALL,
)

_NATIONALITE_RE = re.compile(
    r"\b(Tunisienne|Tunisien|TUNISIENNE|TUNISIEN|تونسية|تونسي)\b",
    re.IGNORECASE,
)

_QUALITY_RE = re.compile(
    r"\b(G[eé]rant|Directeur|Administrateur|وكيل|وكيله|مدير)\b",
    re.IGNORECASE,
)

_COMPANY_LEGACY_RE = re.compile(
    r"\b([A-Z][A-Z\s&\.]{5,100}(?:CORPORATION|CORPORRATION|COMPANY|CO\.?|CORP\.?|SARL|SUARL|SA))\b",
    re.IGNORECASE,
)

_LEGACY_NUMBER_RE = re.compile(r"\b([DBG][0-9]{6,12})\b", re.IGNORECASE)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

BAD_VALUE_TOKENS = {
    "https",
    "registre-entreprises",
    "page",
    "1/2",
    "2/2",
    "qualite",
    "qualité",
    "nationalite",
    "nationalité",
    "date de naissance",
    "nom & prénom",
    "nom&prénom",
    "date de pub",
    "date de publication",
    "date de debut",
    "date de début",
    "informations relatives",
}


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = _compact_spaces(value)
    value = value.strip(" :;,.|-")

    if not value:
        return None

    value = re.sub(r"\s+https?://.*$", "", value, flags=re.IGNORECASE).strip()
    value = re.split(r"https?://|registre-entreprises|1/2|2/2", value, flags=re.IGNORECASE)[0]
    value = _compact_spaces(value)
    value = value.strip(" :;,.|-")

    return value or None


def _is_noisy_value(value: Optional[str], *, max_len: int = 140) -> bool:
    if value is None:
        return True

    v = _compact_spaces(value)
    if not v:
        return True

    if len(v) > max_len:
        return True

    low = v.lower()
    if any(tok in low for tok in BAD_VALUE_TOKENS):
        return True

    digits = len(re.findall(r"\d", v))
    letters = len(re.findall(r"[A-Za-zÀ-ÿ\u0600-\u06FF]", v))
    if digits > 0 and letters == 0:
        return True

    return False


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    raw = raw.strip()

    m = _DATE_YMD_RE.search(raw)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    m = _DATE_DMY_RE.search(raw)
    if m:
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    return None


def _normalize_capital(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    value = raw.strip()
    value = value.replace(" ", "")
    value = value.replace(",", ".")

    matches = re.findall(r"\d+(?:\.\d+)?", value)
    if not matches:
        return None

    candidate = sorted(matches, key=len, reverse=True)[0]

    try:
        dec = Decimal(candidate)
    except InvalidOperation:
        return None

    if dec <= 0:
        return None

    return f"{dec:.3f}"


def _find_first_date_after_label(text: str, label_pattern: str) -> Tuple[Optional[str], Optional[str]]:
    pattern = re.compile(
        label_pattern
        + r".{0,100}?((?:20\d{2}|19\d{2})[\/\-.]\d{1,2}[\/\-.]\d{1,2}|\d{1,2}[\/\-.]\d{1,2}[\/\-.](?:20\d{2}|19\d{2}))",
        re.IGNORECASE | re.DOTALL,
    )

    m = pattern.search(text or "")
    if not m:
        return None, None

    raw = m.group(1)
    return _normalize_date(raw), raw


def _score_text_value(field_name: str, value: Optional[str]) -> float:
    if not value:
        return -100.0

    v = _compact_spaces(value)
    score = 0.0

    if re.search(r"[\u0600-\u06FF]", v):
        score += 2.0

    if re.search(r"[A-Za-zÀ-ÿ]", v):
        score += 1.0

    if len(v) >= 3:
        score += 1.0

    if len(v) > 80:
        score -= 2.0

    if _is_noisy_value(v):
        score -= 5.0

    if field_name in {"identifiant_unique"} and re.fullmatch(r"[0-9]{5,12}[A-Z]", v):
        score += 5.0

    if field_name in {"date_extrait", "date_publication", "date_debut_activite", "dirigeant_date_naissance"}:
        if _normalize_date(v):
            score += 5.0

    if field_name == "capital":
        if _normalize_capital(v):
            score += 4.0

    if field_name in {"raison_sociale", "nom_commercial"} and "CORPORATION" in v.upper():
        score += 4.0

    return score


def _field_to_dict(field: FieldOutput) -> Dict[str, Any]:
    return {
        "name": getattr(field, "name", None),
        "value": getattr(field, "value", None),
        "confidence": getattr(field, "confidence", 0.0),
        "raw_text": getattr(field, "raw_text", None),
        "validated": getattr(field, "validated", False),
        "error": getattr(field, "error", None),
    }


# ---------------------------------------------------------------------
# Extracteur
# ---------------------------------------------------------------------

class RegistreCommerceExtractor(BaseExtractor):
    doc_family = "registre_commerce"
    variant_id = "registre_commerce_tn"

    CRITICAL_FIELDS = {
        "date_extrait",
        "identifiant_unique",
        "raison_sociale",
    }

    FIELD_ORDER = [
        "date_extrait",
        "identifiant_unique",
        "numero_registre",
        "numero_depot",
        "numero_interne",
        "raison_sociale",
        "nom_commercial",
        "adresse_sociale",
        "capital",
        "activite_principale",
        "activite_secondaire",
        "forme_juridique",
        "date_publication",
        "date_debut_activite",
        "dirigeant_qualite",
        "dirigeant_adresse",
        "dirigeant_nationalite",
        "dirigeant_date_naissance",
        "dirigeant_nom_prenom",
    ]

    FIELD_CONFIDENCE = {
        "date_extrait": 0.90,
        "identifiant_unique": 0.92,
        "numero_registre": 0.78,
        "numero_depot": 0.78,
        "numero_interne": 0.78,
        "raison_sociale": 0.88,
        "nom_commercial": 0.62,
        "adresse_sociale": 0.65,
        "capital": 0.86,
        "activite_principale": 0.62,
        "activite_secondaire": 0.55,
        "forme_juridique": 0.62,
        "date_publication": 0.84,
        "date_debut_activite": 0.84,
        "dirigeant_qualite": 0.60,
        "dirigeant_adresse": 0.50,
        "dirigeant_nationalite": 0.70,
        "dirigeant_date_naissance": 0.72,
        "dirigeant_nom_prenom": 0.55,
    }

    def can_handle(self, doc_family: str, variant_id: Optional[str] = None) -> bool:
        if doc_family in {"registre_commerce", "business_registry"}:
            return True

        if variant_id and "registre" in variant_id.lower():
            return True

        return False

    def _field(
        self,
        name: str,
        value: Optional[Any],
        confidence: float,
        raw_text: Optional[str],
        required: bool = False,
        error: Optional[str] = None,
    ) -> FieldOutput:
        validated = value not in (None, "", [])

        if required and not validated:
            error = error or f"{name}_not_found"

        if not validated and error is None:
            error = "field_not_found"

        return self._make_field(
            name=name,
            value=value,
            confidence=confidence if validated else 0.0,
            raw_text=raw_text,
            validated=validated,
            error=None if validated else error,
        )

    def _is_legacy_registry_format(self, text: str) -> bool:
        """
        Détecte un ancien registre de commerce tunisien.
        """
        t = text or ""

        has_company_like_name = bool(_COMPANY_LEGACY_RE.search(t))
        has_legacy_numbers = bool(_LEGACY_NUMBER_RE.search(t))
        has_modern_rne_markers = bool(
            re.search(
                r"Identifiant\s+Unique|Raison\s+sociale|Date\s+de\s+l['’]extrait",
                t,
                re.IGNORECASE,
            )
        )

        return has_company_like_name and has_legacy_numbers and not has_modern_rne_markers

    def _extract_company_name_legacy(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extrait une raison sociale depuis les anciens registres où le nom société
        apparaît souvent en majuscules latines.
        """
        candidates = _COMPANY_LEGACY_RE.findall(text or "")
        cleaned: List[str] = []

        for raw in candidates:
            value = _clean_value(raw)
            if not value:
                continue

            value = value.upper()
            value = re.sub(r"\s+", " ", value).strip()

            # Corrections OCR fréquentes observées sur l'exemple VOYAGEUR.
            value = value.replace("CAZ", "GAZ")
            value = value.replace("CORPORRATION", "CORPORATION")
            value = value.replace("GAZCORPORATION", "GAZ CORPORATION")
            value = value.replace("CAZCORPORATION", "GAZ CORPORATION")
            value = value.replace("GAZ CORP ORATION", "GAZ CORPORATION")

            if len(value) < 8:
                continue

            if value not in cleaned:
                cleaned.append(value)

        if not cleaned:
            return None, None

        best = sorted(cleaned, key=len, reverse=True)[0]
        return best, best

    def _extract_legacy_numbers(self, text: str) -> Dict[str, Optional[str]]:
        result = {
            "numero_interne": None,
            "numero_depot": None,
            "numero_registre": None,
        }

        values = [v.upper() for v in _LEGACY_NUMBER_RE.findall(text or "")]

        for v in values:
            if v.startswith("G") and result["numero_interne"] is None:
                result["numero_interne"] = v
            elif v.startswith("D") and result["numero_depot"] is None:
                result["numero_depot"] = v
            elif v.startswith("B") and result["numero_registre"] is None:
                result["numero_registre"] = v

        return result

    def _extract_date_extrait(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        value, raw = _find_first_date_after_label(
            text,
            r"(?:Date\s+de\s+l['’]extrait|تاريخ\s+استخراج\s+المضمون)",
        )

        if value:
            return value, raw

        m = _DATE_YMD_RE.search(text or "") or _DATE_DMY_RE.search(text or "")
        if not m:
            return None, None

        raw = m.group(0)
        return _normalize_date(raw), raw

    def _extract_simple_regex(
        self,
        pattern: re.Pattern[str],
        text: str,
        *,
        max_len: int = 140,
        allow_noisy: bool = False,
    ) -> Tuple[Optional[str], Optional[str]]:
        m = pattern.search(text or "")
        if not m:
            return None, None

        raw = m.group(1) if m.lastindex else m.group(0)
        value = _clean_value(raw)

        if not value:
            return None, raw

        if not allow_noisy and _is_noisy_value(value, max_len=max_len):
            return None, raw

        return value, raw

    def _extract_identifiant_unique(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        value, raw = self._extract_simple_regex(
            _IDENTIFIANT_UNIQUE_RE,
            text,
            max_len=30,
            allow_noisy=True,
        )

        if value:
            return value, raw

        m = _IDENTIFIANT_FALLBACK_RE.search(text or "")
        if m:
            return m.group(1), m.group(1)

        return None, None

    def _extract_forme_juridique(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        value, raw = self._extract_simple_regex(
            _FORME_JURIDIQUE_RE,
            text,
            max_len=90,
        )

        if value:
            return value, raw

        m = _FORME_JURIDIQUE_SHORT_RE.search(text or "")
        if m:
            return m.group(1).upper(), m.group(1)

        return None, None

    def _extract_direction(self, text: str) -> Dict[str, Optional[str]]:
        result: Dict[str, Optional[str]] = {
            "dirigeant_qualite": None,
            "dirigeant_adresse": None,
            "dirigeant_nationalite": None,
            "dirigeant_date_naissance": None,
            "dirigeant_nom_prenom": None,
        }

        m = _DIRECTION_BLOCK_RE.search(text or "")
        if not m:
            return result

        block = _compact_spaces(m.group(1))
        block = re.split(r"https?://|registre-entreprises|1/2|2/2", block, flags=re.IGNORECASE)[0]
        block = _compact_spaces(block)

        date_matches = list(_DATE_YMD_RE.finditer(block)) + list(_DATE_DMY_RE.finditer(block))
        date_matches = sorted(date_matches, key=lambda m: m.start())

        date_match = date_matches[-1] if date_matches else None
        if date_match:
            result["dirigeant_date_naissance"] = _normalize_date(date_match.group(0))

        nat_match = _NATIONALITE_RE.search(block)
        if nat_match:
            result["dirigeant_nationalite"] = "Tunisienne"

        q_match = _QUALITY_RE.search(block)
        if q_match:
            q = q_match.group(1)
            if q in {"وكيل", "وكيله", "مدير"}:
                result["dirigeant_qualite"] = q
            else:
                result["dirigeant_qualite"] = q.capitalize()

        if date_match:
            after = block[date_match.end():]
            after = _clean_value(after)

            if after and not _is_noisy_value(after, max_len=80):
                result["dirigeant_nom_prenom"] = after

        return result

    def extract(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[FieldOutput]:
        text = text or ""
        compact = _compact_spaces(text)
        is_legacy = self._is_legacy_registry_format(compact)

        date_extrait, raw_date_extrait = self._extract_date_extrait(compact)
        identifiant_unique, raw_identifiant = self._extract_identifiant_unique(compact)

        raison_sociale, raw_raison = self._extract_simple_regex(
            _RAISON_SOCIALE_RE,
            compact,
            max_len=100,
        )

        if not raison_sociale and is_legacy:
            raison_sociale, raw_raison = self._extract_company_name_legacy(compact)

        legacy_numbers = self._extract_legacy_numbers(compact) if is_legacy else {
            "numero_registre": None,
            "numero_depot": None,
            "numero_interne": None,
        }

        nom_commercial, raw_nom_commercial = self._extract_simple_regex(
            _NOM_COMMERCIAL_RE,
            compact,
            max_len=100,
        )

        adresse_sociale, raw_adresse = self._extract_simple_regex(
            _ADRESSE_RE,
            compact,
            max_len=160,
        )

        capital_raw_value, raw_capital = self._extract_simple_regex(
            _CAPITAL_RE,
            compact,
            max_len=40,
            allow_noisy=True,
        )
        capital = _normalize_capital(capital_raw_value)

        activite_principale, raw_activite_principale = self._extract_simple_regex(
            _ACTIVITE_PRINCIPALE_RE,
            compact,
            max_len=180,
        )

        activite_secondaire, raw_activite_secondaire = self._extract_simple_regex(
            _ACTIVITE_SECONDAIRE_RE,
            compact,
            max_len=180,
        )

        forme_juridique, raw_forme_juridique = self._extract_forme_juridique(compact)

        pub_m = _DATE_PUBLICATION_RE.search(compact)
        date_publication = _normalize_date(pub_m.group(1)) if pub_m else None
        raw_date_publication = pub_m.group(1) if pub_m else None

        debut_m = _DATE_DEBUT_ACTIVITE_RE.search(compact)
        date_debut_activite = _normalize_date(debut_m.group(1)) if debut_m else None
        raw_date_debut_activite = debut_m.group(1) if debut_m else None

        direction = self._extract_direction(compact)

        values = {
            "date_extrait": (date_extrait, raw_date_extrait),
            "identifiant_unique": (identifiant_unique, raw_identifiant),
            "numero_registre": (legacy_numbers.get("numero_registre"), legacy_numbers.get("numero_registre")),
            "numero_depot": (legacy_numbers.get("numero_depot"), legacy_numbers.get("numero_depot")),
            "numero_interne": (legacy_numbers.get("numero_interne"), legacy_numbers.get("numero_interne")),
            "raison_sociale": (raison_sociale, raw_raison),
            "nom_commercial": (nom_commercial, raw_nom_commercial),
            "adresse_sociale": (adresse_sociale, raw_adresse),
            "capital": (capital, raw_capital),
            "activite_principale": (activite_principale, raw_activite_principale),
            "activite_secondaire": (activite_secondaire, raw_activite_secondaire),
            "forme_juridique": (forme_juridique, raw_forme_juridique),
            "date_publication": (date_publication, raw_date_publication),
            "date_debut_activite": (date_debut_activite, raw_date_debut_activite),
            "dirigeant_qualite": (direction.get("dirigeant_qualite"), direction.get("dirigeant_qualite")),
            "dirigeant_adresse": (direction.get("dirigeant_adresse"), direction.get("dirigeant_adresse")),
            "dirigeant_nationalite": (direction.get("dirigeant_nationalite"), direction.get("dirigeant_nationalite")),
            "dirigeant_date_naissance": (direction.get("dirigeant_date_naissance"), direction.get("dirigeant_date_naissance")),
            "dirigeant_nom_prenom": (direction.get("dirigeant_nom_prenom"), direction.get("dirigeant_nom_prenom")),
        }

        fields: List[FieldOutput] = []
        for name in self.FIELD_ORDER:
            value, raw = values.get(name, (None, None))
            required = name in self.CRITICAL_FIELDS
            conf = self.FIELD_CONFIDENCE.get(name, 0.50)

            fields.append(
                self._field(
                    name=name,
                    value=value,
                    confidence=conf,
                    raw_text=raw,
                    required=required,
                )
            )

        return fields


# ---------------------------------------------------------------------
# Fusion PaddleOCR / EasyOCR champ par champ
# ---------------------------------------------------------------------

def merge_registre_fields(
    paddle_fields: List[FieldOutput],
    easy_fields: List[FieldOutput],
) -> List[FieldOutput]:
    """
    Fusionne deux extractions RNE/registre champ par champ.
    Ne concatène jamais paddle_text + easy_text.
    """
    by_name: Dict[str, Dict[str, Optional[FieldOutput]]] = {}

    for f in paddle_fields or []:
        name = getattr(f, "name", None)
        if name:
            by_name.setdefault(name, {})["paddle"] = f

    for f in easy_fields or []:
        name = getattr(f, "name", None)
        if name:
            by_name.setdefault(name, {})["easyocr"] = f

    output: List[FieldOutput] = []
    extractor = RegistreCommerceExtractor()

    for name in extractor.FIELD_ORDER:
        pair = by_name.get(name, {})
        p = pair.get("paddle")
        e = pair.get("easyocr")

        chosen = _choose_best_field(name, p, e)
        if chosen is not None:
            output.append(chosen)
        else:
            output.append(
                extractor._field(
                    name=name,
                    value=None,
                    confidence=0.0,
                    raw_text=None,
                    required=name in extractor.CRITICAL_FIELDS,
                    error="field_not_found",
                )
            )

    return output


def _choose_best_field(
    field_name: str,
    paddle_field: Optional[FieldOutput],
    easy_field: Optional[FieldOutput],
) -> Optional[FieldOutput]:
    if paddle_field is None and easy_field is None:
        return None

    if paddle_field is None:
        return easy_field

    if easy_field is None:
        return paddle_field

    p = _field_to_dict(paddle_field)
    e = _field_to_dict(easy_field)

    p_value = p.get("value")
    e_value = e.get("value")

    p_valid = bool(p.get("validated")) and p_value not in (None, "", [])
    e_valid = bool(e.get("validated")) and e_value not in (None, "", [])

    if p_valid and not e_valid:
        return paddle_field

    if e_valid and not p_valid:
        return easy_field

    if not p_valid and not e_valid:
        return paddle_field

    p_norm = _compact_spaces(str(p_value))
    e_norm = _compact_spaces(str(e_value))

    if p_norm == e_norm:
        try:
            paddle_field.confidence = max(
                float(getattr(paddle_field, "confidence", 0.0) or 0.0),
                float(getattr(easy_field, "confidence", 0.0) or 0.0),
                0.90,
            )
        except Exception:
            pass
        return paddle_field

    p_score = _score_text_value(field_name, p_norm)
    e_score = _score_text_value(field_name, e_norm)

    if field_name in {
        "adresse_sociale",
        "activite_principale",
        "activite_secondaire",
        "forme_juridique",
        "dirigeant_nom_prenom",
        "dirigeant_adresse",
    }:
        if e_score >= p_score:
            return easy_field
        return paddle_field

    if e_score > p_score + 0.5:
        return easy_field

    return paddle_field


"""
app/extractors/registre_commerce_extractor.py

Extracteur spécialisé pour les extraits du Registre National des Entreprises
tunisiens.

Objectif:
- extraire les champs structurés depuis l'OCR pleine page ;
- supporter les libellés français et arabes lorsque possible ;
- éviter les faux positifs ;
- retourner review_required si les champs critiques sont absents.

Champs principaux:
- date_extrait
- identifiant_unique
- raison_sociale
- nom_commercial
- adresse_sociale
- capital
- activite_principale
- activite_secondaire
- forme_juridique
- date_publication
- date_debut_activite
- dirigeant_nom_prenom
- dirigeant_qualite
- dirigeant_adresse
- dirigeant_nationalite
- dirigeant_date_naissance

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from app.extractors.base import BaseExtractor, FieldOutput


_DATE_YMD_RE = re.compile(r"\b(20\d{2}|19\d{2})[\/\-.](\d{1,2})[\/\-.](\d{1,2})\b")
_DATE_DMY_RE = re.compile(r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](20\d{2}|19\d{2})\b")

_IDENTIFIANT_UNIQUE_RE = re.compile(
    r"(?:Identifiant\s+Unique|المعرف\s+الوحيد)\s*[:\-]?\s*([A-Z0-9]{5,20})",
    re.IGNORECASE,
)

_RAISON_SOCIALE_RE = re.compile(
    r"(?:Raison\s+sociale|الإسم\s+الإجتماعي)\s*[:\-]?\s*([A-Z0-9À-ÿ\u0600-\u06FF\s\.\-&']{2,100})",
    re.IGNORECASE,
)

_NOM_COMMERCIAL_RE = re.compile(
    r"(?:Nom\s+Commercial|الإسم\s+التجاري)\s*[:\-]?\s*([A-Z0-9À-ÿ\u0600-\u06FF\s\.\-&']{2,100})",
    re.IGNORECASE,
)

_ADRESSE_RE = re.compile(
    r"(?:Adresse\s+Sociale|المقر\s+الإجتماعي)\s*[:\-]?\s*(.+?)(?=\s+(?:Capital|رأس\s+المال|Activit[eé]\s+Principale|النشاط\s+الأصلي)|$)",
    re.IGNORECASE | re.DOTALL,
)

_CAPITAL_RE = re.compile(
    r"(?:Capital|رأس\s+المال)\s*[:\-]?\s*([0-9][0-9\s.,]{2,30})",
    re.IGNORECASE,
)

_ACTIVITE_PRINCIPALE_RE = re.compile(
    r"(?:Activit[eé]\s+Principale|النشاط\s+الأصلي)\s*[:\-]?\s*(.+?)(?=\s+(?:Activit[eé]\s+Secondaire|النشاط\s+الثانوي|Forme\s+Juridique|النظام\s+القانوني|Date\s+de\s+Publication)|$)",
    re.IGNORECASE | re.DOTALL,
)

_ACTIVITE_SECONDAIRE_RE = re.compile(
    r"(?:Activit[eé]\s+Secondaire|النشاط\s+الثانوي)\s*[:\-]?\s*(.+?)(?=\s+(?:Forme\s+Juridique|النظام\s+القانوني|Date\s+de\s+Publication|تاريخ\s+النشر)|$)",
    re.IGNORECASE | re.DOTALL,
)

_FORME_JURIDIQUE_RE = re.compile(
    r"(?:Forme\s+Juridique|النظام\s+القانوني)\s*[:\-]?\s*(.+?)(?=\s+(?:Date\s+de\s+Publication|تاريخ\s+النشر|Date\s+de\s+d[eé]but|تاريخ\s+بداية)|$)",
    re.IGNORECASE | re.DOTALL,
)

_DATE_PUBLICATION_RE = re.compile(
    r"(?:Date\s+de\s+Publication|تاريخ\s+النشر)\s*[:\-]?\s*((?:20\d{2}|19\d{2})[\/\-.]\d{1,2}[\/\-.]\d{1,2}|\d{1,2}[\/\-.]\d{1,2}[\/\-.](?:20\d{2}|19\d{2}))",
    re.IGNORECASE,
)

_DATE_DEBUT_ACTIVITE_RE = re.compile(
    r"(?:Date\s+de\s+d[eé]but\s+d['’]activit[eé]|تاريخ\s+بداية\s+النشاط)\s*[:\-]?\s*((?:20\d{2}|19\d{2})[\/\-.]\d{1,2}[\/\-.]\d{1,2}|\d{1,2}[\/\-.]\d{1,2}[\/\-.](?:20\d{2}|19\d{2}))",
    re.IGNORECASE,
)

_DIRECTION_BLOCK_RE = re.compile(
    r"(?:Informations\s+relatives\s+[aà]\s+la\s+Direction|معلومات\s+تخص\s+الإدارة)(.+)$",
    re.IGNORECASE | re.DOTALL,
)

_NATIONALITE_RE = re.compile(
    r"\b(Tunisienne|Tunisien|TUNISIENNE|TUNISIEN|تونسية|تونسي)\b",
    re.IGNORECASE,
)

_QUALITY_RE = re.compile(
    r"\b(G[eé]rant|Directeur|Administrateur|وكيل|مدير)\b",
    re.IGNORECASE,
)


def _compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = _compact_spaces(value)
    value = value.strip(" :;,.|-")

    if not value:
        return None

    # Supprime les fins OCR parasites fréquentes.
    value = re.sub(r"\s+https?://.*$", "", value, flags=re.IGNORECASE).strip()

    return value or None


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    raw = raw.strip()

    m = _DATE_YMD_RE.search(raw)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    m = _DATE_DMY_RE.search(raw)
    if m:
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    return None


def _normalize_capital(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    value = raw.strip()
    value = value.replace(" ", "")
    value = value.replace(",", ".")

    # Exemple OCR: 16000,000 ou 16000.000
    matches = re.findall(r"\d+(?:\.\d+)?", value)

    if not matches:
        return None

    candidate = matches[0]

    try:
        dec = Decimal(candidate)
    except InvalidOperation:
        return None

    if dec <= 0:
        return None

    # Garde 3 décimales pour les montants tunisiens.
    return f"{dec:.3f}"


def _find_first_date_after_label(text: str, label_pattern: str) -> Tuple[Optional[str], Optional[str]]:
    pattern = re.compile(label_pattern + r".{0,80}?((?:20\d{2}|19\d{2})[\/\-.]\d{1,2}[\/\-.]\d{1,2}|\d{1,2}[\/\-.]\d{1,2}[\/\-.](?:20\d{2}|19\d{2}))", re.IGNORECASE | re.DOTALL)
    m = pattern.search(text or "")
    if not m:
        return None, None

    raw = m.group(1)
    return _normalize_date(raw), raw


class RegistreCommerceExtractor(BaseExtractor):
    doc_family = "registre_commerce"
    variant_id = "registre_commerce_tn"

    CRITICAL_FIELDS = {
        "date_extrait",
        "identifiant_unique",
        "raison_sociale",
    }

    def can_handle(self, doc_family: str, variant_id: Optional[str] = None) -> bool:
        if doc_family == "registre_commerce":
            return True

        if variant_id and "registre" in variant_id.lower():
            return True

        return False

    def _field(
        self,
        name: str,
        value: Optional[Any],
        confidence: float,
        raw_text: Optional[str],
        required: bool = False,
        error: Optional[str] = None,
    ) -> FieldOutput:
        validated = value not in (None, "", [])

        if required and not validated:
            error = error or f"{name}_not_found"

        if not validated and error is None:
            error = "field_not_found"

        return self._make_field(
            name=name,
            value=value,
            confidence=confidence if validated else 0.0,
            raw_text=raw_text,
            validated=validated,
            error=None if validated else error,
        )

    def _extract_date_extrait(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        value, raw = _find_first_date_after_label(
            text,
            r"(?:Date\s+de\s+l['’]extrait|تاريخ\s+استخراج\s+المضمون)",
        )

        if value:
            return value, raw

        # Fallback: première date du document, souvent en haut ou dans la zone Date extrait.
        m = _DATE_YMD_RE.search(text or "") or _DATE_DMY_RE.search(text or "")
        if not m:
            return None, None

        raw = m.group(0)
        return _normalize_date(raw), raw

    def _extract_simple_regex(
        self,
        pattern: re.Pattern[str],
        text: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        m = pattern.search(text or "")
        if not m:
            return None, None

        raw = m.group(1) if m.lastindex else m.group(0)
        value = _clean_value(raw)

        return value, raw

    def _extract_direction(self, text: str) -> Dict[str, Optional[str]]:
        
        Extraction simple du tableau de direction.

        L'image exemple contient:
        Qualité | Adresse | Nationalité | Date de naissance | Nom & Prénom
        وكيله | ... | تونسية | 1983/10/11 | إيناس بوعطيلة
        
        result: Dict[str, Optional[str]] = {
            "dirigeant_qualite": None,
            "dirigeant_adresse": None,
            "dirigeant_nationalite": None,
            "dirigeant_date_naissance": None,
            "dirigeant_nom_prenom": None,
        }

        m = _DIRECTION_BLOCK_RE.search(text or "")
        if not m:
            return result

        block = _compact_spaces(m.group(1))

        # Date de naissance.
        date_match = _DATE_YMD_RE.search(block) or _DATE_DMY_RE.search(block)
        if date_match:
            result["dirigeant_date_naissance"] = _normalize_date(date_match.group(0))

        # Nationalité.
        nat_match = _NATIONALITE_RE.search(block)
        if nat_match:
            result["dirigeant_nationalite"] = "Tunisienne"

        # Qualité.
        q_match = _QUALITY_RE.search(block)
        if q_match:
            q = q_match.group(1)
            if q in {"وكيل", "مدير"}:
                result["dirigeant_qualite"] = q
            else:
                result["dirigeant_qualite"] = q.capitalize()

        # Tentative nom/prénom arabe ou latin après la date.
        if date_match:
            after = block[date_match.end():]
            after = _clean_value(after)
            if after:
                # On limite pour éviter d'embarquer l'URL de fin.
                after = re.split(r"https?://|page|1/2|2/2", after, flags=re.IGNORECASE)[0]
                after = _clean_value(after)
                if after and 2 <= len(after) <= 80:
                    result["dirigeant_nom_prenom"] = after

        return result

    def extract(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[FieldOutput]:
        text = text or ""
        compact = _compact_spaces(text)

        fields: List[FieldOutput] = []

        date_extrait, raw_date_extrait = self._extract_date_extrait(compact)
        identifiant_unique, raw_identifiant = self._extract_simple_regex(_IDENTIFIANT_UNIQUE_RE, compact)
        raison_sociale, raw_raison = self._extract_simple_regex(_RAISON_SOCIALE_RE, compact)
        nom_commercial, raw_nom_commercial = self._extract_simple_regex(_NOM_COMMERCIAL_RE, compact)
        adresse_sociale, raw_adresse = self._extract_simple_regex(_ADRESSE_RE, compact)

        capital_raw_value, raw_capital = self._extract_simple_regex(_CAPITAL_RE, compact)
        capital = _normalize_capital(capital_raw_value)

        activite_principale, raw_activite_principale = self._extract_simple_regex(_ACTIVITE_PRINCIPALE_RE, compact)
        activite_secondaire, raw_activite_secondaire = self._extract_simple_regex(_ACTIVITE_SECONDAIRE_RE, compact)
        forme_juridique, raw_forme_juridique = self._extract_simple_regex(_FORME_JURIDIQUE_RE, compact)

        pub_m = _DATE_PUBLICATION_RE.search(compact)
        date_publication = _normalize_date(pub_m.group(1)) if pub_m else None
        raw_date_publication = pub_m.group(1) if pub_m else None

        debut_m = _DATE_DEBUT_ACTIVITE_RE.search(compact)
        date_debut_activite = _normalize_date(debut_m.group(1)) if debut_m else None
        raw_date_debut_activite = debut_m.group(1) if debut_m else None

        direction = self._extract_direction(compact)

        fields.append(self._field("date_extrait", date_extrait, 0.86, raw_date_extrait, required=True))
        fields.append(self._field("identifiant_unique", identifiant_unique, 0.88, raw_identifiant, required=True))
        fields.append(self._field("raison_sociale", raison_sociale, 0.84, raw_raison, required=True))
        fields.append(self._field("nom_commercial", nom_commercial, 0.65, raw_nom_commercial, required=False))
        fields.append(self._field("adresse_sociale", adresse_sociale, 0.74, raw_adresse, required=False))
        fields.append(self._field("capital", capital, 0.82, raw_capital, required=False))
        fields.append(self._field("activite_principale", activite_principale, 0.72, raw_activite_principale, required=False))
        fields.append(self._field("activite_secondaire", activite_secondaire, 0.60, raw_activite_secondaire, required=False))
        fields.append(self._field("forme_juridique", forme_juridique, 0.72, raw_forme_juridique, required=False))
        fields.append(self._field("date_publication", date_publication, 0.82, raw_date_publication, required=False))
        fields.append(self._field("date_debut_activite", date_debut_activite, 0.82, raw_date_debut_activite, required=False))

        fields.append(self._field("dirigeant_qualite", direction.get("dirigeant_qualite"), 0.65, direction.get("dirigeant_qualite"), required=False))
        fields.append(self._field("dirigeant_adresse", direction.get("dirigeant_adresse"), 0.55, direction.get("dirigeant_adresse"), required=False))
        fields.append(self._field("dirigeant_nationalite", direction.get("dirigeant_nationalite"), 0.70, direction.get("dirigeant_nationalite"), required=False))
        fields.append(self._field("dirigeant_date_naissance", direction.get("dirigeant_date_naissance"), 0.75, direction.get("dirigeant_date_naissance"), required=False))
        fields.append(self._field("dirigeant_nom_prenom", direction.get("dirigeant_nom_prenom"), 0.60, direction.get("dirigeant_nom_prenom"), required=False))

        return fields"""