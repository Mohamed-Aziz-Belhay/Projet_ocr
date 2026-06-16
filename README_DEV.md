# OCR Microservice Enterprise — Guide de démarrage (sans Docker)

## Installation

```bash
pip install fastapi uvicorn[standard] pydantic pydantic-settings python-dotenv
pip install python-multipart pyyaml PyJWT cryptography
pip install sqlalchemy[asyncio] aiosqlite alembic
pip install opencv-python-headless pillow pymupdf numpy langdetect
pip install prometheus-fastapi-instrumentator httpx

# Moteurs OCR (choisir selon besoin)
pip install paddleocr      # pour arabe/RTL — RECOMMANDÉ pour CIN
pip install pytesseract    # + installer Tesseract binaire
pip install easyocr        # manuscrite/photos dégradées
```

### Tesseract sur Windows
Télécharger : https://github.com/UB-Mannheim/tesseract/wiki
Ajouter au PATH : `C:\Program Files\Tesseract-OCR\`
Packs de langue : `tesseract-ocr-ara`, `tesseract-ocr-fra`

## Démarrage

```bash
mkdir -p data/uploads data/results
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Swagger UI → http://localhost:8000/docs
Clé API de test → `dev-key-123`

---

## Tester avec les documents réels

### CIN tunisienne (carte1.jpg, carte5.jpg)
```bash
curl -X POST http://localhost:8000/extract \
  -H "X-API-Key: dev-key-123" \
  -F "file=@carte1.jpg" \
  -F "template_id=cin_tn" \
  -F "engine=paddle"
```
Résultat attendu pour carte1.jpg :
- id_number: "09275830"
- last_name: "نور الدين"
- first_name: "صدقي"
- birth_date: "1988-07-06"
- birth_place: "القلعة الكبرى"

### Facture tunisienne (factureTN.jpg)
```bash
curl -X POST http://localhost:8000/extract \
  -H "X-API-Key: dev-key-123" \
  -F "file=@factureTN.jpg" \
  -F "template_id=invoice_tn" \
  -F "engine=tesseract"
```
Résultat attendu :
- invoice_number: "FA20BJ001"
- total_ttc: "1 000,000 DT"
- vat_rate: "13"

### Registre de commerce (registre-de-commerce2.jpg)
```bash
curl -X POST http://localhost:8000/extract \
  -H "X-API-Key: dev-key-123" \
  -F "file=@registre-de-commerce2.jpg" \
  -F "template_id=registre_commerce_tn" \
  -F "engine=paddle"
```

### Auto-détection (sans template_id)
```bash
curl -X POST http://localhost:8000/extract \
  -H "X-API-Key: dev-key-123" \
  -F "file=@carte1.jpg"
```
Le système détecte automatiquement `cin_tn` grâce aux ancres arabes.

---

## Templates disponibles

| ID                     | Document                     | Moteur conseillé |
|------------------------|------------------------------|------------------|
| `cin_tn`               | CIN tunisienne               | `paddle`         |
| `invoice_tn`           | Facture tunisienne           | `tesseract`      |
| `registre_commerce_tn` | Registre de commerce TN      | `paddle`         |
| `invoice_generic`      | Facture générique            | `tesseract`      |
| `receipt_generic`      | Reçu générique               | `tesseract`      |

```bash
# Lister tous les templates
curl http://localhost:8000/templates -H "X-API-Key: dev-key-123"
```

---

## Spécificités CIN tunisienne

### Pourquoi les labels sont en arabe ?
La CIN tunisienne n'a PAS de labels en français.
Les labels sont : `اللقب` (nom), `الاسم` (prénom), `تاريخ الولادة` (date naissance), `مكانها` (lieu)

### Format des dates
Les dates utilisent les mois arabes tunisiens (translittération) :
- `جانفي` = Janvier
- `جويلية` = Juillet
- `جانفي` = Janvier
Le système convertit automatiquement vers ISO 8601 (YYYY-MM-DD).

### Ordre RTL
Sur la CIN, les labels sont à DROITE et les valeurs à GAUCHE :
```
نور الدين  اللقب
صدقي       الاسم
```
L'extracteur gère les deux ordres possibles selon comment PaddleOCR lit le texte.

---

## Déboguer un champ null

1. **Activer les logs DEBUG** dans `.env` : `LOG_LEVEL=DEBUG`
2. **Regarder `raw_text`** dans la réponse : contient le texte OCR brut
3. **Vérifier que le moteur reconnaît le script** : pour arabe → `engine=paddle`
4. **Tester sans template** pour voir le texte brut OCR :
```bash
curl -X POST http://localhost:8000/extract \
  -H "X-API-Key: dev-key-123" \
  -F "file=@carte1.jpg" \
  -F "engine=paddle"
# → La réponse contient raw_text avec ce que PaddleOCR a reconnu
```
5. Si `raw_text` est vide → problème de moteur OCR (paddle non installé ?)
6. Si `raw_text` contient du texte mais les champs sont null → problème de patterns



## Surya (optionnel, expérimental)

Installation : `pip install surya-ocr`

- utile pour benchmark layout/OCR multilingue
- non requis pour démarrer l'application
- activable avec `ENABLE_SURYA_EXPERIMENTAL=true`
