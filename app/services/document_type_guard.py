"""
app/services/document_type_guard.py

Validation rapide du type documentaire avant extraction OCR complète.

Types supportés côté UI/backend:
- auto
- custom
- cin_tn
- id_document
- passport
- invoice
- registre_commerce

Objectifs:
- Bloquer les mauvais choix évidents.
- Distinguer CIN tunisienne, carte d'identité étrangère, passeport, facture et registre.
- Éviter qu'une carte d'identité étrangère soit acceptée comme passeport.
- Gérer l'arabe OCR inversé caractère par caractère.

FIX: le motif de détection MRZ "carte identité" (\bid[a-z0-9<]{5,}) était
trop permissif et matchait n'importe quel mot commençant par "id" suivi de
5 caractères alphanumériques - notamment "identité"/"identite", très courant
sur les CIN tunisiennes elles-mêmes. Remplacé par un motif qui exige la
présence d'un caractère de remplissage MRZ "<" (comme pour le motif passeport
P<), ce qui élimine les faux positifs tout en gardant la détection des
vraies MRZ de carte d'identité (ID<TUN..., ID<SVK..., etc.).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TypeDetectionResult:
    detected_type: str
    confidence: float
    reasons: list[str]


SUPPORTED_GUARD_TYPES = {
    "auto",
    "custom",
    "unknown",
    "cin_tn",
    "id_document",
    "passport",
    "invoice",
    "registre_commerce",
}


def normalize_document_type(value: str | None) -> str:
    if not value:
        return "auto"

    v = value.strip().lower()

    aliases = {
        # Auto / custom
        "auto": "auto",
        "custom": "custom",
        "unknown": "unknown",

        # Carte d'identité générique / étrangère
        "id": "id_document",
        "id_card": "id_document",
        "id-card": "id_document",
        "identity_card": "id_document",
        "identity card": "id_document",
        "id_document": "id_document",
        "document_identite": "id_document",
        "document_identité": "id_document",
        "carte_identite": "id_document",
        "carte_identité": "id_document",
        "carte identité": "id_document",

        # CIN tunisienne
        "cin": "cin_tn",
        "cin_tn": "cin_tn",
        "cin tunisienne": "cin_tn",
        "cin_tunisienne": "cin_tn",
        "carte nationale": "cin_tn",
        "carte_identite_tn": "cin_tn",
        "carte_identité_tn": "cin_tn",
        "carte d'identité tunisienne": "cin_tn",

        # Facture
        "facture": "invoice",
        "invoice": "invoice",
        "ttn": "invoice",
        "ttn_electronic": "invoice",

        # Passeport
        "passport": "passport",
        "passeport": "passport",

        # Registre commerce
        "registre": "registre_commerce",
        "rc": "registre_commerce",
        "rne": "registre_commerce",
        "registre de commerce": "registre_commerce",
        "registre_commerce": "registre_commerce",
    }

    return aliases.get(v, v)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _reverse_arabic_tokens(text: str) -> str:
    """
    Certains OCR retournent les mots arabes inversés caractère par caractère.
    Exemple:
        ةقاطب -> بطاقة
        فيرعتلا -> التعريف
    """
    if not text:
        return ""

    arabic_re = re.compile(r"[\u0600-\u06FF]")
    fixed_tokens: list[str] = []

    for token in text.split():
        if arabic_re.search(token):
            fixed_tokens.append(token[::-1])
        else:
            fixed_tokens.append(token)

    return " ".join(fixed_tokens)


def _prepare_text(raw_text: str) -> str:
    text = raw_text or ""
    normal = re.sub(r"\s+", " ", text).strip()
    reversed_arabic = _reverse_arabic_tokens(normal)

    # On garde les deux versions:
    # - normal: texte OCR original
    # - reversed_arabic: mots arabes corrigés si inversés
    return f"{normal} {reversed_arabic}".lower()


def _score_weighted(text: str, weighted_keywords: list[tuple[str, float]]) -> tuple[float, list[str]]:
    score = 0.0
    found: list[str] = []

    for keyword, weight in weighted_keywords:
        kw = keyword.lower().strip()
        if kw and kw in text:
            score += weight
            found.append(keyword)

    return score, _unique(found)


def _has_regex(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE | re.UNICODE) is not None


def detect_document_type_from_text(raw_text: str) -> TypeDetectionResult:
    """
    Détection rapide par indices forts.
    Ce n'est pas l'extraction finale.
    C'est seulement un garde-fou avant extraction complète.
    """
    compact = _prepare_text(raw_text)

    if not compact.strip():
        return TypeDetectionResult("unknown", 0.0, [])

    cin_tn_keywords = [
        ("بطاقة التعريف الوطنية", 4.0),
        ("بطاقة التعريف", 3.0),
        ("الجمهورية التونسية", 4.0),
        ("الجمهورية", 1.0),
        ("التونسية", 1.5),
        ("تونسية", 1.5),
        ("تونس", 1.0),
        ("بطاقة", 1.0),
        ("تعريف", 1.0),
        ("التعريف", 1.0),
        ("الوطنية", 1.0),
        ("الوطنيه", 1.0),
        ("الاسم", 1.0),
        ("الإسم", 1.0),
        ("اللقب", 1.0),
        ("تاريخ الولادة", 1.5),
        ("الولادة", 1.0),
        ("الولاده", 1.0),
        ("مكان الولادة", 1.5),
    ]

    id_document_keywords = [
        ("id-card", 4.0),
        ("id card", 4.0),
        ("identity card", 4.0),
        ("national identity card", 4.0),
        ("obciansky preukaz", 4.0),
        ("občiansky preukaz", 4.0),
        ("slovak republic", 3.0),
        ("slovenska republika", 3.0),
        ("personal no", 2.5),
        ("personal number", 2.5),
        ("rodné číslo", 2.5),
        ("date of expiry", 1.5),
        ("date of issue", 1.5),
        ("issued by", 1.5),
        ("surname", 0.5),
        ("given names", 0.5),
        ("nationality", 0.5),
        ("date of birth", 0.5),
        ("sex", 0.5),
    ]

    # Ne pas donner un poids fort aux champs génériques:
    # surname/date of birth existent aussi sur les cartes d'identité.
    passport_keywords = [
        ("passport", 5.0),
        ("passeport", 5.0),
        ("passport no", 4.0),
        ("passport number", 4.0),
        ("no. passport", 4.0),
        ("type p", 2.0),
        ("issuing authority", 2.0),
        ("date of expiry", 0.5),
        ("surname", 0.3),
        ("given names", 0.3),
        ("nationality", 0.3),
    ]

    invoice_keywords = [
        ("facture", 4.0),
        ("invoice", 4.0),
        ("total ttc", 3.0),
        ("total ht", 2.5),
        ("tva", 2.0),
        ("montant", 1.5),
        ("net à payer", 3.0),
        ("net a payer", 3.0),
        ("date facture", 2.0),
        ("n° facture", 2.0),
        ("numero facture", 2.0),
        ("désignation", 1.5),
        ("designation", 1.5),
        ("quantité", 1.5),
        ("quantite", 1.5),
        ("prix unitaire", 1.5),
    ]

    registre_keywords = [
        ("registre de commerce", 5.0),
        ("extrait du registre", 5.0),
        ("rccm", 4.0),
        ("rne", 3.0),
        ("matricule fiscal", 4.0),
        ("raison sociale", 3.0),
        ("forme juridique", 3.0),
        ("capital", 2.0),
        ("activité", 2.0),
        ("activite", 2.0),
        ("date début activité", 2.0),
        ("date de début d'activité", 2.0),
        ("tribunal", 2.0),
        ("greffe", 2.0),
    ]

    scores: dict[str, tuple[float, list[str]]] = {
        "cin_tn": _score_weighted(compact, cin_tn_keywords),
        "id_document": _score_weighted(compact, id_document_keywords),
        "passport": _score_weighted(compact, passport_keywords),
        "invoice": _score_weighted(compact, invoice_keywords),
        "registre_commerce": _score_weighted(compact, registre_keywords),
    }

    # MRZ passeport: très fort. Exemple: P<TUN..., P<FRA...
    if _has_regex(compact, r"\bp<[a-z0-9<]{5,}"):
        score, reasons = scores["passport"]
        scores["passport"] = (score + 8.0, _unique(reasons + ["MRZ passeport P<"]))

    # MRZ carte identité: souvent I< ou ID<..., classé id_document.
    # FIX: exige un '<' de remplissage MRZ (comme le motif passeport P<)
    # pour éviter de matcher "identité"/"identite" en faux positif.
    if _has_regex(compact, r"\bid[a-z0-9]{0,3}<[a-z0-9<]{3,}"):
        score, reasons = scores["id_document"]
        scores["id_document"] = (score + 6.0, _unique(reasons + ["MRZ carte identité"]))

    best_type = "unknown"
    best_score = 0.0
    best_reasons: list[str] = []

    for doc_type, (score, reasons) in scores.items():
        if score > best_score:
            best_type = doc_type
            best_score = score
            best_reasons = reasons

    # Anti-faux positif passport:
    # Une carte d'identité étrangère contient souvent surname/given names/date of birth.
    # On n'accepte "passport" que s'il y a un indice passeport fort.
    passport_score, passport_reasons = scores["passport"]
    passport_strong = any(
        reason.lower() in {
            "passport",
            "passeport",
            "passport no",
            "passport number",
            "no. passport",
            "mrz passeport p<",
        }
        for reason in passport_reasons
    )

    if best_type == "passport" and not passport_strong:
        scores["passport"] = (0.0, [])
        best_type = "unknown"
        best_score = 0.0
        best_reasons = []

        for doc_type, (score, reasons) in scores.items():
            if score > best_score:
                best_type = doc_type
                best_score = score
                best_reasons = reasons

    if best_score <= 0:
        return TypeDetectionResult("unknown", 0.0, [])

    confidence = min(1.0, best_score / 4.0)

    return TypeDetectionResult(
        detected_type=best_type,
        confidence=confidence,
        reasons=_unique(best_reasons),
    )


def is_type_compatible(
    selected_type: str,
    detected: TypeDetectionResult,
    *,
    min_confidence_to_block: float = 0.25,
) -> tuple[bool, str]:
    selected = normalize_document_type(selected_type)
    detected_type = normalize_document_type(detected.detected_type)

    if selected in {"auto", "custom", "unknown", ""}:
        return True, "Type automatique/custom: pas de blocage."

    if detected_type in {"unknown", "auto", ""}:
        return True, "Type non détecté avec assez de confiance: extraction autorisée."

    if selected == detected_type:
        return True, "Type sélectionné compatible avec le document."

    # Une CIN tunisienne est aussi un document d'identité.
    if selected == "id_document" and detected_type == "cin_tn":
        return True, "CIN tunisienne compatible avec document d'identité générique."

    if detected.confidence >= min_confidence_to_block:
        message = (
            f"Le type sélectionné '{selected}' ne correspond pas au document détecté "
            f"'{detected_type}'. Raisons: {', '.join(detected.reasons[:5])}."
        )
        return False, message

    return True, "Confiance de détection faible: extraction autorisée."