"""
app/services/cin_text_normalizer.py
Normalise le texte extrait d'une CIN tunisienne.
Gère les variantes OCR de l'écriture arabe et les chiffres indo-arabes.
"""
from __future__ import annotations
import re
import unicodedata
from typing import Any, Dict, Optional, Tuple

from app.pipeline.common import normalize_text, to_ascii_digits


class CINTextNormalizer:

    # Corrections OCR de noms arabes courants
    ARABIC_OCR_NAME_FIXES: Dict[str, str] = {
        "محمم": "محمد", "دمحم": "محمد", "عزمم": "عزيز", "زمزع": "عزيز",
        "بممز": "زينب", "اضر": "رضا", "اضرر": "رضا", "ناضمر": "رمضان",
        "رماظ": "ظاهر", "نماممما": "الممامن", "محماب": "بالحم", "محماب": "بالحم",
        "ةممخ": "خممة", "دممبزما": "الزبيدي", "نمسحما": "الحسين",
        "نبرعما": "العربي", "ندعما": "العادي",
    }

    # Corrections OCR de lieux arabes
    ARABIC_OCR_PLACE_FIXES: Dict[str, str] = {
        "سمة": "سوسة", "س مة": "سوسة", "مم سة": "سوسة", "سمسم": "سوسة",
        "نابمس": "قابس", "نابسم": "قابس",
        "جمدمبم": "جندوبة",
        "الممعة": "الجمعة الأولى",
        "الممعة": "الجمعة الكبرى",
    }

    CIN_STOP_TOKENS = {
        "الاسم", "اللقب", "اللقب", "الاسم", "العمر", "السن", "سن",
        "التاريخ", "تاريخ", "الميلاد", "ميلاد", "مكان", "محل",
        "الجنسية", "الجنسيه", "الجنس", "تونس", "الجمهورية",
        "الجمهوريه", "التونسية", "التونسيه",
        "بطاقة", "هوية", "وطنية", "وطنيه",
        "رقم", "ر", "رق", "صالحة",
    }

    CIN_MONTH_VARIANTS: Dict[int, Tuple[str, ...]] = {
        1:  ("جانفي", "جانفي", "جانف", "جان"),
        2:  ("فيفري", "فبريري", "فبري", "فيري", "فبري", "فيري"),
        3:  ("مارس",),
        4:  ("أفريل", "افريل", "افريل", "أفريل"),
        5:  ("ماي",),
        6:  ("جوان", "جوانم"),
        7:  ("جويلية", "جويلي", "جويلت"),
        8:  ("أوت", "اوت", "أوث"),
        9:  ("سبتمبر", "سبتمبر"),
        10: ("أكتوبر", "اكتوبر", "اكتوبر", "أكتوبر", "اكتبر"),
        11: ("نوفمبر", "نوفمبر", "نوفمر", "نوفم", "نوف", "نوفبمر", "نوفبر"),
        12: ("ديسمبر", "ديسمبر", "ديسمر"),
    }

    REVERSED_LIKE_TOKENS = {
        "جمبزنما", "دممر", "رامعمب", "دمعما", "زاممما", "ءامدم",
        "نمسحما", "بممز", "نمدص", "رمم", "نمضمما", "نمرتاممما", "رامب",
    }

    def normalize_labels(self, text: str) -> str:
        replacements = [
            ("اللقب:", "اللقب"), ("اللقب:", "اللقب"), ("الالقب", "اللقب"),
            ("نساما", "الاسم"), ("نسإما", "الاسم"), ("نسا", "الاسم"),
            ("النسم", "الاسم"), ("النسم", "الاسم"), ("الاسم:", "الاسم"),
            ("الممادة", "الولادة"), ("الم ادة", "الولادة"), ("الممامادة", "الولادة"),
            ("تاريخالممادة", "تاريخ الولادة"),
            ("تاريخمالممادة", "تاريخ الولادة"),
            ("تاريخ الممادة", "تاريخ الولادة"),
            ("الولادة", "الولادة"),
            ("الممامم", "الموام"), ("الممام", "الموام"), ("عامما", "الموام"),
            ("عاما", "الموام"), ("مامما", "الموام"), ("تاما", "الموام"),
            ("مامم", "الموام"), ("الامم", "الموام"), ("الما", "الموام"),
            ("مامما", "الموام"), ("مامغا", "الموام"), ("الموامم", "الموام"),
            ("ممممموام", "الموام"), ("مممموام", "الموام"), ("تمممموام", "الموام"),
            ("نحمممموام", "الموام"),
            ("مت", "بنت"), ("مت", "بنت"), ("بثت", "بنت"),
        ]
        for bad, good in replacements:
            text = text.replace(bad, good)
        return text

    def normalize_arabic_text(self, text: Any) -> str:
        value = normalize_text(str(text or ""))
        value = to_ascii_digits(value)
        value = self.normalize_labels(value)
        for bad, good in self.ARABIC_OCR_NAME_FIXES.items():
            value = re.sub(
                rf"(?<![\u0600-\u06FF]){re.escape(bad)}(?![\u0600-\u06FF])",
                good, value
            )
        for bad, good in self.ARABIC_OCR_PLACE_FIXES.items():
            value = value.replace(bad, good)
        # Supprimer caractères de contrôle
        value = "".join(
            c for c in value
            if not (unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\t ")
        )
        return re.sub(r"\s+", " ", value).strip()

    def clean_name_token(self, token: str) -> Optional[str]:
        token = self.normalize_arabic_text(token)
        token = re.sub(r"[^\u0600-\u06FF]", "", token).strip()
        if not token or len(token) < 2 or token in self.CIN_STOP_TOKENS:
            return None
        if token in self.REVERSED_LIKE_TOKENS:
            return None
        return token

    @staticmethod
    def reverse_words_per_line(text: str) -> str:
        return "\n".join(
            " ".join(reversed(line.split()))
            for line in text.split("\n")
        )

    def month_from_context(self, context: str) -> Optional[int]:
        context = self.normalize_arabic_text(context)
        for month, variants in self.CIN_MONTH_VARIANTS.items():
            for token in variants:
                if token and token in context:
                    return month
        return None