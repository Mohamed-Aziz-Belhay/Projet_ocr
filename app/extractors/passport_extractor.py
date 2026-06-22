from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.extractors.base import BaseExtractor, FieldOutput
from app.services.mrz_parser import parse_td3_passport_mrz, extract_td3_mrz_lines

# ── Recherche du numéro de passeport hors MRZ (inchangé) ──────────────────
# NOTE (correction encodage) : le fragment turc "pasportun n[o°]mrasi" était
# corrompu (caractères perdus). Terme correct : "pasaport numarası"
# (= "numéro de passeport" en turc). Le [ıi] gère à la fois le "ı" turc
# (i sans point) et son équivalent ASCII degradé "i".
_PASSPORT_NO_RE = re.compile(
    r"(?:passport\s*no|passport\s*n[o°]|pasaport\s*numaras[ıi]|document\s*no)\s*[:#]?\s*([A-Z0-9]{6,12})",
    re.I,
)

# ── CORRECTIF 1 : ancrage MRZ ──────────────────────────────────────────────
# Avant : re.findall(r"[A-Z0-9<]{30,44}", text) matchait N'IMPORTE QUELLE
# séquence alphanumérique de 30-44 caractères (numéro de référence postal,
# texte bruité, etc.) — aucune garantie que ce soit réellement une MRZ.
#
# Maintenant : on réutilise extract_td3_mrz_lines() de mrz_parser.py — la
# même fonction qui valide déjà les lignes pour passport_extraction_service.
# Pas de logique de détection MRZ dupliquée entre les deux pipelines.


class PassportExtractor(BaseExtractor):
    doc_family = "id_document"
    variant_id = "passport_generic"

    def can_handle(self, doc_family: str, variant_id: Optional[str] = None) -> bool:
        if doc_family != "id_document":
            return False
        return bool(variant_id and "passport" in variant_id)

    def extract(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[FieldOutput]:
        fields: List[FieldOutput] = []

        # ── CORRECTIF 1 (suite) : MRZ ancrée, pas un regex générique ───────
        mrz_lines, _mrz_line_errors = extract_td3_mrz_lines(text or "")

        # ── CORRECTIF 2 : sexe dérivé de la MRZ validée, pas d'un regex
        # cherchant 'M' ou 'F' n'importe où dans le texte ──────────────────
        #
        # Avant :
        #   _SEX_RE = re.compile(r"([MF])|([QK])/(?:[FM])", re.I)
        # → matchait la première lettre M/F trouvée dans TOUT le texte OCR,
        #   d'où le faux positif observé : raw_text "m?" -> gender "M"
        #   avec confidence 0.75, sur un document qui n'était même pas
        #   un passeport (cf. result_invoice_tn_v3.json).
        #
        # Maintenant : on ne déduit le sexe (et les autres champs MRZ) que
        # si parse_td3_passport_mrz() valide la structure + le checksum.
        # Sinon, le champ reste None plutôt que de fabriquer une valeur
        # avec une confiance artificiellement élevée.
        passport_number = None
        surname = None
        given_names = None
        dob = None
        exp = None
        sex = None
        mrz_confidence = 0.0

        if len(mrz_lines) == 2:
            mrz_text = f"{mrz_lines[0]}\n{mrz_lines[1]}"
            parsed = parse_td3_passport_mrz(mrz_text)

            if parsed.valid:
                # MRZ structurellement valide ET checksum correct :
                # on peut faire confiance aux champs.
                passport_number = parsed.document_number
                surname = parsed.surname
                given_names = parsed.given_names
                dob = parsed.birth_date
                exp = parsed.expiry_date
                sex = parsed.gender
                mrz_confidence = 0.9  # alignée sur le niveau de confiance
                                       # utilisé par passport_extraction_service
            # Si parsed.valid est False, on ne remonte AUCUN champ MRZ :
            # mieux vaut "non trouvé" qu'une valeur fausse avec confiance
            # élevée. Le champ déclenchera naturellement une révision.

        # Fallback texte libre (hors MRZ) uniquement pour le numéro de
        # passeport — ce chemin n'a pas de checksum, donc confidence plus
        # basse et non court-circuité par une valeur déjà trouvée en MRZ.
        if not passport_number:
            m = _PASSPORT_NO_RE.search(text or "")
            if m:
                passport_number = m.group(1).strip().upper()

        def make(name, value, conf=0.0, raw=None):
            return self._make_field(
                name, value, conf, raw,
                validated=value is not None,
                error=None if value is not None else "Not found",
            )

        fields.append(make(
            "passport_number", passport_number,
            mrz_confidence if (passport_number and mrz_lines) else (0.92 if passport_number else 0.0),
            passport_number,
        ))
        fields.append(make("surname", surname, mrz_confidence, surname))
        fields.append(make("given_names", given_names, mrz_confidence, given_names))
        fields.append(make("date_of_birth", dob, mrz_confidence, dob))
        fields.append(make("date_of_expiry", exp, mrz_confidence, exp))
        fields.append(make("sex", sex, mrz_confidence, sex))

        # On ne remonte les lignes MRZ brutes QUE si la validation a réussi.
        # extract_td3_mrz_lines() a un dernier recours interne (chercher un
        # simple "P" isolé si "P<" n'est pas trouvé) qui peut accrocher du
        # texte non-MRZ (ex: "PT? iull..." dans un ticket postal). Tant que
        # parse_td3_passport_mrz() n'a pas validé la structure+checksum,
        # mieux vaut ne rien remonter que du texte poubelle avec validated=True.
        if mrz_confidence and len(mrz_lines) >= 1:
            fields.append(make("mrz_line_1", mrz_lines[0], mrz_confidence, mrz_lines[0]))
        if mrz_confidence and len(mrz_lines) >= 2:
            fields.append(make("mrz_line_2", mrz_lines[1], mrz_confidence, mrz_lines[1]))

        return fields