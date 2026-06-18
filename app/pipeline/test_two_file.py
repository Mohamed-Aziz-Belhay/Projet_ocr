# generate_preprocess_demo.py
# Place ce fichier dans le dossier racine du projet FastAPI
import cv2
import numpy as np
from app.pipeline.preprocess import preprocess   # adapte l'import selon ton projet

# Charge une image de test (ta CIN de test)
image_path = "test_cin.jpg"   # mets ici le chemin d'une vraie CIN de test
img_original = cv2.imread(image_path)

# Sauvegarder l'original
cv2.imwrite("screen_preprocess_before.png", img_original)
print("Avant sauvegardé")

# Appliquer le prétraitement
img_processed = preprocess(img_original)   # ta fonction preprocess

# Sauvegarder après traitement
cv2.imwrite("screen_preprocess_after.png", img_processed)
print("Après sauvegardé")