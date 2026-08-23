"""
classer_echecs_id_number.py

Avant de faire un tri visuel image par image, vérifie automatiquement
si chaque cas où id_number manque est :
  (a) un PLANTAGE complet (erreur mémoire, réponse vide/erreur) --
      rien à voir avec la qualité d'extraction, à écarter
  (b) une VRAIE extraction partielle (d'autres champs présents,
      seul id_number manque) -- candidat réel à investiguer

Utilisation :
    python classer_echecs_id_number.py
"""
from __future__ import annotations

import json
from pathlib import Path

# ============================================================
CASE_IDS = [
    "carte17", "carte18", "carte19", "carte20", "carte21", "carte22",
    "carte24", "carte25", "carte26", "carte27", "carte28", "carte29", "carte30",
]

RESPONSES_DIR = Path("ResultatTest/responses/cin_tn")
PREFIX = "cin_tn"
# ============================================================


def classify(case_id: str) -> str:
    path = RESPONSES_DIR / f"{PREFIX}__{case_id}.json"

    if not path.is_file():
        return "FICHIER_INTROUVABLE"

    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return "JSON_INVALIDE"

    # Signature d'un plantage : pas de "fields", ou une clé "detail"
    # contenant un message d'erreur (comme carte29 vu précédemment).
    if "detail" in data and "fields" not in data:
        return "PLANTAGE (erreur/exception)"

    if not data:
        return "PLANTAGE (réponse vide)"

    fields = data.get("fields", [])
    if not fields:
        return "PLANTAGE (aucun champ)"

    # Combien de champs (hors id_number) ont une vraie valeur ?
    other_fields_with_value = sum(
        1 for f in fields
        if isinstance(f, dict) and f.get("name") != "id_number" and f.get("value")
    )

    if other_fields_with_value >= 2:
        return f"VRAIE EXTRACTION PARTIELLE ({other_fields_with_value} autres champs présents) -- À INVESTIGUER"

    return "EXTRACTION QUASI VIDE (probablement dégradée) -- suspect"


def main() -> None:
    print(f"Classement de {len(CASE_IDS)} cas...\n")

    counts = {"plantage": 0, "a_investiguer": 0, "autre": 0}

    for case_id in CASE_IDS:
        result = classify(case_id)
        print(f"{case_id:<10} -> {result}")

        if "PLANTAGE" in result or "INTROUVABLE" in result or "INVALIDE" in result:
            counts["plantage"] += 1
        elif "À INVESTIGUER" in result:
            counts["a_investiguer"] += 1
        else:
            counts["autre"] += 1

    print("\n" + "=" * 60)
    print(f"Plantages / réponses vides : {counts['plantage']}")
    print(f"Vraies extractions partielles à investiguer : {counts['a_investiguer']}")
    print(f"Autres (suspects, à revérifier) : {counts['autre']}")
    print("=" * 60)

    if counts["plantage"] >= len(CASE_IDS) // 2:
        print("\n-> La majorité de ces cas sont des plantages, pas des échecs")
        print("   d'extraction ciblés. Le motif ressemble à l'instabilité")
        print("   mémoire déjà diagnostiquée (comme pour les passeports).")
        print("   Ne pas chasser un bug id_number sur ces cas-là.")


if __name__ == "__main__":
    main()