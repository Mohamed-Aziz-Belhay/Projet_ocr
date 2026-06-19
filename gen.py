import sys, os, cv2
sys.path.insert(0, r'C:\Users\Belha\Downloads\ocr_final_modified_refactor\ocr_final')
from app.pipeline.preprocess import preprocess
image_path = r'C:\Users\Belha\OneDrive\Bureau\DataSetOCR\IMAGES\carte15.jpg'
img = cv2.imread(image_path)
if img is None:
    print("ERREUR image introuvable")
    sys.exit(1)
cv2.imwrite('screen_preprocess_before.png', img)
print("Avant OK")
img2 = preprocess(img)
cv2.imwrite('screen_preprocess_after.png', img2)
print("Apres OK")
