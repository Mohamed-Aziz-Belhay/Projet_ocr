"""
triage_id_number_manquant.py

Trie les cas CIN où id_number a échoué, en distinguant :
  (a) le numéro est visible sur l'image mais n'a pas été extrait
      -> candidat à un vrai correctif de code
  (b) le numéro est caché/flou/coupé/absent de l'image
      -> pas un bug, rien à corriger, juste une limite du jeu de données

Ouvre chaque image concernée une par une (visualiseur par défaut de
Windows), vous demandez juste de répondre par o/n dans le terminal.
À la fin, écrit un résumé clair avec seulement les cas à investiguer.

Utilisation :
    python triage_id_number_manquant.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

# ============================================================
#  CONFIGURATION — à adapter si vos chemins diffèrent
# ============================================================

# Le résumé de la campagne CIN d'origine (celle déjà citée dans le
# rapport : 23,3 % / 73,3 %). Ajustez le nom si le vôtre est différent.
RESULTS_FILE = Path("ResultatTest/cin_tn_results.json")

IMAGES_FOLDER = Path(r"C:\Users\Belha\OneDrive\Bureau\DataSetOCR\IMAGES")

# Nom du champ à trier
TARGET_FIELD = "id_number"

OUTPUT_FILE = Path("triage_id_number_resultat.json")

# ============================================================


def load_results(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        print(f"ERREUR : fichier introuvable -> {path}")
        print("Adaptez RESULTS_FILE en haut du script si le nom diffère.")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Supporte soit une liste directe, soit {"results": [...]}
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    if isinstance(data, list):
        return data

    print("ERREUR : structure JSON inattendue (ni liste, ni clé 'results').")
    sys.exit(1)


def find_image(case_id: str) -> Path | None:
    """Cherche l'image correspondant à un case_id (ex. 'cin_tn__carte7' -> carte7.jpg)."""
    # Extrait juste le nom de fichier depuis le case_id (dernière partie après __)
    stem = case_id.split("__")[-1] if "__" in case_id else case_id

    for ext in (".jpg", ".jpeg", ".png"):
        candidate = IMAGES_FOLDER / f"{stem}{ext}"
        if candidate.is_file():
            return candidate

    return None


def open_image(path: Path) -> None:
    try:
        os.startfile(str(path))  # Windows uniquement
    except AttributeError:
        # Repli si jamais lancé hors Windows
        subprocess.run(["xdg-open", str(path)], check=False)


def main() -> None:
    # Liste confirmée par classer_echecs_id_number.py : ces 10 cas ont
    # une vraie extraction partielle (id_number seul en échec, tous les
    # autres champs présents) -- contrairement à carte27/28/29, qui ont
    # planté (erreur mémoire) et sont volontairement exclus ici.
    candidate_ids = [
        "carte17", "carte18", "carte19", "carte20", "carte21",
        "carte22", "carte24", "carte25", "carte26", "carte30",
    ]

    candidates = [{"case_id": f"cin_tn__{cid}", "critical_missing": "id_number:missing_field"} for cid in candidate_ids]

    print(f"{len(candidates)} cas confirmés à trier visuellement.\n")

    print("Pour chaque image, elle va s'ouvrir dans votre visualiseur par défaut.")
    print(f"Répondez : le numéro '{TARGET_FIELD}' est-il visible et lisible sur l'image ?")
    print("  o = oui, visible et lisible (candidat à un vrai bug)")
    print("  n = non, caché/flou/coupé/absent (pas un bug)")
    print("  s = sauter ce cas (image introuvable ou incertain)\n")
    input("Appuyez sur Entrée pour commencer...")

    triage: List[Dict[str, Any]] = []

    for i, case in enumerate(candidates, start=1):
        case_id = case.get("case_id", "?")
        image_path = find_image(case_id)

        print(f"\n[{i}/{len(candidates)}] {case_id}")

        if image_path is None:
            print(f"  Image introuvable pour ce cas -- sauté.")
            triage.append({"case_id": case_id, "reponse": "image_introuvable"})
            continue

        open_image(image_path)
        reponse = input("  Numéro visible et lisible ? (o/n/s) : ").strip().lower()

        triage.append({
            "case_id": case_id,
            "image": str(image_path),
            "reponse": reponse,
            "critical_missing": case.get("critical_missing", ""),
        })

    # ── Résumé final ────────────────────────────────────────
    visibles = [t for t in triage if t["reponse"] == "o"]
    caches = [t for t in triage if t["reponse"] == "n"]
    sautes = [t for t in triage if t["reponse"] not in ("o", "n")]

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(triage, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"RÉSUMÉ ({len(triage)} cas triés)")
    print("=" * 60)
    print(f"Numéro VISIBLE mais non extrait (à investiguer) : {len(visibles)}")
    for t in visibles:
        print(f"   - {t['case_id']}")
    print(f"\nNuméro caché/absent (pas un bug) : {len(caches)}")
    print(f"Sautés/incertains : {len(sautes)}")
    print(f"\nDétail complet écrit dans : {OUTPUT_FILE.resolve()}")
    print("=" * 60)

    if visibles:
        print("\nPour les cas 'visibles', envoyez-moi leur JSON complet")
        print("(ResultatTest/responses/cin_tn/<case_id>.json) + l'image.")


if __name__ == "__main__":
    main()