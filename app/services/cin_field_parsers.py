"""
app/services/cin_field_parsers.py
Parseurs de champs pour la CIN tunisienne.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.pipeline.common import normalize_text
from app.services.cin_text_normalizer import CINTextNormalizer


class CINFieldParsers:
    def __init__(self, normalizer: Optional[CINTextNormalizer] = None):
        self.norm = normalizer or CINTextNormalizer()

    def looks_like_family_name(self, value: Any) -> bool:
        text = self.norm.normalize_arabic_text(value)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        tokens = [t for t in text.split() if t]
        if not tokens or len(tokens) > 3:
            return False

        bad_tokens = {
            "الاسم", "نساما", "نسإما", "اللقب", "بننما", "تاريخ", "تاخ",
            "تابيخ", "ثاريخ", "الولادة", "موامه", "موام", "الجمهورية",
            "الجمهوريه", "التونسية", "التونسيه", "بطاقة", "بطاقه",
            "التعريف", "الوطنية", "الوطنيه", "right_text_block",
        }
        if any(t in bad_tokens for t in tokens):
            return False
        if len(tokens) >= 3 and ("ولد" in tokens or "بنت" in tokens):
            return False

        place_like = {"تونس", "سوسة", "قابس", "توزر", "حزمة", "مونتوريا", "مدنين", "الجم", "الجمعة"}
        if any(t in place_like for t in tokens):
            return False
        return True

    def looks_like_first_name_phrase(self, value: Any) -> bool:
        text = self.norm.normalize_arabic_text(value)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        tokens = [t for t in text.split() if t]
        if not tokens or len(tokens) > 3:
            return False

        bad_tokens = {
            "ولد", "بنت", "الاسم", "اللقب", "اللقب", "اللقب", "اللقب",
            "العمر", "السن", "سن", "التاريخ", "تاريخ", "الميلاد", "ميلاد",
            "الجنسية", "الجنسيه", "الجمهورية", "الجمهوريه",
            "الجمهورية", "الجمهوريه", "التونسية", "التونسيه",
        }
        return not any(t in bad_tokens for t in tokens)

    def name_has_forbidden_context(self, value: Any) -> bool:
        text = self.norm.normalize_arabic_text(value)
        forbidden = [
            "تاريخ", "تاخ", "تابيخ", "ثاريخ", "الولادة", "موامه", "موام",
            "مممموام", "مممموام", "تمممموام", "الجمهورية", "الجمهوريه",
            "التونسية", "التونسيه", "بطاقة", "بطاقه", "التعريف", "الوطنية",
            "الوطنيه", "CIN_", "right_text_block",
        ]
        return any(x in text for x in forbidden)

    def clean_family_candidate(self, raw: Any) -> Optional[str]:
        text = self.norm.normalize_arabic_text(raw)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None

        stop_tokens = {
            "الاسم", "نساما", "نسإما", "اللقب", "بننما", "تاريخ", "تاخ",
            "تابيخ", "ثاريخ", "الولادة", "موامه", "موام", "الجمهورية",
            "التونسية", "بطاقة", "التعريف", "الوطنية", "الوطنيه",
            "right_text_block", "CIN", "NUMBER", "FAMILY", "NAME", "FIRST",
            "DATE", "BIRTH", "PLACE",
        }

        tokens: List[str] = []
        for tok in text.split():
            clean = self.norm.clean_name_token(tok)
            if not clean or clean in stop_tokens:
                break
            tokens.append(clean)

        if not tokens or len(tokens) > 3:
            return None

        value = " ".join(tokens)
        return value if self.looks_like_family_name(value) else None

    def clean_first_name_candidate(self, raw: Any) -> Optional[str]:
        text = self.norm.normalize_arabic_text(raw)
        text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None

        stop_tokens = {
            "الاسم", "اللقب", "اللقب", "اللقب", "اللقب", "ولد", "بنت",
            "العمر", "السن", "سن", "التاريخ", "تاريخ", "الميلاد", "ميلاد",
            "الجنسية", "الجنسيه", "الجمهورية", "الجمهوريه",
            "الجمهورية", "التونسية", "التونسيه",
            "الجمهورية", "التونسية",
            "right_text_block",
        }

        tokens: List[str] = []
        for tok in text.split():
            clean = self.norm.clean_name_token(tok)
            if not clean or clean in stop_tokens:
                break
            tokens.append(clean)

        if not tokens or len(tokens) > 3:
            return None

        value = " ".join(tokens)
        return value if self.looks_like_first_name_phrase(value) else None

    def extract_family_name_from_text(self, text: str) -> Optional[str]:
        text = self.norm.normalize_arabic_text(text)
        candidates: List[Tuple[int, str, str]] = []

        patterns = [
            (40, r"اللقب\s+([\u0600-\u06FF][\u0600-\u06FF\s]{1,45}?)(?=\s+(?:الاسم|تاريخ الميلاد|$))"),
            (44, r"([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\sبنت\s+([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\sولد"),
            (20, r"(?<![A-Za-z_])([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\sبنت(?=\s|$)"),
        ]

        for base_score, pattern in patterns:
            for match in re.finditer(pattern, text):
                value = self.clean_family_candidate(match.group(1))
                if value:
                    candidates.append((base_score + len(value.split()), value, match.group(0)))

        if not candidates:
            return None
        ranked = [(score, idx, value, ctx) for idx, (score, value, ctx) in enumerate(candidates)]
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return ranked[0][2]

    def extract_first_name_from_text(self, text: str) -> Optional[str]:
        text = self.norm.normalize_arabic_text(text)
        candidates: List[Tuple[int, str, str]] = []

        patterns = [
            (48, r"بنت\s+[\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2}\sولد\s+([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+(?=بنت\b)"),
            (44, r"ولد\s+([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+(?=بنت\b)"),
            (34, r"(?:^|\s)([\u0600-\u06FF]{2,25}(?:\s+[\u0600-\u06FF]{2,25}){0,2})\s+الاسم\s+(?=بنت\b)"),
            (20, r"ولد\s+([\u0600-\u06FF]{2,25})(?=\s+(?:تاريخ الميلاد|$))"),
        ]

        for base_score, pattern in patterns:
            for match in re.finditer(pattern, text):
                raw = match.group(1)
                raw_tokens = raw.split()
                tried = [" ".join(raw_tokens[-w:]) for w in (1, 2, 3) if len(raw_tokens) >= w]
                if not tried:
                    tried = [raw]
                for candidate_raw in tried:
                    value = self.clean_first_name_candidate(candidate_raw)
                    if value:
                        candidates.append((base_score + max(0, 3 - len(value.split())), value, match.group(0)))
                        break

        if not candidates:
            return None
        ranked = [(score, idx, value, ctx) for idx, (score, value, ctx) in enumerate(candidates)]
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return ranked[0][2]

    def extract_names_from_text(self, raw_text: str) -> Tuple[Optional[str], Optional[str], str]:
        text = self.norm.normalize_arabic_text(raw_text)
        text = re.sub(r"[^\u0600-\u06FF0-9\s\n]", " ", text)
        text = re.sub(r"[ \t]+", " ", text).strip()

        family_name = self.extract_family_name_from_text(text)
        first_name  = self.extract_first_name_from_text(text)
        used = ["normal"]

        if not family_name or not first_name:
            reversed_text = self.norm.normalize_arabic_text(self.norm.reverse_words_per_line(text))
            if not family_name:
                family_name = self.extract_family_name_from_text(reversed_text)
                if family_name:
                    used.append("family_word_order_reversed")
            if not first_name:
                first_name = self.extract_first_name_from_text(reversed_text)
                if first_name:
                    used.append("first_word_order_reversed")

        debug = f"اللقب:{family_name or '?'} الاسم:{first_name or '?'} variants:{','.join(used)}"
        return family_name, first_name, debug

    def extract_birth_date_from_context(self, raw_text: str) -> Tuple[Optional[str], Optional[str]]:
        text = self.norm.normalize_arabic_text(raw_text)
        text = re.sub(r"\s+", " ", text).strip()
        current_year = date.today().year
        month_words = [v for variants in self.norm.CIN_MONTH_VARIANTS.values() for v in variants]
        month_re = "|".join(re.escape(w) for w in sorted(set(month_words), key=len, reverse=True) if w)

        direct = re.search(rf"\b([0-9]{{1,2}})\s+({month_re})\s+([0-9]{{4}})\b", text)
        if direct:
            day   = int(direct.group(1))
            month = self.norm.month_from_context(direct.group(2))
            year  = int(direct.group(3))
            if month and 1 <= day <= 31 and 1900 <= year <= current_year:
                return f"{year:04d}-{month:02d}-{day:02d}", f"direct_textual:{direct.group(0)}"

        contexts: List[str] = []
        for anchor in ("تاريخ الولادة", "الولادة", "مولود", "مولودة", "ماالولادة"):
            idx = text.find(anchor)
            if idx >= 0:
                contexts.append(text[max(0, idx - 90): min(len(text), idx + 150)])
        contexts.append(text)

        candidates: List[Tuple[int, str, str]] = []
        for ctx in contexts:
            month = self.norm.month_from_context(ctx)
            years = [y for y in [int(x) for x in re.findall(r"\b(19[0-9]{2}|20[0-9]{2})\b", ctx)]
                     if 1900 <= y <= current_year]

            if month:
                days = [n for n in [int(x) for x in re.findall(r"\b([0-9]{1,2})\b", ctx)] if 1 <= n <= 31]
                for year in years:
                    for day in days:
                        candidates.append(((20 if "الولادة" in ctx else 0) + 10,
                                           f"{year:04d}-{month:02d}-{day:02d}", ctx))

            nums = [int(x) for x in re.findall(r"\b([0-9]{1,4})\b", ctx)]
            for i in range(len(nums) - 2):
                a, b, c = nums[i], nums[i + 1], nums[i + 2]
                triples = []
                if 1 <= a <= 31 and 1 <= b <= 12 and 1900 <= c <= current_year:
                    triples.append((c, b, a))
                if 1900 <= a <= current_year and 1 <= b <= 12 and 1 <= c <= 31:
                    triples.append((a, b, c))
                for year, month_num, day in triples:
                    candidates.append((8 + (15 if "مولود" in ctx else 0),
                                       f"{year:04d}-{month_num:02d}-{day:02d}", ctx))

        if candidates:
            return max(candidates, key=lambda x: x[0])[1], "contextual_birth_date"
        return None, None

    def extract_birth_place_from_context(self, raw_text: str) -> Tuple[Optional[str], Optional[str]]:
        text = self.norm.normalize_arabic_text(raw_text)
        text = re.sub(r"[^\u0600-\u06FF0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        known_places = [
            "الجمعة الكبرى", "حزمة نوفمبر", "حياة الأولى",
            "المنستير", "الكاف", "سوسة", "مارس", "تونس", "قابس", "صفاقس", "نابل", "بنزرت",
        ]

        label_re = r"(?:موامه|مممموامه|عاما|مامه|ماما|مامه|الموامه)"
        stop_re  = r"(?=\s*(?:بنت|بننما|نساما|العمر|تاخ|ثاريخ|الولادة|CIN_|right_text_block|$))"
        pattern  = label_re + r"\s+([\u0600-\u06FF\s]{2,45}?)" + stop_re

        stop_words = {
            "الجمهورية", "التونسية", "تونسية", "بطاقة", "بطاقه", "التعريف",
            "الوطنية", "الوطنيه", "اللقب", "بننما", "الاسم", "نساما",
            "تاريخ", "تاخ", "تابيخ", "ثاريخ", "الولادة", "بن", "بنت",
        }
        likely_name_tokens = {
            "محمد", "محمد", "بنت", "ولد", "احمد", "علي", "فاطمة",
            "خالد", "سلمى", "يوسف", "امين", "سامي", "رضا", "مريم", "نور",
            "هاني", "غزالة", "سلوى", "نجلاء", "منى",
        }

        def clean_place_candidate(raw: str) -> Optional[str]:
            raw_norm = self.norm.normalize_arabic_text(raw)
            for known in known_places:
                if known in raw_norm:
                    return known
            tokens = []
            for token in raw_norm.strip().split():
                clean = self.norm.clean_name_token(token)
                if not clean or clean in stop_words or clean in self.norm.REVERSED_LIKE_TOKENS:
                    break
                if clean in likely_name_tokens and tokens:
                    break
                tokens.append(clean)
            if not tokens or len(tokens) > 3:
                return None
            place = normalize_text(" ".join(tokens))
            for bad, good in self.norm.ARABIC_OCR_PLACE_FIXES.items():
                place = place.replace(bad, good)
            if not place or self.name_has_forbidden_context(place):
                return None
            return place

        candidates: List[Tuple[int, int, str, str]] = []
        for idx, match in enumerate(re.finditer(pattern, text)):
            place = clean_place_candidate(match.group(1))
            if not place:
                continue
            score = 40 + idx
            if len(place.split()) >= 2:
                score += 8
            if place == "تونس":
                score -= 30
            if place in known_places:
                score += 15
            candidates.append((score, idx, place, f"place_after_label:{match.group(0)}"))

        for place in known_places:
            if place == "مارس":
                continue
            if place in text:
                candidates.append((20 + (8 if len(place.split()) >= 2 else 0), -1, place, "known_place_in_raw_text"))

        if not candidates:
            return None, None
        best_by_place: Dict[str, Tuple[int, int, str, str]] = {}
        for cand in candidates:
            score, idx, place, reason = cand
            old = best_by_place.get(place)
            if old is None or (score, idx) > (old[0], old[1]):
                best_by_place[place] = cand
        best = max(best_by_place.values(), key=lambda x: (x[0], x[1]))
        return best[2], best[3]