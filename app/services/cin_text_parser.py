
from __future__ import annotations

from typing import List, Optional, Set, Tuple

from app.services.cin_rules import (
    TN_PLACES,
    clean_words,
    contains_label_fragment,
    is_placeholder_value,
    is_valid_birth_place_strict,
    is_valid_family_name,
    is_valid_given_name,
    normalize_name,
    normalize_place,
)

RELATION_HINTS = {"بن", "بنت", "ابن", "حرم", "نب", "ننب", "تنب", "مرح"}

FAMILY_LABEL_HINTS = {
    "اللقب", "لقب", "القب", "بقللا", "اللفب", "للفب", "لفب", "الب", "الاقب",
}
FIRST_LABEL_HINTS = {
    "الاسم", "اسم", "الام", "السم", "اللسم", "مسالا", "الم", "مإل", "اللم", "الإسم",
}
PLACE_LABEL_HINTS = {
    "مكانها", "مكان", "مكانالولادة", "مكانالولاده", "محل",
    "اهزاكم", "اهناكم", "كانها", "تهاها", "كاغا", "معاا",
}
HEADER_WORDS = {"الجمهورية", "التونسية", "بطاقة", "التعريف", "الوطنية"}

_COMMON_AR_INITIALS = set("اأإآابتثجحخدذرزسشصضطظعغفقكلمنهوي")
_BETTER_NAME_INITIALS = set("اأإآبتمسشحعنفركلويدهز")
_BETTER_NAME_FINALS = {"ي", "ة", "ه", "ن", "ر", "د", "م", "ل", "ب", "ق", "ف"}
_WEAK_NAME_FINALS = {"ض", "ظ", "ط", "ث", "خ", "ذ", "غ"}

_PLACE_CANONICAL_REPLACEMENTS = {
    "الكبري": "الكبرى",
    "الكبر": "الكبرى",
    "الصغري": "الصغرى",
    "الانف": "الأنف",
}

_EXTRA_MULTIWORD_PLACES = {
    "حزوة توزر",
    "القلعة الكبرى",
    "القلعة الصغرى",
    "حمام الأنف",
    "زهرة مدين",
    "رأس الجبل",
    "منزل بورقيبة",
    "منزل بوزلفة",
    "سيدي بوزيد",
    "بن عروس",
    "تونس المدينة",
}


def all_known_places() -> Set[str]:
    return set(TN_PLACES) | set(_EXTRA_MULTIWORD_PLACES)


def canonicalize_place(text: str) -> str:
    value = normalize_place(text)
    for old, new in _PLACE_CANONICAL_REPLACEMENTS.items():
        value = value.replace(old, new)
    return normalize_place(value)


def name_char_len(text: str) -> int:
    return len("".join(clean_words(text)))


def _looks_headerish(value: str) -> bool:
    return any(w in HEADER_WORDS for w in clean_words(value))


def _contains_relation_words(value: str) -> bool:
    return any(w in RELATION_HINTS for w in clean_words(value))


def name_plausibility_score(text: str) -> float:
    words = clean_words(text)
    if not words:
        return -999.0

    score = 0.0
    for w in words:
        if not w:
            continue

        if w[0] == "ء":
            score -= 2.5
        if w[0] in _COMMON_AR_INITIALS:
            score += 0.8
        if w[0] in _BETTER_NAME_INITIALS:
            score += 1.2
        if w.startswith("ال"):
            score += 0.3
        if w.startswith("بن") and len(w) >= 4:
            score += 1.4
        if w.endswith("نب") and len(w) >= 4:
            score -= 1.4

        if w[-1] in _BETTER_NAME_FINALS:
            score += 1.1
        elif w[-1] in _WEAK_NAME_FINALS:
            score -= 1.0

        if any(ch in w[1:-1] for ch in ("ا", "و", "ي")):
            score += 0.5

        if len(w) <= 2:
            score -= 2.8
        elif len(w) >= 5:
            score += 0.4

    if contains_label_fragment(text):
        score -= 5.0
    if _contains_relation_words(text):
        score -= 6.0
    if _looks_headerish(text):
        score -= 6.0
    if name_char_len(text) < 3:
        score -= 8.0
    if normalize_name(text) in TN_PLACES:
        score -= 6.0

    return score


def _reverse_arabic_tokens(text: str) -> str:
    words = clean_words(text)
    if not words:
        return str(text or "").strip()
    return " ".join(w[::-1] for w in words)


def _join_trailing_single_letter(text: str) -> str:
    words = clean_words(text)
    if len(words) >= 2 and len(words[-1]) == 1:
        words[-2] = words[-2] + words[-1]
        words = words[:-1]
    return " ".join(words)


def _token_approx_equal(a: str, b: str) -> bool:
    a = canonicalize_place(a)
    b = canonicalize_place(b)
    if not a or not b:
        return False
    if a == b:
        return True

    la, lb = len(a), len(b)
    mn = min(la, lb)
    mx = max(la, lb)
    if mn >= 3 and mx - mn <= 1 and (a.startswith(b) or b.startswith(a)):
        return True

    return False


def repair_name_candidate(raw: str, validator) -> Optional[str]:
    raw = str(raw or "").strip()
    if not raw:
        return None

    candidates: List[Tuple[float, str]] = []
    seen: Set[str] = set()

    base = normalize_name(raw)
    joined = normalize_name(_join_trailing_single_letter(base))
    rev = normalize_name(_reverse_arabic_tokens(raw))
    rev_join = normalize_name(_join_trailing_single_letter(rev))

    for cand in (base, joined, rev, rev_join):
        if not cand or cand in seen:
            continue
        seen.add(cand)

        if is_placeholder_value(cand):
            continue
        if contains_label_fragment(cand):
            continue
        if _contains_relation_words(cand) or _looks_headerish(cand):
            continue
        if name_char_len(cand) < 3:
            continue
        if not validator(cand):
            continue

        score = name_plausibility_score(cand)
        if cand == base:
            score += 0.5
        candidates.append((score, cand))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def repair_place_candidate(raw: str) -> Optional[str]:
    raw = str(raw or "").strip()
    if not raw:
        return None

    cands: List[str] = []

    base = canonicalize_place(raw)
    if base:
        cands.append(base)

    joined = canonicalize_place(_join_trailing_single_letter(base))
    if joined and joined not in cands:
        cands.append(joined)

    rev = canonicalize_place(_reverse_arabic_tokens(raw))
    if rev and rev not in cands:
        cands.append(rev)

    rev_join = canonicalize_place(_join_trailing_single_letter(rev))
    if rev_join and rev_join not in cands:
        cands.append(rev_join)

    for cand in cands:
        if not cand:
            continue
        if is_placeholder_value(cand):
            continue
        if contains_label_fragment(cand):
            continue
        if _contains_relation_words(cand) or _looks_headerish(cand):
            continue
        if is_valid_birth_place_strict(cand) or cand in _EXTRA_MULTIWORD_PLACES:
            return cand

    return None


def _candidate_from_single_token(tok: str, validator) -> Optional[str]:
    if tok in HEADER_WORDS or tok in PLACE_LABEL_HINTS:
        return None
    return repair_name_candidate(tok, validator)


def _collect_after_label_candidates(
    tokens: List[str],
    start_idx: int,
    validator,
    stop_hints: Set[str],
    limit: int = 6,
) -> List[str]:
    out: List[str] = []
    max_j = min(len(tokens), start_idx + limit)
    for j in range(start_idx, max_j):
        tok = tokens[j]
        if tok in stop_hints or tok in RELATION_HINTS:
            break
        cand = _candidate_from_single_token(tok, validator)
        if cand and cand not in out:
            out.append(cand)
    return out


def _collect_before_label_candidates(
    tokens: List[str],
    label_idx: int,
    validator,
    stop_hints: Set[str],
    limit: int = 4,
) -> List[str]:
    out: List[str] = []
    min_j = max(0, label_idx - limit)
    for j in range(label_idx - 1, min_j - 1, -1):
        tok = tokens[j]
        if tok in stop_hints or tok in RELATION_HINTS:
            break
        cand = _candidate_from_single_token(tok, validator)
        if cand and cand not in out:
            out.append(cand)
    return out


def extract_family_name_from_text(raw_text: str) -> Optional[str]:
    tokens = clean_words(raw_text)
    scored: List[Tuple[float, str]] = []
    stop_hints = FAMILY_LABEL_HINTS | FIRST_LABEL_HINTS | PLACE_LABEL_HINTS

    for i, tok in enumerate(tokens):
        if tok not in FAMILY_LABEL_HINTS and not contains_label_fragment(tok):
            continue

        before_cands = _collect_before_label_candidates(tokens, i, is_valid_family_name, stop_hints, limit=4)
        after_cands = _collect_after_label_candidates(tokens, i + 1, is_valid_family_name, stop_hints, limit=6)

        if before_cands:
            scored.append((30.0 + name_plausibility_score(before_cands[0]), before_cands[0]))
        if after_cands:
            scored.append((28.0 + name_plausibility_score(after_cands[-1]), after_cands[-1]))
            scored.append((22.0 + name_plausibility_score(after_cands[0]), after_cands[0]))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def extract_first_name_from_text(raw_text: str) -> Optional[str]:
    tokens = clean_words(raw_text)
    scored: List[Tuple[float, str]] = []
    stop_hints = FAMILY_LABEL_HINTS | FIRST_LABEL_HINTS | PLACE_LABEL_HINTS

    for i, tok in enumerate(tokens):
        if tok not in FIRST_LABEL_HINTS and not contains_label_fragment(tok):
            continue

        before_cands = _collect_before_label_candidates(tokens, i, is_valid_given_name, stop_hints, limit=4)
        after_cands = _collect_after_label_candidates(tokens, i + 1, is_valid_given_name, stop_hints, limit=6)

        if before_cands:
            scored.append((29.0 + name_plausibility_score(before_cands[0]), before_cands[0]))
        if after_cands:
            scored.append((28.0 + name_plausibility_score(after_cands[0]), after_cands[0]))
            scored.append((20.0 + name_plausibility_score(after_cands[-1]), after_cands[-1]))

    for i, tok in enumerate(tokens):
        if tok not in RELATION_HINTS:
            continue

        if i + 1 < len(tokens):
            cand_next = _candidate_from_single_token(tokens[i + 1], is_valid_given_name)
            if cand_next:
                scored.append((16.0 + name_plausibility_score(cand_next), cand_next))

        if i - 1 >= 0:
            cand_prev = _candidate_from_single_token(tokens[i - 1], is_valid_given_name)
            if cand_prev:
                scored.append((14.0 + name_plausibility_score(cand_prev), cand_prev))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_value = scored[0]
    if best_score < 23.0:
        return None
    return best_value


def extract_name_pair_from_text(raw_text: str) -> Tuple[Optional[str], Optional[str]]:
    return extract_family_name_from_text(raw_text), extract_first_name_from_text(raw_text)


def _joined_token_forms(text_tokens: List[str]) -> Set[str]:
    forms: Set[str] = set()
    n = len(text_tokens)
    for size in (2, 3):
        for i in range(n - size + 1):
            chunk = text_tokens[i:i + size]
            forms.add(" ".join(chunk))
            forms.add("".join(chunk))
    return {canonicalize_place(x) for x in forms if x}


def _place_tokens_match(place: str, text_tokens: List[str]) -> bool:
    p_tokens = clean_words(place)
    if not p_tokens:
        return False

    used = [False] * len(text_tokens)
    for pt in p_tokens:
        found = False
        for i, tt in enumerate(text_tokens):
            if used[i]:
                continue
            if _token_approx_equal(pt, tt):
                used[i] = True
                found = True
                break
        if not found:
            return False
    return True


def best_known_place_from_text(raw_text: str) -> Optional[str]:
    text_tokens = clean_words(raw_text)
    text_norm = " ".join(text_tokens)
    joined_forms = _joined_token_forms(text_tokens)
    if not text_norm:
        return None

    ranked: List[Tuple[float, str]] = []
    for place in sorted(all_known_places(), key=len, reverse=True):
        p = canonicalize_place(place)
        if not p:
            continue

        score = None
        if p in text_norm:
            score = 100.0 + len(clean_words(p)) * 5 + len(p) * 0.1
        elif p in joined_forms or p.replace(" ", "") in joined_forms:
            score = 95.0 + len(clean_words(p)) * 5 + len(p) * 0.1
        elif _place_tokens_match(p, text_tokens):
            score = 80.0 + len(clean_words(p)) * 5 + len(p) * 0.1

        if score is not None:
            ranked.append((score, p))

    if not ranked:
        return None

    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1]