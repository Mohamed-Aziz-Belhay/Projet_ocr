#cin_rules.py - Core rules and heuristics for Tunisian CIN parsing and validation
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

try:
    from app.pipeline.field_extractors import parse_date_any as _external_parse_date_any
except Exception:
    _external_parse_date_any = None


AR_MONTHS: Dict[str, str] = {
    "جانفي": "01",
    "جانفى": "01",
    "فيفري": "02",
    "فيفرى": "02",
    "فبراير": "02",
    "مارس": "03",
    "افريل": "04",
    "أفريل": "04",
    "ابريل": "04",
    "أبريل": "04",
    "ماي": "05",
    "جوان": "06",
    "يونيو": "06",
    "جويلية": "07",
    "جويليه": "07",
    "يوليو": "07",
    "اوت": "08",
    "أوت": "08",
    "سبتمبر": "09",
    "اكتوبر": "10",
    "أكتوبر": "10",
    "نوفمبر": "11",
    "ديسمبر": "12",
}

TN_PLACES: Set[str] = {
    "تونس",
    "سوسة",
    "صفاقس",
    "القيروان",
    "قابس",
    "قفصة",
    "بنزرت",
    "نابل",
    "زغوان",
    "مدنين",
    "تطاوين",
    "قبلي",
    "جندوبة",
    "باجة",
    "سليانة",
    "الكاف",
    "المنستير",
    "المهدية",
    "سيدي بوزيد",
    "القصرين",
    "أريانة",
    "اريانة",
    "منوبة",
    "توزر",
    "حزوة",
    "القلعة الكبرى",
    "القلعة الصغرى",
    "الكرم",
    "حمام الأنف",
    "حمام الانف",
    "الماتلين",
    "جرجيس",
    "جرزونة",
    "قرمبالية",
    "رادس",
    "بومهل",
    "المروج",
    "المرسى",
    "دوار هيشر",
    "طبربة",
    "الوردية",
    "باردو",
    "الزهراء",
    "بني خلاد",
    "دار شعبان",
    "قليبية",
    "الهوارية",
    "منزل تميم",
    "المكنين",
    "الجم",
    "قصر هلال",
    "مساكن",
    "بوحجلة",
    "الشابة",
    "نفطة",
    "دقاش",
    "دوز",
    "رمادة",
    "تاجروين",
    "الدهماني",
    "سبيبة",
    "فريانة",
    "حيدرة",
    "مكثر",
    "سجنان",
    "رفراف",
    "ماطر",
    "رأس الجبل",
    "راس الجبل",
    "منزل بورقيبة",
    "منزل بوزلفة",
    "بن عروس",
    "المحمدية",
    "الوردانين",
    "فيتوريا",
    "زهرة مدين",
    "تونس المدينة",
}

# Common Tunisian given/family names (not exhaustive — a best-effort seed
# list, grown from real OCR'd cards). Used only to decide whether a name
# token that fails validation was probably read character-mirrored by OCR
# (see fix_reversed_name_token below): EasyOCR/Paddle sometimes emit a name
# box with its Arabic characters in reverse order — this is a DIFFERENT
# failure mode from the word-order reversal already handled elsewhere, and
# it is inconsistent (some boxes on the very same card are mirrored, others
# aren't), so it cannot be "always undone" — only recognizing the intended
# name lets us tell which orientation is right.
COMMON_GIVEN_NAMES: Set[str] = {
    "محمد", "احمد", "علي", "عمر", "يوسف", "ابراهيم", "حسين", "عبدالله",
    "خالد", "وليد", "سامي", "نبيل", "منير", "كريم", "رياض", "رضا", "هشام",
    "طارق", "فريد", "نجيب", "مراد", "انيس", "ماهر", "شادي", "زياد", "وسيم",
    "فادي", "عماد", "صدقي", "المنصف", "الشاذلي", "حبيب", "بشير", "منصف",
    "عادل", "لطفي", "فوزي", "جمال", "كمال", "بلقاسم", "الطيب", "سفيان",
    "ياسين", "ايمن", "معز", "غازي", "حمزة", "بلال", "عثمان", "صابر",
    "ظافر", "روند", "عميد", "زينب", "فاطمة", "امنة", "امينة", "سعاد",
    "نجاة", "ليلى", "سلمى", "هند", "راضية", "منال", "نادية", "سامية",
    "وفاء", "ايمان", "درة", "هيفاء", "ذكري", "اسماء", "رانيا", "مريم",
    "خديجة", "حياة", "سنية", "نعيمة", "منجية", "جميلة", "نجوي", "سهام",
    "وئام", "دنيا", "يسري", "بسمة", "فاتن", "رجاء", "حنان", "سميرة", "ريم",
    "اميرة", "شيماء", "نور", "سالم", "امين", "سفيان", "احلام", "وداد",
}

COMMON_FAMILY_NAMES: Set[str] = {
    "الشراد", "جماعي", "الزبيدي", "الحسني", "عامري", "بكار", "الوضيف",
    "العوادي", "جمور", "الجريدي", "نورالدين", "تريكي", "بوعزيزي",
    "الجلاصي", "الماجري", "الغربي", "السلامي", "الطرابلسي", "الشابي",
    "القروي", "الفقيه", "الورتاني", "الدريدي", "الحمروني", "الخياري",
    "الزغل", "الغزواني", "الحداد", "الجندوبي", "السماوي", "الشرفي",
    "بلحاج", "التومي", "الماجدي", "النقاز", "الكناني", "الجبوزي",
    "بنعمار",
}

# Every word making up any entry above, for per-token matching (family
# names are sometimes 2 tokens, e.g. would split "بن عروس"-style compounds
# if any were added here).
_COMMON_NAME_WORDS: Set[str] = {
    w for name in (COMMON_GIVEN_NAMES | COMMON_FAMILY_NAMES) for w in name.split()
}


def fix_reversed_name_token(token: str) -> str:
    """
    If `token` fails to match any known name but its char-by-char reversal
    does (and only then), return the reversed form — otherwise return the
    token unchanged. Deliberately conservative: with no gazetteer match in
    either direction, we have no evidence either way, so we don't guess.
    """
    if not token or len(token) < 2:
        return token
    if token in _COMMON_NAME_WORDS:
        return token
    reversed_token = token[::-1]
    if reversed_token in _COMMON_NAME_WORDS:
        return reversed_token
    return token


RELATION_WORDS = {"بن", "بنت", "ابن", "حرم", "نب", "ننب"}
HEADER_KEYWORDS = {"الجمهورية", "التونسية", "بطاقة", "التعريف", "الوطنية"}

# Individual words making up any TN_PLACES entry (e.g. "حمام" and "الأنف"
# from "حمام الأنف"). A single-word name candidate that is really just one
# half of a compound place name is a strong sign it leaked from the
# birth-place value into a name field (observed in production: a spatial
# box mismatch returned first_name="حمام", a fragment of "حمام الأنف").
PLACE_NAME_WORDS: Set[str] = {word for place in TN_PLACES for word in place.split()}

LABEL_KEYWORDS = {
    "اللقب", "الاقب", "لقب", "للقب", "بقللا", "اللفب", "للفب", "القب",
    "الاسم", "الإسم", "الام", "اام", "لاسم", "السم", "اللسم", "مسالا", "الم",
    "تاريخ", "الولادة", "مكانها", "مكان", "محل",
}

LABEL_FRAGMENTS = {
    "اللقب", "لقب", "القب", "بقللا", "اللفب",
    "الاسم", "اسم", "مسالا", "الام", "اللسم", "السم", "الم",
}

DATE_LABELS = ["تاريخ الولادة", "تاريخ", "الولادة", "تارخ", "تاخ", "خات", "غرات"]
PLACE_LABELS = ["مكانها", "مكان الولادة", "مكان", "محل", "اهناكم", "اهزاكم", "اهاكم", "عانا", "كانها"]

NAME_BAD_TOKENS = {
    "اللقب", "لقب", "القب",
    "الاسم", "اسم",
    "تاريخ", "الولادة", "مكانها", "مكان",
    "الجمهورية", "التونسية", "بطاقة", "التعريف", "الوطنية",
    "الم",
}
PLACE_BAD_TOKENS = {
    "اللقب", "الاسم", "تاريخ", "الولادة", "مكانها", "مكان",
    "الجمهورية", "التونسية", "بطاقة", "التعريف", "الوطنية",
}
COMPOUND_NAME_TAILS = {"الدين", "الله", "الرحمن", "الحميد", "العزيز", "الكريم", "القادر"}

_NON_AR_CLEAN_RE = re.compile(r"[^\u0600-\u06FF0-9\s:/\-.]")
_MULTI_SPACE_RE = re.compile(r"\s+")
_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")


def norm_digits(s: str) -> str:
    trans = str.maketrans(
        "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
        "01234567890123456789",
    )
    return (s or "").translate(trans)


def digits_only(s: str) -> str:
    return re.sub(r"\D", "", norm_digits(s or ""))


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def collapse_repeated_chars(s: str) -> str:
    return re.sub(r"([\u0600-\u06FF])\1+", r"\1", s or "")


def strip_noise_edges(s: str) -> str:
    return re.sub(r"^[\W_]+|[\W_]+$", "", s or "").strip()


def _normalize_arabic_chars(s: str) -> str:
    s = norm_digits(s or "")
    s = _DIACRITICS_RE.sub("", s)
    s = s.replace("ـ", "")
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ؤ", "و").replace("ئ", "ي")
    s = s.replace("ى", "ي")
    s = _NON_AR_CLEAN_RE.sub(" ", s)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def is_ar_word(w: str) -> bool:
    return bool(re.fullmatch(r"[\u0600-\u06FF]+", w or ""))


def clean_words(text: str) -> List[str]:
    base = _normalize_arabic_chars(text)
    out: List[str] = []
    for tok in base.split():
        tok = collapse_repeated_chars(tok)
        tok = strip_noise_edges(tok)
        if tok:
            out.append(tok)
    return out


def lines_from_text(text: str) -> List[str]:
    out: List[str] = []
    for ln in (text or "").splitlines():
        ln = norm_space(_normalize_arabic_chars(ln))
        if ln:
            out.append(ln)
    return out


def contains_relation_word(text: str) -> bool:
    return any(w in RELATION_WORDS for w in clean_words(text))


def contains_label_fragment(text: str) -> bool:
    # Whole-token match only: several LABEL_FRAGMENTS entries are short
    # (e.g. "الام", "الم") and, as a raw substring check against the whole
    # text, matched inside common Tunisian first names such as "سالم"
    # (contains "الم") or "الأمين" (contains "الام"), silently discarding
    # correctly-OCR'd names. A label fragment only makes sense as a
    # complete token (or its char-mirrored OCR form), never as an infix.
    words = clean_words(text)
    if not words:
        return False
    for w in words:
        if w in LABEL_FRAGMENTS or w[::-1] in LABEL_FRAGMENTS:
            return True
    return False


def is_placeholder_value(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return True

    up = t.upper()
    if up.startswith("CIN_"):
        return True

    bad_ascii = {
        "FAMILY_NAME",
        "FIRST_NAME",
        "DATE_OF_BIRTH",
        "PLACE_OF_BIRTH",
        "CIN_NUMBER",
    }
    if any(x in up for x in bad_ascii):
        return True

    if re.fullmatch(r"[A-Z_:\-\s]+", up):
        return True

    return False


def normalize_name(s: str) -> str:
    return norm_space(" ".join(clean_words(s)))


def normalize_place(s: str) -> str:
    value = norm_space(" ".join(clean_words(s)))
    value = value.replace("الكبري", "الكبرى")
    value = value.replace("الصغري", "الصغرى")
    value = value.replace("الانف", "الأنف")
    return norm_space(value)


def _parse_date_fallback(text: str) -> Optional[str]:
    t = _normalize_arabic_chars(text)

    m = re.search(r"\b(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})\b", t)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mth <= 12:
            return f"{y:04d}-{mth:02d}-{d:02d}"

    m = re.search(r"\b(\d{1,2})\s+([^\d\s]{3,12})\s+(\d{4})\b", t)
    if m:
        d = int(m.group(1))
        month_txt = m.group(2)
        y = int(m.group(3))
        month_num = AR_MONTHS.get(month_txt)
        if month_num and 1 <= d <= 31:
            return f"{y:04d}-{int(month_num):02d}-{d:02d}"

    m = re.search(r"\b(\d{4})\s+([^\d\s]{3,12})\s+(\d{1,2})\b", t)
    if m:
        y = int(m.group(1))
        month_txt = m.group(2)
        d = int(m.group(3))
        month_num = AR_MONTHS.get(month_txt)
        if month_num and 1 <= d <= 31:
            return f"{y:04d}-{int(month_num):02d}-{d:02d}"

    return None


def parse_date_any(text: str) -> Optional[str]:
    if _external_parse_date_any is not None:
        try:
            out = _external_parse_date_any(text)
            if out:
                return out
        except Exception:
            pass
    return _parse_date_fallback(text)


def is_valid_cin_number(s: str) -> bool:
    return bool(re.fullmatch(r"\d{8}", digits_only(s)))


def is_valid_name(s: str) -> bool:
    t = normalize_name(s)
    words = clean_words(t)

    if not t or not words:
        return False
    if is_placeholder_value(t):
        return False
    if contains_label_fragment(t):
        return False
    # A relation word ("بن"/"بنت") alone marks a short, legitimate Tunisian
    # family name such as "بن علي" (Ben Ali) or "بن عمار" — only a full
    # filiation chain (>=3 tokens, e.g. "رضا بن عمر بن العربي") should be
    # rejected here. Mirrors app.pipeline.cin_runner._looks_like_family_name.
    if len(words) >= 3 and contains_relation_word(t):
        return False
    if any(re.search(r"\d", w) for w in words):
        return False
    if any(w in NAME_BAD_TOKENS for w in words):
        return False
    if len(words) > 2:
        return False
    if t in TN_PLACES:
        return False
    if any(w in PLACE_NAME_WORDS for w in words):
        return False
    return True


def is_valid_family_name(s: str) -> bool:
    return is_valid_name(s)


def is_valid_given_name(s: str) -> bool:
    # Independent from is_valid_name: Tunisian first names may legitimately
    # be 2-3 tokens (e.g. "محمد الأمين", "فاطمة الزهراء", "عبد الرحمن") —
    # capping at a single token discarded correctly-OCR'd compound first
    # names. Mirrors app.pipeline.cin_runner._looks_like_first_name_phrase,
    # which allows up to 3 tokens.
    t = normalize_name(s)
    words = clean_words(t)

    if not t or not words:
        return False
    if is_placeholder_value(t):
        return False
    if contains_label_fragment(t):
        return False
    if contains_relation_word(t):
        return False
    if any(re.search(r"\d", w) for w in words):
        return False
    if any(w in NAME_BAD_TOKENS for w in words):
        return False
    if len(words) > 3:
        return False
    if t in TN_PLACES:
        return False
    if any(w in PLACE_NAME_WORDS for w in words):
        return False
    return True


def is_valid_place(s: str) -> bool:
    t = normalize_place(s)
    words = clean_words(t)

    if not t or not words:
        return False
    if is_placeholder_value(t):
        return False
    if contains_label_fragment(t):
        return False
    # Same rationale as is_valid_name: "بن عروس" (Ben Arous) is a real
    # Tunisian governorate and is listed in TN_PLACES — only reject when a
    # relation word appears inside a much longer (filiation-like) fragment.
    if len(words) >= 3 and contains_relation_word(t):
        return False
    if any(re.search(r"\d", w) for w in words):
        return False
    if any(w in PLACE_BAD_TOKENS for w in words):
        return False
    if len(words) > 3:
        return False
    return True


def is_valid_birth_place_strict(s: str) -> bool:
    t = normalize_place(s)
    if not is_valid_place(t):
        return False
    if len(clean_words(t)) == 1 and t not in TN_PLACES:
        return False
    return True


def extract_best_birth_date_from_text(raw_text: str) -> Optional[str]:
    lines = lines_from_text(raw_text)
    candidates: List[Tuple[int, str]] = []

    for ln in lines:
        iso = parse_date_any(ln)
        if iso:
            score = 5
            if any(lbl in ln for lbl in ["تاريخ", "الولادة", "تاخ", "تارخ", "خات"]):
                score += 4
            if any(m in ln for m in AR_MONTHS):
                score += 3
            candidates.append((score, iso))

    text_norm = _normalize_arabic_chars(raw_text)
    for m in re.finditer(r"(\d{1,2}\s+[^\d\s]{3,12}\s+\d{4})", text_norm):
        iso = parse_date_any(m.group(1))
        if iso:
            candidates.append((8, iso))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def extract_best_birth_place_from_text(raw_text: str) -> Optional[str]:
    text_norm = " ".join(lines_from_text(raw_text))
    if not text_norm:
        return None

    best = None
    best_len = -1
    for place in sorted(TN_PLACES, key=len, reverse=True):
        p = normalize_place(place)
        if p and p in text_norm:
            if len(p) > best_len:
                best = p
                best_len = len(p)

    return best


def normalize_field_value(field: str, value) -> Optional[str]:
    if value is None:
        return None

    v = str(value).strip()
    if not v:
        return None
    if is_placeholder_value(v):
        return None

    if field == "cin_number":
        d = digits_only(v)
        return d if is_valid_cin_number(d) else None

    if field == "date_of_birth":
        return parse_date_any(v)

    if field == "family_name":
        v = normalize_name(v)
        return v if is_valid_family_name(v) else None

    if field == "first_name":
        v = normalize_name(v)
        return v if is_valid_given_name(v) else None

    if field == "place_of_birth":
        v = normalize_place(v)
        return v if is_valid_birth_place_strict(v) else None

    return None