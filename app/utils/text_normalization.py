"""
app/utils/text_normalization.py
Normalisation du texte OCR pour les documents arabes, français et mixtes.
"""
from __future__ import annotations
import re
from typing import Optional
import unicodedata


def normalize_arabic(text: str) -> str:
    """
    Normalise le texte arabe OCR :
    - Supprime les diacritiques (tashkeel)
    - Normalise les variantes de lettres (alef, ya, ta marbuta)
    - Supprime les caractères parasites courants
    """
    if not text:
        return text

    # Supprime les diacritiques arabes (harakat)
    DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")
    text = DIACRITICS.sub("", text)

    # Normalise les variantes d'alef
    text = re.sub(r"[آأإٱ]", "ا", text)

    # Normalise ya maqsura → ya
    text = text.replace("ى", "ي")

    # Normalise ta marbuta → ha
    text = text.replace("ة", "ه")

    return text.strip()


def normalize_french(text: str) -> str:
    """
    Normalise le texte français OCR :
    - Supprime les caractères de contrôle
    - Normalise les apostrophes et tirets
    """
    if not text:
        return text
    text = re.sub(r"[''`]", "'", text)
    text = re.sub(r"[–—]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_ocr_noise(text: str) -> str:
    """
    Supprime le bruit OCR commun :
    - Lignes de tirets ou underscores
    - Répétitions de caractères parasites (|||, ___, ...)
    - Sauts de ligne excessifs
    """
    text = re.sub(r"[|_\-]{3,}", " ", text)
    text = re.sub(r"\.{3,}", "...", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_arabic_name(text: str) -> str:
    """
    Extrait uniquement les tokens arabes d'une ligne mixte.
    Utile pour séparer nom/prénom d'un contexte pollué.
    """
    arabic_tokens = re.findall(r"[\u0600-\u06FF\u0750-\u077F]+", text)
    return " ".join(arabic_tokens)


def is_arabic(text: str) -> bool:
    """Retourne True si le texte contient majoritairement des caractères arabes."""
    if not text:
        return False
    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    total_chars = len(re.sub(r"\s", "", text))
    return total_chars > 0 and (arabic_chars / total_chars) > 0.3


def normalize_number(text: str) -> Optional[str]:
    """
    Normalise un nombre extrait de texte OCR.
    Ex: "884 425,50" → "884425.50"
        "1 000,000 DT" → "1000.000"
    """
    if not text:
        return None
    # Remove currency symbols and units
    import re
    cleaned = re.sub(r'[^\d\s,.]', '', text).strip()
    # Remove thousands separator spaces
    cleaned = re.sub(r'\s+', '', cleaned)
    # Handle comma as decimal separator (French: 1.000,50 → 1000.50)
    if ',' in cleaned and '.' in cleaned:
        # Both present: dot is thousands, comma is decimal
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        # Only comma: could be decimal (French) or thousands
        parts = cleaned.split(',')
        if len(parts) == 2 and len(parts[1]) <= 3:
            cleaned = cleaned.replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    return cleaned if cleaned else None


