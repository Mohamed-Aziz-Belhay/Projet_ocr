from __future__ import annotations
import re
from typing import Iterable, List, Optional

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
ARABIC_TOKEN_RE = re.compile(r"[\u0600-\u06FF]{2,}")
BIDI_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
DIGIT_TRANSLATION = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_PERSIAN_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_DIAC_RE = re.compile(r"[\u064B-\u065F\u0670]")

# Mots-étiquettes courants sur une CIN tunisienne (labels imprimés, pas des
# valeurs) : à exclure quand on cherche une valeur de champ (nom, lieu...).
# Reconstruit puis RECOUPÉ avec une ancienne version fonctionnelle du
# fichier (_HEADER_WORDS) retrouvée par l'utilisateur - les deux sources
# convergent sur ce noyau.
COMMON_LABEL_TOKENS = {
    "بطاقة", "بطاقه", "التعريف", "تعريف",
    "الوطنية", "الوطنيه", "وطنيه",
    "الجمهورية", "الجمهوريه", "التونسية", "التونسيه", "تونسية", "تونس",
    "الاسم", "الإسم", "اللقب",
    "تاريخ", "الولادة", "الولاده", "ولادة", "الميلاد",
    "مكان", "بمكان",
    "ولد", "ولدت", "في",
    "تحرير", "صالحة", "غاية", "الرقم", "رقم",
}

# Tokens trop faibles/génériques pour constituer, à eux seuls, un lieu de
# naissance plausible (prépositions, connecteurs).
WEAK_PLACE_TOKENS = {"من", "في", "إلى", "على", "ب"}

# Mots d'en-tête utilisés par clean_arabic_phrase() / choose_best_rtl_candidate()
# ci-dessous (fonctions héritées de l'ancienne version). Alias de
# COMMON_LABEL_TOKENS pour rester cohérent entre les deux API.
_HEADER_WORDS = COMMON_LABEL_TOKENS


# ==========================================================================
# API actuelle (utilisée par app.pipeline.cin_localizer et le pipeline CIN)
# ==========================================================================

def strip_bidi_controls(text: str) -> str:
    return BIDI_RE.sub("", text or "")


def normalize_arabic_digits(text: str) -> str:
    return (text or "").translate(DIGIT_TRANSLATION)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def cleanup_arabic_text(text: str) -> str:
    text = strip_bidi_controls(text)
    text = normalize_arabic_digits(text)
    text = re.sub(r"[^\u0600-\u06FF0-9\s:/\\.\-]", " ", text)
    return normalize_spaces(text)


def contains_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text or ""))


def arabic_tokens(text: str) -> List[str]:
    return ARABIC_TOKEN_RE.findall(cleanup_arabic_text(text))


def filter_label_tokens(tokens: Iterable[str], forbidden: Optional[Iterable[str]] = None) -> List[str]:
    banned = set(COMMON_LABEL_TOKENS)
    if forbidden:
        banned.update(forbidden)
    return [t for t in tokens if t not in banned]


def plausible_arabic_phrase(text: str, *, max_tokens: int = 4, forbidden: Optional[Iterable[str]] = None) -> Optional[str]:
    toks = filter_label_tokens(arabic_tokens(text), forbidden)
    if not toks:
        return None
    if len(toks) > max_tokens:
        toks = toks[:max_tokens]
    phrase = " ".join(toks).strip()
    if len(phrase) < 2:
        return None
    return phrase


def score_arabic_phrase_quality(text: str) -> float:
    text = cleanup_arabic_text(text)
    if not text:
        return 0.0
    toks = arabic_tokens(text)
    if not toks:
        return 0.0
    penalty = 0.0
    if any(tok in COMMON_LABEL_TOKENS for tok in toks):
        penalty += 0.35
    if re.search(r"\d", text):
        penalty += 0.25
    if len(toks) > 3:
        penalty += 0.25
    if len(set(toks)) < len(toks):
        penalty += 0.10
    score = 0.9 - penalty
    return max(0.0, min(1.0, score))


def has_forbidden_label_token(text: str, forbidden: Optional[Iterable[str]] = None) -> bool:
    toks = set(arabic_tokens(text))
    banned = set(COMMON_LABEL_TOKENS)
    if forbidden:
        banned.update(forbidden)
    return bool(toks & banned)


def is_probable_name_value(text: str, forbidden: Optional[Iterable[str]] = None) -> bool:
    cleaned = cleanup_arabic_text(text)
    toks = filter_label_tokens(arabic_tokens(cleaned), forbidden)
    if not toks:
        return False
    if len(toks) > 2:
        return False
    if any(tok in WEAK_PLACE_TOKENS for tok in toks):
        return False
    if any(len(tok) < 2 for tok in toks):
        return False
    if re.search(r"\d", cleaned):
        return False
    return True


def is_probable_place_value(text: str, forbidden: Optional[Iterable[str]] = None) -> bool:
    cleaned = cleanup_arabic_text(text)
    toks = filter_label_tokens(arabic_tokens(cleaned), forbidden)
    if not toks:
        return False
    if len(toks) > 3:
        return False
    if any(tok in WEAK_PLACE_TOKENS for tok in toks):
        return False
    if re.search(r"\d", cleaned):
        return False
    return True


# ==========================================================================
# Fonctions héritées de l'ancienne version — ajoutées en complément, au cas
# où un autre module du repo les importe encore (à vérifier via un grep).
# Elles ne remplacent ni ne modifient l'API ci-dessus.
# ==========================================================================

def normalize_digits(text: str) -> str:
    if not text:
        return text
    return text.translate(DIGIT_TRANSLATION).translate(_PERSIAN_DIGIT_TRANSLATION)


def strip_diacritics(text: str) -> str:
    return _DIAC_RE.sub("", text or "")


def normalize_arabic_basic(text: str) -> str:
    if not text:
        return text
    text = normalize_digits(text)
    text = strip_diacritics(text)
    text = text.replace("ى", "ي")
    text = text.replace("ؤ", "و").replace("ئ", "ي")
    text = re.sub(r"[آأإٱ]", "ا", text)
    text = normalize_spaces(text)
    return text


def reverse_arabic_token(token: str) -> str:
    if not token or not ARABIC_TOKEN_RE.fullmatch(token):
        return token
    return token[::-1]


def reverse_arabic_tokens_in_text(text: str) -> str:
    if not text:
        return text
    return re.sub(r"[\u0600-\u06FF]+", lambda m: m.group(0)[::-1], text)


def candidate_rtl_forms(text: str) -> List[str]:
    """Génère plusieurs variantes RTL plausibles (texte normal, inversé
    caractère par caractère, ordre des mots inversé, combinaisons) — utile
    quand on ne sait pas dans quel sens l'OCR a rendu le texte arabe."""
    if not text:
        return []
    raw = normalize_spaces(text)
    rev_chars = reverse_arabic_tokens_in_text(raw)
    toks = raw.split()
    rev_order = " ".join(reversed(toks)) if len(toks) > 1 else raw
    rev_chars_toks = rev_chars.split()
    rev_chars_order = " ".join(reversed(rev_chars_toks)) if len(rev_chars_toks) > 1 else rev_chars

    out: List[str] = []
    for v in (
        raw,
        rev_chars,
        rev_order,
        rev_chars_order,
        normalize_arabic_basic(raw),
        normalize_arabic_basic(rev_chars),
        normalize_arabic_basic(rev_order),
    ):
        v = normalize_spaces(v)
        if v and v not in out:
            out.append(v)
    return out


def clean_arabic_phrase(text: str) -> str:
    """Nettoie une phrase arabe et retire les mots d'en-tête connus."""
    if not text:
        return ""
    text = normalize_arabic_basic(text)
    text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
    text = normalize_spaces(text)
    toks = [t for t in text.split() if t and t not in _HEADER_WORDS]
    return " ".join(toks).strip()


def choose_best_rtl_candidate(candidates: Iterable[str], *, min_len: int = 2, max_len: int = 60) -> str:
    """Sélectionne, parmi plusieurs candidats RTL (ex: sortie de
    candidate_rtl_forms), celui qui ressemble le plus à une vraie valeur
    métier (pénalise les mots d'en-tête, les chiffres, les longueurs
    aberrantes)."""
    best = ""
    best_score = -10**9
    for cand in candidates:
        if not cand:
            continue
        txt = clean_arabic_phrase(cand)
        if not txt:
            continue
        toks = arabic_tokens(txt)
        if not toks:
            continue
        joined = " ".join(toks)
        ln = len(joined)
        score = 0
        score += 8 * len(toks)
        score += min(ln, max_len)
        score -= 25 if ln < min_len else 0
        score -= 25 if ln > max_len else 0
        score -= 20 * sum(1 for t in toks if t in _HEADER_WORDS)
        score -= 10 * sum(1 for ch in joined if ch.isdigit())
        if score > best_score:
            best_score = score
            best = joined
    return best


def normalize_number_candidate(text: str) -> str:
    text = normalize_digits(text or "")
    return re.sub(r"\D", "", text)