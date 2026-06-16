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

RELATION_WORDS = {"بن", "بنت", "ابن", "حرم", "نب", "ننب"}
HEADER_KEYWORDS = {"الجمهورية", "التونسية", "بطاقة", "التعريف", "الوطنية"}

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
    t = _normalize_arabic_chars(text)
    if not t:
        return False
    for frag in LABEL_FRAGMENTS:
        if frag in t or frag[::-1] in t:
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
    if contains_relation_word(t):
        return False
    if any(re.search(r"\d", w) for w in words):
        return False
    if any(w in NAME_BAD_TOKENS for w in words):
        return False
    if len(words) > 2:
        return False
    if t in TN_PLACES:
        return False
    return True


def is_valid_family_name(s: str) -> bool:
    return is_valid_name(s)


def is_valid_given_name(s: str) -> bool:
    t = normalize_name(s)
    words = clean_words(t)
    if not is_valid_name(t):
        return False
    if len(words) > 1:
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
    if contains_relation_word(t):
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