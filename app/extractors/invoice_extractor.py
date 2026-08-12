"""
app/extractors/invoice_extractor.py

Extracteur specialise pour les factures tunisiennes.

Objectifs:
- Facture electronique TTN
- Facture commerciale tunisienne standard
- Facture simple de service
- Extraction des champs de base
- Extraction de la reference unique TTN
- Extraction tolerante mais controlee du tableau
- Verification de coherence des montants

Important:
- Les noms internes restent en anglais pour ne pas casser le backend.
- Les labels francais sont ajoutes cote runner/API/UI.

Correctifs additionnels (factures de transitaire international, ex.
SEKO BANSARD, SAVINO DEL BENE) :
- _TOTAL_TTC_PATTERNS : ajout de motifs "BALANCE DUE", "TOTAL TND" et
  "TOTAL A V/DEBIT", absents des factures tunisiennes domestiques mais
  utilises par les transitaires internationaux.
- _extract_invoice_date : ajout du format "26-Jan-24" (jour, mois
  abrege en anglais, annee sur 2 chiffres), en complement du format
  numerique jj/mm/aaaa deja gere. Les deux motifs sont essayes en
  complement l'un de l'autre, sans rien retirer du comportement deja
  valide sur les factures tunisiennes.
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger
from app.extractors.base import BaseExtractor, FieldOutput
from app.utils.text_normalization import normalize_number

log = get_logger(__name__)


# -----------------------------------------------------------------------------
# Normalisation texte
# -----------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    text = text or ""
    text = text.replace("\u00a0", " ")
    text = text.replace("|", " ")
    text = text.replace("’", "'")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _strip_targeted_ocr_section(text: str) -> str:
    """
    Protection importante.

    L'OCR ciblee peut produire du bruit utile pour le numero/date, mais elle ne
    doit jamais contaminer l'extraction des montants. On coupe donc tout ce qui
    vient apres [INVOICE_TARGETED_OCR] pour les fonctions montant.
    """
    return (text or "").split("[INVOICE_TARGETED_OCR", 1)[0]


def _normalize_amount(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = str(value).strip()
    value = value.replace(" ", "")

    # Correctif : distinguer les deux conventions numeriques.
    # - "1,087.840" (transitaires internationaux) : virgule = separateur
    #   de milliers, point = decimale -> on retire seulement la virgule.
    # - "76,500" (factures tunisiennes) : virgule = decimale -> on la
    #   remplace par un point, comme avant.
    if "," in value and "." in value:
        value = value.replace(",", "")
    else:
        value = value.replace(",", ".")

    try:
        return normalize_number(value)
    except Exception:
        return value


def _amount_to_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None:
        return None

    try:
        cleaned = str(value).replace(" ", "").replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


# Mois anglais abreges -> numero de mois (correctif factures internationales).
_MONTH_NAME_TO_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MONTH_NAME_DATE_RE = re.compile(
    r"\b(\d{1,2})[-\s]+([A-Za-z]{3,9})[-\s]+(\d{2,4})\b",
)


def _parse_month_name_date(raw: str) -> Optional[str]:
    """
    Parse un format "26-Jan-24" ou "26 Jan 2024" (jour, mois abrege
    anglais, annee sur 2 ou 4 chiffres) -- correctif pour les factures
    de transitaire international (ex. SEKO BANSARD), absentes du
    format numerique jj/mm/aaaa habituel des factures tunisiennes.
    """
    if not raw:
        return None

    m = _MONTH_NAME_DATE_RE.search(raw)

    if not m:
        return None

    day_str, month_str, year_str = m.groups()
    month_num = _MONTH_NAME_TO_NUM.get(month_str.strip().lower()[:4].rstrip("."))

    if month_num is None:
        month_num = _MONTH_NAME_TO_NUM.get(month_str.strip().lower()[:3])

    if month_num is None:
        return None

    try:
        day = int(day_str)
        year = int(year_str)

        if len(year_str) == 2:
            year = 2000 + year if year < 50 else 1900 + year

        if not (1 <= day <= 31 and 1900 <= year <= 2100):
            return None

        from datetime import datetime

        dt = datetime(year, month_num, day)
        return dt.strftime("%Y-%m-%d")

    except (ValueError, Exception):
        return None


def _parse_date_to_iso(raw: Optional[str], prefer_mdy: bool = False) -> Optional[str]:
    if not raw:
        return None

    raw = str(raw).strip()
    parts = re.split(r"[/\-\.]", raw)

    if len(parts) != 3:
        return raw

    a, b, y = parts

    try:
        first = int(a)
        second = int(b)

        if len(y) == 2:
            year_2 = int(y)
            year = 2000 + year_2 if year_2 < 50 else 1900 + year_2
        else:
            year = int(y)

        # Par defaut Tunisie = jj/mm/aaaa. Si b > 12, alors c'est mm/jj/aaaa.
        use_mdy = prefer_mdy or second > 12

        if use_mdy:
            month = first
            day = second
        else:
            day = first
            month = second

        if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100):
            return raw

        return f"{year:04d}-{month:02d}-{day:02d}"

    except Exception:
        return raw


def _first_match(patterns: List[re.Pattern], text: str) -> Tuple[Optional[str], Optional[str]]:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue

        value = match.group(1).strip() if match.lastindex else match.group(0).strip()
        raw = match.group(0).strip()
        return value, raw

    return None, None


def _first_valid_match(patterns: List[re.Pattern], text: str, validator) -> Tuple[Optional[str], Optional[str]]:
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue

        value = match.group(1).strip() if match.lastindex else match.group(0).strip()
        raw = match.group(0).strip()

        if validator(value):
            return value, raw

    return None, None


def _normalize_currency(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    raw = raw.upper().strip()

    if raw in {"DT", "TND", "DINAR", "DINARS", "MILLIME", "MILLIMES"}:
        return "TND"

    if raw == "€":
        return "EUR"

    if raw == "$":
        return "USD"

    if raw == "£":
        return "GBP"

    return raw


def _valid_invoice_number(value: Optional[str]) -> bool:
    if not value:
        return False

    value = value.strip()

    bad_values = {
        "facture",
        "ture",
        "lectronique",
        "electronique",
        "électronique",
        "date",
        "client",
        "total",
        "copie",
        "reference",
        "référence",
    }

    if value.lower() in bad_values:
        return False

    # Un vrai numero doit contenir au moins un chiffre.
    if not re.search(r"\d", value):
        return False

    if len(value) > 40:
        return False

    return True


def _is_label_like_customer(value: Optional[str]) -> bool:
    if not value:
        return True

    low = value.lower().strip()

    bad_phrases = {
        "mode de connexion",
        "rang du compte",
        "profil",
        "code client",
        "matricule fiscal",
        "date limite",
        "tunis",
        "copie de la facture",
    }

    return any(p in low for p in bad_phrases)


# -----------------------------------------------------------------------------
# Patterns generaux
# -----------------------------------------------------------------------------

_INVOICE_NUMBER_PATTERNS = [
    re.compile(
        r"(?:Facture\s*N[°o]?\s*[:#\-]?\s*)"
        r"([A-Z0-9][A-Z0-9\-\/]{0,30})",
        re.I,
    ),
    re.compile(
        r"(?:Facture\s+num[eé]ro\s*[:#\-]?\s*)"
        r"([A-Z0-9][A-Z0-9\-\/]{0,30})",
        re.I,
    ),
    re.compile(
        r"(?:Invoice\s*(?:No|N°|#)?\s*[:#\-]?\s*)"
        r"([A-Z0-9][A-Z0-9\-\/]{0,30})",
        re.I,
    ),
    re.compile(r"\bFacture\s*[:#\-]?\s*([0-9][A-Z0-9\-\/]{0,30})", re.I),
    re.compile(r"\b(?:FAC|INV)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9\-\/]{1,30})", re.I),
    # Repli fenetre (dernier recours) : sur certaines factures de
    # transitaire international (ex. SAVINO DEL BENE), l'OCR lineraise
    # un tableau en regroupant d'abord tous les en-tetes de colonnes
    # ("Facture: Notre ref. Date"), puis toutes les valeurs plus loin
    # ("502515 T40005 23/01/2024") -- le numero n'est alors plus
    # immediatement apres le libelle "Facture". On tolere jusqu'a 100
    # caracteres intermediaires (ex. la raison sociale du fournisseur),
    # essaye uniquement si tous les motifs stricts ci-dessus ont echoue.
    re.compile(r"\bFacture\s*[:#\-]?\s*.{0,100}?(\d{4,10})\b", re.I),
]

_REFERENCE_UNIQUE_PATTERNS = [
    re.compile(r"R[eéè]f[eéè]rence\s+Unique\s*[:\-]?\s*([0-9]{8,50})", re.I),
    re.compile(r"Reference\s+Unique\s*[:\-]?\s*([0-9]{8,50})", re.I),
    re.compile(r"sous\s+la\s+r[eéè]f[eéè]rence\s*[:\-]?\s*([0-9]{8,50})", re.I),
    re.compile(r"(?:r[eéè]f[eéè]rence|reference|refirence|larirence|l[aà]rirence)\s*[:\-]?\s*([0-9]{8,50})", re.I),
    re.compile(r"\b([0-9]{18,50})\b"),
]

_SUPPLIER_PATTERNS = [
    re.compile(r"\bT\.?\s*T\.?\s*N\b", re.I),
    re.compile(r"\bTunisie\s+TradeNet\b", re.I),
    re.compile(r"\bTUNISIE\s+TRADENET\b", re.I),
    re.compile(
        r"\b((?:Ste|Sté|SARL|SA|SUARL|Société|Entreprise|Ets)\s+[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\s\.\-&]{1,80})\b",
        re.I,
    ),
]

_CUSTOMER_PATTERNS = [
    re.compile(r"Nom\s*Compte\s*:\s*([A-Za-zÀ-ÿ0-9\s,\.\-]{2,70})", re.I),
    re.compile(r"\bClient\s*:\s*([A-Za-zÀ-ÿ0-9\s,\.\-]{2,70})", re.I),
    re.compile(r"\bNom\s+client\s*:\s*([A-Za-zÀ-ÿ0-9\s,\.\-]{2,70})", re.I),
]

_SUPPLIER_TAX_PATTERNS = [
    re.compile(r"(?:Matricule\s*Fiscal(?:e)?|MF|Identifiant\s+fiscal)\s*[:\-]?\s*([A-Z0-9\/\-]{5,30})", re.I),
]

_CUSTOMER_TAX_PATTERNS = [
    re.compile(r"(?:Matricule\s*Fiscal(?:e)?\s+client|MF\s+client)\s*[:\-]?\s*([A-Z0-9\/\-]{5,30})", re.I),
]

_PERIOD_RANGE_RE = re.compile(
    r"P[eé]riode\s*du\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\s+Au\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
    re.I,
)

_PERIOD_START_PATTERNS = [
    re.compile(r"P[eé]riode\s*[:\-]?\s*du\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", re.I),
    re.compile(r"P[eé]riode\s*du\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", re.I),
]

_PERIOD_END_PATTERNS = [
    re.compile(r"\bAu\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", re.I),
    re.compile(r"P[eé]riode.*?\bAu\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", re.I | re.S),
]

_PAYMENT_DUE_PATTERNS = [
    re.compile(r"Date\s+Limite\s+de\s+paiement\s*[:\-]?\s*([0-9]{6,8}|\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", re.I),
    re.compile(r"Date\s+d['’]?[eé]ch[eé]ance\s*[:\-]?\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", re.I),
]

_CURRENCY_RE = re.compile(r"\b(DT|TND|EUR|USD|MAD|GBP)\b|\b([€$£])\b|\b(DINARS?|MILLIMES?)\b", re.I)

_AMOUNT_VALUE = r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,3})?)"

_TOTAL_HT_PATTERNS = [
    re.compile(r"Total\s*H\.?\s*T\.?\s*V\.?\s*A\.?\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Total\s*H\.?\s*T\.?\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Montant\s*H\.?\s*T\.?\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Total\s*HTVA\s*" + _AMOUNT_VALUE, re.I),
]

_VAT_AMOUNT_PATTERNS = [
    re.compile(r"Montant\s+TVA\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"\bTVA\s+Montant\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Montant\s+T\.?\s*V\.?\s*A\.?\s*" + _AMOUNT_VALUE, re.I),
]

_VAT_RATE_PATTERNS = [
    re.compile(
        r"Taux\s*\(?%?\)?\s+Base\s+Montant\s+TVA\s+Total\s+H\.?\s*T\.?\s*V\.?\s*A\.?\s+"
        r"[0-9]+(?:[.,][0-9]{1,3})?\s+([0-9]{1,2}(?:[.,][0-9])?)\s+",
        re.I,
    ),
    re.compile(r"Taux.*?Base.*?Montant\s+TVA.*?[0-9]+(?:[.,][0-9]{1,3})?\s+([0-9]{1,2}(?:[.,][0-9])?)\s+", re.I | re.S),
    re.compile(r"\bT\.?\s*V\.?\s*A\.?\s+([0-9]{1,2}(?:[.,][0-9])?)\s+PUH", re.I),
    re.compile(r"\bTax\s*%\s*([0-9]{1,2}(?:[.,][0-9])?)\b", re.I),
    re.compile(r"\b([0-9]{1,2}(?:[.,][0-9])?)\s*%\s*(?:TVA|T\.?\s*V\.?\s*A\.?)", re.I),
    re.compile(r"Taux\s*\(?%?\)?\s*[:\-]?\s*([0-9]{1,2}(?:[.,][0-9])?)", re.I),
]

_STAMP_PATTERNS = [
    re.compile(r"Droit\s+de\s+Timbre\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"\bTimbre\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Timbre\s+fiscal\s*" + _AMOUNT_VALUE, re.I),
]

_TOTAL_TTC_PATTERNS = [
    re.compile(r"Montant\s*T\.?\s*T\.?\s*C\.?\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Total\s*T\.?\s*T\.?\s*C\.?\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Net\s*[àa]\s*payer\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Montant\s+(?:total|net|TTC)\s*[:\-]?\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"Total\s+facture\s*[:\-]?\s*" + _AMOUNT_VALUE, re.I),
    # Correctifs factures de transitaire international (SEKO BANSARD,
    # SAVINO DEL BENE) : vocabulaire absent des factures tunisiennes
    # domestiques, ajoutes en complement, sans rien retirer ci-dessus.
    # Un code devise (TND, USD...) s'intercale souvent entre le libelle
    # et le montant -- pris en compte explicitement.
    re.compile(r"BALANCE\s+DUE\s*(?:[A-Z]{2,4}\s*)?" + _AMOUNT_VALUE, re.I),
    re.compile(r"TOTAL\s+TND\s*" + _AMOUNT_VALUE, re.I),
    re.compile(r"TOTAL\s+A\s*V\/?\s*D[EÉ]BIT\s*(?:[A-Z]{2,4}\s*)?" + _AMOUNT_VALUE, re.I),
]


# -----------------------------------------------------------------------------
# Detection / extraction helpers
# -----------------------------------------------------------------------------

def _detect_invoice_profile(text: str) -> str:
    low = text.lower()

    if (
        "reference unique" in low
        or "référence unique" in low
        or "tunisie tradenet" in low
        or "t.t.n" in low
        or "ttn" in low
        or "larirence" in low
    ):
        return "ttn_electronic"

    if (
        "matricule fiscal" in low
        or "identifiant fiscal" in low
        or "tva" in low
        or "t.t.c" in low
        or "total ttc" in low
    ):
        return "standard"

    if "facture" in low and ("client" in low or "total" in low):
        return "simple"

    return "unknown"


def _extract_supplier(text: str) -> Tuple[Optional[str], Optional[str]]:
    for pattern in _SUPPLIER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue

        raw = match.group(0).strip()
        raw_upper = raw.upper().replace(" ", "")

        if "T.T.N" in raw.upper() or "TTN" in raw_upper or "TRADENET" in raw.upper():
            return "Tunisie TradeNet", raw

        if match.lastindex:
            return match.group(1).strip(), raw

        return raw, raw

    return None, None


def _extract_customer(text: str) -> Tuple[Optional[str], Optional[str]]:
    value, raw = _first_match(_CUSTOMER_PATTERNS, text)

    if _is_label_like_customer(value):
        return None, None

    if value:
        value = re.split(
            r"\b(?:Mode\s+de\s+connexion|Rang\s+du\s+compte|Profil|Code\s+Client|Matricule\s+Fiscal|Date\s+Limite)\b",
            value,
            flags=re.I,
        )[0].strip(" :-")

    if _is_label_like_customer(value):
        return None, None

    return value, raw


def _extract_reference_unique(text: str) -> Tuple[Optional[str], Optional[str]]:
    return _first_match(_REFERENCE_UNIQUE_PATTERNS, text)


def _extract_invoice_date(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrait uniquement une date clairement associee au champ Date de facture.

    On evite de prendre la premiere date trouvee, car les factures TTN contiennent
    souvent une date limite de paiement.

    Correctif : essaie d'abord le format numerique habituel (jj/mm/aaaa),
    puis, s'il echoue, un format "INVOICE DATE 26-Jan-24" (jour, mois
    abrege anglais, annee) rencontre sur les factures de transitaire
    international.
    """
    date_pattern = re.compile(r"\bDate\s*[:\-]?\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})", re.I)

    for match in date_pattern.finditer(text):
        before = text[max(0, match.start() - 35): match.start()].lower()
        after = text[match.start(): match.start() + 55].lower()

        if "limite" in before or "limite" in after or "paiement" in after:
            continue

        return _parse_date_to_iso(match.group(1)), match.group(0).strip()

    # Repli : format "INVOICE DATE 26-Jan-24" / "Date 26 Jan 2024".
    month_date_pattern = re.compile(
        r"\bDate\s*[:\-]?\s*(\d{1,2}[-\s]+[A-Za-z]{3,9}[-\s]+\d{2,4})", re.I
    )

    for match in month_date_pattern.finditer(text):
        before = text[max(0, match.start() - 35): match.start()].lower()
        after = text[match.start(): match.start() + 55].lower()

        if "limite" in before or "limite" in after or "paiement" in after or "due" in before:
            continue

        parsed = _parse_month_name_date(match.group(1))

        if parsed:
            return parsed, match.group(0).strip()

    # Dernier recours : meme probleme de lineraisation de tableau que
    # pour invoice_number (cf. commentaire ci-dessus) -- le libelle
    # "Date" et sa valeur peuvent etre separes par d'autres en-tetes de
    # colonnes. Tolere jusqu'a 100 caracteres intermediaires, essaye
    # uniquement si les motifs stricts precedents ont tous echoue.
    window_date_pattern = re.compile(
        r"\bDate\s*[:\-]?\s*.{0,100}?(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\b", re.I
    )

    for match in window_date_pattern.finditer(text):
        before = text[max(0, match.start() - 35): match.start()].lower()
        after = text[match.start(): match.start() + 100].lower()

        if "limite" in before or "limite" in after or "paiement" in after or "due" in before:
            continue

        return _parse_date_to_iso(match.group(1)), match.group(0).strip()

    return None, None


def _normalize_due_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    raw = raw.strip()

    if re.fullmatch(r"\d{6}", raw):
        d = raw[0:2]
        m = raw[2:4]
        y = "20" + raw[4:6]
        return _parse_date_to_iso(f"{d}/{m}/{y}")

    if re.fullmatch(r"\d{8}", raw):
        d = raw[0:2]
        m = raw[2:4]
        y = raw[4:8]
        return _parse_date_to_iso(f"{d}/{m}/{y}")

    return _parse_date_to_iso(raw)


def _extract_period_range(text: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    m = _PERIOD_RANGE_RE.search(text)

    if m:
        start_raw = m.group(1)
        end_raw = m.group(2)

        end_parts = re.split(r"[/\-\.]", end_raw)
        prefer_mdy = False

        if len(end_parts) >= 2:
            try:
                prefer_mdy = int(end_parts[1]) > 12
            except Exception:
                prefer_mdy = False

        return (
            _parse_date_to_iso(start_raw, prefer_mdy=prefer_mdy),
            _parse_date_to_iso(end_raw, prefer_mdy=prefer_mdy),
            m.group(0),
            m.group(0),
        )

    start_raw_value, start_raw = _first_match(_PERIOD_START_PATTERNS, text)
    end_raw_value, end_raw = _first_match(_PERIOD_END_PATTERNS, text)

    return (
        _parse_date_to_iso(start_raw_value),
        _parse_date_to_iso(end_raw_value),
        start_raw,
        end_raw,
    )


def _extract_vat_rate(text: str) -> Tuple[Optional[str], Optional[str]]:
    text = _strip_targeted_ocr_section(text)

    for pattern in _VAT_RATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue

        value = match.group(1).strip() if match.lastindex else match.group(0).strip()
        normalized = _normalize_amount(value)
        dec = _amount_to_decimal(normalized)

        if dec is None:
            continue

        # 96, 55, etc. sont des faux positifs OCR, pas des taux TVA.
        if Decimal("0") <= dec <= Decimal("30"):
            return normalized, match.group(0).strip()

    return None, None


def _extract_total_ttc(
    text: str,
    total_ht: Optional[str] = None,
    vat_amount: Optional[str] = None,
    stamp_amount: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    text = _strip_targeted_ocr_section(text)

    value, raw = _first_match(_TOTAL_TTC_PATTERNS, text)

    if value:
        return _normalize_amount(value), raw

    marker = re.search(r"Montant\s*T\.?\s*T\.?\s*C\.?,?", text, re.I)

    if marker:
        tail = text[marker.end(): marker.end() + 260]
        candidates = re.findall(r"\b[0-9]+[.,][0-9]{1,3}\b", tail)

        decimal_candidates: List[Tuple[Decimal, str]] = []

        for candidate in candidates:
            item = _normalize_amount(candidate)
            dec = _amount_to_decimal(item)

            if dec is None:
                continue

            if Decimal("0") < dec < Decimal("100000"):
                decimal_candidates.append((dec, item))

        if decimal_candidates:
            ht_dec = _amount_to_decimal(total_ht)
            vat_dec = _amount_to_decimal(vat_amount)
            stamp_dec = _amount_to_decimal(stamp_amount) or Decimal("0")

            if ht_dec is not None and vat_dec is not None:
                expected = ht_dec + vat_dec + stamp_dec

                for dec, item in decimal_candidates:
                    if abs(dec - expected) <= Decimal("0.010"):
                        return item, f"Montant T.T.C fallback matched consistency: {tail}"

            decimal_candidates.sort(key=lambda x: x[0], reverse=True)
            return decimal_candidates[0][1], f"Montant T.T.C fallback: {tail}"

    # Dernier recours : sur certaines factures de transitaire
    # international (ex. SEKO BANSARD), le montant total n'est adjacent
    # a AUCUN libelle reconnu dans le texte lineraise par l'OCR (tableau
    # multi-colonnes eclate). Ces factures repetent neanmoins souvent
    # leur total a plusieurs endroits (sous-total, solde du, pied de
    # page) : le montant le plus frequemment repete dans tout le texte
    # est donc un candidat plausible, essaye uniquement si tout le reste
    # a echoue.
    # Garde-fou important : un vrai total de facture a (quasiment) toujours
    # une partie decimale (centimes/millimes) -- un entier nu comme "24"
    # (fragment d'annee dans une date "26-Jan-24" repete plusieurs fois)
    # ne doit jamais etre retenu, meme s'il est frequent.
    all_amounts = re.findall(r"\b[0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{1,3}\b", text)
    normalized_amounts = [
        (_normalize_amount(a), _amount_to_decimal(_normalize_amount(a)))
        for a in all_amounts
    ]
    plausible = [
        (item, dec) for item, dec in normalized_amounts
        if dec is not None and Decimal("1") < dec < Decimal("1000000")
    ]

    if plausible:
        counts = Counter(item for item, _ in plausible)
        most_common_item, occurrences = counts.most_common(1)[0]

        if occurrences >= 2:
            return most_common_item, f"frequency fallback ({occurrences}x in raw_text)"

    return None, None


def _infer_stamp_from_summary(
    total_ht: Optional[str],
    vat_amount: Optional[str],
    total_ttc: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    ht = _amount_to_decimal(total_ht)
    vat = _amount_to_decimal(vat_amount)
    ttc = _amount_to_decimal(total_ttc)

    if ht is None or vat is None or ttc is None:
        return None, None

    stamp = ttc - ht - vat

    if Decimal("0") <= stamp <= Decimal("5"):
        return f"{stamp:.3f}", "inferred: total_ttc - total_ht - vat_amount"

    return None, None


# -----------------------------------------------------------------------------
# Extraction tableau
# -----------------------------------------------------------------------------

def _is_probable_table_code(token: str) -> bool:
    token = token.strip().upper().strip(":-")

    blacklist = {
        "CODE", "TOTAL", "POSTE", "BASE", "TVA", "TAX", "MONTANT",
        "VERSEMENT", "COUPON", "BULLETIN", "REFERENCE", "RÉFÉRENCE",
        "SOUS", "BERGES", "ADRESSE", "TELEPHONE", "TÉLÉPHONE",
        "TELECOPIE", "TÉLÉCOPIE", "SITE", "WEB", "TUNISIE",
        "TRADENET", "RC",
    }

    if token in blacklist:
        return False

    if not re.fullmatch(r"[A-Z][A-Z0-9\.]{1,12}", token):
        return False

    return True


def _get_table_segment(text: str) -> Optional[str]:
    flat = _compact_text(text)

    header_patterns = [
        re.compile(
            r"Code\s+D[ée]signation\s+Quantit[eé]?\s+T\.?\s*V\.?\s*A\.?%?\s+P\.?\s*U\.?\s*H\.?\s*T\.?\s*V\.?\s*A\.?\s+Total\s+H\.?\s*T\.?\s*V\.?\s*A\.?,?",
            re.I,
        ),
        re.compile(r"Code\s+D[ée]signation.*?Total\s+H\.?\s*T\.?\s*V\.?\s*A\.?,?", re.I),
    ]

    start_pos = None

    for pattern in header_patterns:
        match = pattern.search(flat)
        if match:
            start_pos = match.end()
            break

    if start_pos is None:
        return None

    tail = flat[start_pos:]

    stop_patterns = [
        r"\bTaux\s*\(?%\)?\s+Base\b",
        r"\bMontant\s+TVA\b",
        r"\bDroit\s+de\s+Timbre\b",
        r"\bMontant\s*T\.?\s*T\.?\s*C\.?\b",
        r"\bArrete\b",
        r"\bArr[eê]t[eé]\b",
        r"\bPoste\s+Coupon\b",
        r"\bCoupon\s+de\s+Versement\b",
    ]

    stop_pos = len(tail)

    for stop in stop_patterns:
        match = re.search(stop, tail, re.I)
        if match:
            stop_pos = min(stop_pos, match.start())

    segment = tail[:stop_pos].strip()

    if len(segment) < 10:
        return None

    return segment


def _extract_line_items(text: str, default_vat_rate: Optional[str] = None) -> List[Dict[str, Any]]:
    segment = _get_table_segment(text)

    if not segment:
        return []

    tokens = segment.split()
    items: List[Dict[str, Any]] = []

    i = 0

    while i < len(tokens):
        code = tokens[i].strip().upper().strip(":-")

        if not _is_probable_table_code(code):
            i += 1
            continue

        window = tokens[i + 1: i + 18]
        number_positions: List[Tuple[int, str]] = []

        for j, tok in enumerate(window):
            clean = tok.strip().replace(",", ".")
            if re.fullmatch(r"[0-9]+(?:[.][0-9]+)?", clean):
                number_positions.append((j, tok))

        if len(number_positions) < 3:
            i += 1
            continue

        first_num_pos = number_positions[0][0]
        designation_tokens = window[:first_num_pos]
        designation = " ".join(designation_tokens).strip(" :-")

        if len(designation) < 3:
            i += 1
            continue

        numeric_values = [v for _, v in number_positions]

        quantity = _normalize_amount(numeric_values[0])
        vat_rate = default_vat_rate
        unit_price = None
        line_total = None
        used_count = 3

        if len(numeric_values) >= 4:
            possible_vat = _normalize_amount(numeric_values[1])
            possible_vat_dec = _amount_to_decimal(possible_vat)

            if possible_vat_dec is not None and Decimal("0") <= possible_vat_dec <= Decimal("30"):
                vat_rate = possible_vat
                unit_price = _normalize_amount(numeric_values[2])
                line_total = _normalize_amount(numeric_values[3])
                used_count = 4
            else:
                unit_price = _normalize_amount(numeric_values[1])
                line_total = _normalize_amount(numeric_values[2])
                used_count = 3
        else:
            unit_price = _normalize_amount(numeric_values[1])
            line_total = _normalize_amount(numeric_values[2])
            used_count = 3

        qty_dec = _amount_to_decimal(quantity)
        unit_dec = _amount_to_decimal(unit_price)
        total_dec = _amount_to_decimal(line_total)

        if qty_dec is None or unit_dec is None or total_dec is None:
            i += 1
            continue

        if qty_dec > Decimal("10000"):
            i += 1
            continue

        if unit_dec > Decimal("1000000") or total_dec > Decimal("1000000"):
            i += 1
            continue

        items.append(
            {
                "code": code,
                "designation": designation,
                "quantite": quantity,
                "taux_tva": vat_rate,
                "prix_unitaire_htva": unit_price,
                "total_htva": line_total,
            }
        )

        last_used_pos = number_positions[used_count - 1][0]
        i = i + 1 + last_used_pos + 1

    unique: List[Dict[str, Any]] = []
    seen = set()

    for item in items:
        key = (
            item.get("code"),
            item.get("designation"),
            item.get("quantite"),
            item.get("prix_unitaire_htva"),
            item.get("total_htva"),
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique[:30]


# -----------------------------------------------------------------------------
# Validation metier
# -----------------------------------------------------------------------------

def _amount_consistency(
    total_ht: Optional[str],
    vat_amount: Optional[str],
    stamp_amount: Optional[str],
    total_ttc: Optional[str],
) -> Tuple[Optional[bool], Optional[str]]:
    ht = _amount_to_decimal(total_ht)
    vat = _amount_to_decimal(vat_amount)
    stamp = _amount_to_decimal(stamp_amount) or Decimal("0")
    ttc = _amount_to_decimal(total_ttc)

    if ht is None or vat is None or ttc is None:
        return None, None

    calculated = ht + vat + stamp
    diff = abs(calculated - ttc)
    ok = diff <= Decimal("0.010")

    return ok, f"{ht}+{vat}+{stamp}={calculated} ≈ {ttc}"


# -----------------------------------------------------------------------------
# Extracteur principal
# -----------------------------------------------------------------------------

class InvoiceExtractor(BaseExtractor):
    doc_family = "invoice"
    variant_id = "invoice_tn_multi_profile_v33_no_targeted_amount_contamination"

    def extract(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[FieldOutput]:
        text = _clean_text(text)
        compact = _compact_text(text)
        fields: List[FieldOutput] = []

        profile = _detect_invoice_profile(compact)

        fields.append(
            self._make_field(
                "invoice_profile",
                profile,
                0.80 if profile != "unknown" else 0.30,
                profile,
                validated=profile != "unknown",
                error=None if profile != "unknown" else "invoice_profile_unknown",
            )
        )

        invoice_number, invoice_number_raw = _first_valid_match(
            _INVOICE_NUMBER_PATTERNS,
            compact,
            _valid_invoice_number,
        )

        fields.append(
            self._make_field(
                "invoice_number",
                invoice_number,
                0.88 if invoice_number else 0.0,
                invoice_number_raw,
                validated=invoice_number is not None,
                error=None if invoice_number else "invoice_number_not_found",
            )
        )

        invoice_date, invoice_date_raw = _extract_invoice_date(compact)

        fields.append(
            self._make_field(
                "invoice_date",
                invoice_date,
                0.86 if invoice_date else 0.0,
                invoice_date_raw,
                validated=invoice_date is not None,
                error=None if invoice_date else "invoice_date_not_found",
            )
        )

        reference_unique, reference_unique_raw = _extract_reference_unique(compact)

        fields.append(
            self._make_field(
                "reference_unique",
                reference_unique,
                0.86 if reference_unique else 0.0,
                reference_unique_raw,
                validated=reference_unique is not None,
                error=None if reference_unique else "reference_unique_not_found",
            )
        )

        supplier_name, supplier_raw = _extract_supplier(compact)

        fields.append(
            self._make_field(
                "supplier_name",
                supplier_name,
                0.78 if supplier_name else 0.0,
                supplier_raw,
                validated=supplier_name is not None,
                error=None if supplier_name else "supplier_name_not_found",
            )
        )

        customer_name, customer_raw = _extract_customer(compact)

        fields.append(
            self._make_field(
                "customer_name",
                customer_name,
                0.70 if customer_name else 0.0,
                customer_raw,
                validated=customer_name is not None,
                error=None if customer_name else "customer_name_not_found",
            )
        )

        supplier_tax_id, supplier_tax_raw = _first_match(_SUPPLIER_TAX_PATTERNS, compact)

        fields.append(
            self._make_field(
                "supplier_tax_id",
                supplier_tax_id,
                0.72 if supplier_tax_id else 0.0,
                supplier_tax_raw,
                validated=supplier_tax_id is not None,
                error=None if supplier_tax_id else "supplier_tax_id_not_found",
            )
        )

        customer_tax_id, customer_tax_raw = _first_match(_CUSTOMER_TAX_PATTERNS, compact)

        fields.append(
            self._make_field(
                "customer_tax_id",
                customer_tax_id,
                0.70 if customer_tax_id else 0.0,
                customer_tax_raw,
                validated=customer_tax_id is not None,
                error=None if customer_tax_id else "customer_tax_id_not_found",
            )
        )

        period_start, period_end, period_start_raw, period_end_raw = _extract_period_range(compact)

        fields.append(
            self._make_field(
                "period_start",
                period_start,
                0.72 if period_start else 0.0,
                period_start_raw,
                validated=period_start is not None,
                error=None if period_start else "period_start_not_found",
            )
        )

        fields.append(
            self._make_field(
                "period_end",
                period_end,
                0.72 if period_end else 0.0,
                period_end_raw,
                validated=period_end is not None,
                error=None if period_end else "period_end_not_found",
            )
        )

        due_raw_value, due_raw = _first_match(_PAYMENT_DUE_PATTERNS, compact)
        payment_due_date = _normalize_due_date(due_raw_value)

        fields.append(
            self._make_field(
                "payment_due_date",
                payment_due_date,
                0.72 if payment_due_date else 0.0,
                due_raw,
                validated=payment_due_date is not None,
                error=None if payment_due_date else "payment_due_date_not_found",
            )
        )

        # Montants: utiliser la version sans contamination OCR ciblee.
        amount_text = _strip_targeted_ocr_section(compact)

        total_ht_raw_value, total_ht_raw = _first_match(_TOTAL_HT_PATTERNS, amount_text)
        total_ht = _normalize_amount(total_ht_raw_value)

        fields.append(
            self._make_field(
                "total_ht",
                total_ht,
                0.84 if total_ht else 0.0,
                total_ht_raw,
                validated=total_ht is not None,
                error=None if total_ht else "total_ht_not_found",
            )
        )

        vat_amount_raw_value, vat_amount_raw = _first_match(_VAT_AMOUNT_PATTERNS, amount_text)
        vat_amount = _normalize_amount(vat_amount_raw_value)

        fields.append(
            self._make_field(
                "vat_amount",
                vat_amount,
                0.84 if vat_amount else 0.0,
                vat_amount_raw,
                validated=vat_amount is not None,
                error=None if vat_amount else "vat_amount_not_found",
            )
        )

        vat_rate, vat_rate_raw = _extract_vat_rate(amount_text)

        fields.append(
            self._make_field(
                "vat_rate",
                vat_rate,
                0.75 if vat_rate else 0.0,
                vat_rate_raw,
                validated=vat_rate is not None,
                error=None if vat_rate else "vat_rate_not_found",
            )
        )

        stamp_raw_value, stamp_raw = _first_match(_STAMP_PATTERNS, amount_text)
        stamp_amount = _normalize_amount(stamp_raw_value)

        total_ttc, total_ttc_raw = _extract_total_ttc(
            amount_text,
            total_ht=total_ht,
            vat_amount=vat_amount,
            stamp_amount=stamp_amount,
        )

        if not stamp_amount:
            inferred_stamp, inferred_stamp_raw = _infer_stamp_from_summary(
                total_ht=total_ht,
                vat_amount=vat_amount,
                total_ttc=total_ttc,
            )

            if inferred_stamp:
                stamp_amount = inferred_stamp
                stamp_raw = inferred_stamp_raw

        fields.append(
            self._make_field(
                "stamp_amount",
                stamp_amount,
                0.78 if stamp_amount else 0.0,
                stamp_raw,
                validated=stamp_amount is not None,
                error=None if stamp_amount else "stamp_amount_not_found",
            )
        )

        fields.append(
            self._make_field(
                "total_ttc",
                total_ttc,
                0.90 if total_ttc else 0.0,
                total_ttc_raw,
                validated=total_ttc is not None,
                error=None if total_ttc else "total_ttc_not_found",
            )
        )

        currency = None
        currency_raw = None

        currency_match = _CURRENCY_RE.search(compact)

        if currency_match:
            currency_raw = currency_match.group(0)
            currency = _normalize_currency(currency_raw)

        if not currency and re.search(r"\bDINARS?\b|\bMILLIMES?\b", compact, re.I):
            currency = "TND"
            currency_raw = "DINARS/MILLIMES"

        fields.append(
            self._make_field(
                "currency",
                currency,
                0.80 if currency else 0.0,
                currency_raw,
                validated=currency is not None,
                error=None if currency else "currency_not_found",
            )
        )

        line_items = _extract_line_items(amount_text, default_vat_rate=vat_rate)

        fields.append(
            self._make_field(
                "line_items",
                line_items,
                0.82 if line_items else 0.0,
                "table: Code / Designation / Quantite / TVA / PUHTVA / Total HTVA" if line_items else None,
                validated=bool(line_items),
                error=None if line_items else "line_items_not_found",
            )
        )

        consistency, consistency_raw = _amount_consistency(
            total_ht=total_ht,
            vat_amount=vat_amount,
            stamp_amount=stamp_amount,
            total_ttc=total_ttc,
        )

        fields.append(
            self._make_field(
                "amount_consistency",
                consistency,
                0.90 if consistency is not None else 0.0,
                consistency_raw,
                validated=consistency is True,
                error=None if consistency is True else "amount_consistency_not_computable_or_failed",
            )
        )

        log.debug(
            "InvoiceExtractor done",
            extra={
                "profile": profile,
                "invoice_number": invoice_number,
                "reference_unique": reference_unique,
                "line_items": len(line_items),
                "total_ttc": total_ttc,
            },
        )

        return fields