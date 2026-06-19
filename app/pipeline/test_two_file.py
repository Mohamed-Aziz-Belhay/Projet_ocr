# -*- coding: utf-8 -*-
"""
test_two_file.py
Génère screen_preprocess_before.png et screen_preprocess_after.png
pour la figure 3.2 du rapport PFE ExtractLY.

Lancer depuis la racine du projet :
    cd C:\Users\Belha\Downloads\ocr_final_modified_refactor\ocr_final
    python test_two_file.py
"""
import sys
import os
import cv2

# ── Chemin de l'image de test ─────────────────────────────────────────────────
# ✅ r"..." obligatoire sous Windows pour éviter SyntaxError unicode escape
image_path = r'C:\Users\Belha\OneDrive\Bureau\DataSetOCR\IMAGES\carte15.jpg'

# ── Dossier de sortie (racine du projet) ──────────────────────────────────────
output_dir    = r'C:\Users\Belha\Downloads\ocr_final_modified_refactor\ocr_final'
output_before = os.path.join(output_dir, 'screen_preprocess_before.png')
output_after  = os.path.join(output_dir, 'screen_preprocess_after.png')

# ── Vérification image ────────────────────────────────────────────────────────
if not os.path.exists(image_path):
    print(f"ERREUR : image introuvable :\n  {image_path}")
    sys.exit(1)

# ── Ajouter la racine du projet au path Python ───────────────────────────────
sys.path.insert(0, output_dir)

# ── Import confirmé depuis la structure du projet ─────────────────────────────
# app/pipeline/preprocess.py existe (vérifié dans structure.txt)
from app.pipeline.preprocess import preprocess
print("Import app.pipeline.preprocess : OK")

# ── Charger l'image originale ─────────────────────────────────────────────────
img_original = cv2.imread(image_path)

if img_original is None:
    print(f"ERREUR : cv2 ne peut pas lire l'image : {image_path}")
    sys.exit(1)

print(f"Image chargée : {img_original.shape} ({img_original.dtype})")

# ── Sauvegarder AVANT prétraitement ──────────────────────────────────────────
cv2.imwrite(output_before, img_original)
print(f"Avant : {output_before}")

# ── Appliquer le prétraitement ────────────────────────────────────────────────
img_processed = preprocess(img_original)

if img_processed is None:
    print("ERREUR : preprocess() a retourné None")
    sys.exit(1)

print(f"Image traitée : {img_processed.shape} ({img_processed.dtype})")

# ── Sauvegarder APRÈS prétraitement ──────────────────────────────────────────
cv2.imwrite(output_after, img_processed)
print(f"Après : {output_after}")

print("\n=== SUCCES ===")
print("Copie ces deux fichiers dans le dossier de ton .tex :")
print(f"  {output_before}")
print(f"  {output_after}")