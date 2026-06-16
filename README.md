# OCR Microservice Enterprise

**Projet de Fin d'Études** — Extraction intelligente de documents par OCR.

## Démarrage rapide (sans Docker)

### 1. Prérequis système
```bash
# Python 3.11
python --version

# Tesseract OCR (Windows)
# Télécharger : https://github.com/UB-Mannheim/tesseract/wiki
# Ajouter au PATH : C:\Program Files\Tesseract-OCR\

# Tesseract (Linux)
sudo apt-get install tesseract-ocr tesseract-ocr-ara tesseract-ocr-fra
```

### 2. Installation Python
```bash
pip install -r requirements.txt
```

> **Note moteurs :** installez uniquement ce dont vous avez besoin :
> - `pip install paddlepaddle paddleocr` — pour arabe/RTL
> - `pip install pytesseract` — pour PDF propres
> - `pip install easyocr` — pour manuscrit

### 3. Lancer le serveur
```bash
# Créer les dossiers de données
mkdir -p data/uploads data/results

# Démarrer
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Accéder à l'application

| URL | Description |
|-----|-------------|
| http://localhost:8000/ | Interface web (comme i2ocr.com) |
| http://localhost:8000/docs | Swagger UI — documentation API |
| http://localhost:8000/health | Statut serveur + moteurs |

**Clé API de développement :** `dev-key-123`

---

## Tester avec les vrais documents

### CIN tunisienne
```bash
curl -X POST http://localhost:8000/extract \
  -H "X-API-Key: dev-key-123" \
  -F "file=@carte1.jpg" \
  -F "template_id=cin_tn" \
  -F "engine=paddle"
```
Résultat attendu :
```json
{
  "status": "success",
  "fields": [
    {"name": "id_number",   "value": "09275830"},
    {"name": "last_name",   "value": "نور الدين"},
    {"name": "first_name",  "value": "صدقي"},
    {"name": "birth_date",  "value": "1988-07-06"},
    {"name": "birth_place", "value": "القلعة الكبرى"}
  ]
}
```

### Facture tunisienne
```bash
curl -X POST http://localhost:8000/extract \
  -H "X-API-Key: dev-key-123" \
  -F "file=@factureTN.jpg" \
  -F "template_id=invoice_tn" \
  -F "engine=tesseract"
```

### Registre de commerce
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
# Le système détecte automatiquement cin_tn
```

---

## Templates disponibles

| ID | Document | Moteur | Champs |
|----|----------|--------|--------|
| `cin_tn` | CIN Tunisienne | PaddleOCR | 5 |
| `invoice_tn` | Facture tunisienne | Tesseract | 8 |
| `registre_commerce_tn` | Registre de commerce TN | PaddleOCR | 8 |
| `invoice_generic` | Facture générique | Auto | 8 |
| `receipt_generic` | Reçu générique | Auto | 7 |

---

## Architecture

```
app/
├── main.py                     ← FastAPI + UI statique sur /
├── static/index.html           ← Interface web (i2ocr style)
├── engines/
│   ├── paddle_engine.py        ← PaddleOCR 2.x ET 3.x (auto-adapté)
│   ├── tesseract_engine.py     ← Tesseract 5
│   └── easyocr_engine.py       ← EasyOCR
├── pipeline/
│   ├── runner.py               ← Orchestration OCR → extraction
│   ├── field_extractors.py     ← Regex + ancres RTL/LTR
│   ├── preprocess.py           ← Déskew, débruitage, upscale
│   └── lang_detect.py          ← Détection langue
├── extractors/
│   └── cin_extractor.py        ← Extracteur spécialisé CIN (ligne par ligne)
├── templates/                  ← Configurations YAML des documents
│   ├── cin_tn.yaml
│   ├── invoice_tn.yaml
│   └── registre_commerce_tn.yaml
├── services/
│   ├── engine_selector.py      ← Sélection intelligente du moteur
│   └── template_service.py     ← Chargement + auto-détection
└── utils/
    └── date_validation.py      ← Dates arabes : "06 جويلية 1988" → 1988-07-06
```

---

## Résultats des tests

| Test | Résultat |
|------|----------|
| Syntaxe Python (92 fichiers) | ✅ 0 erreur |
| Templates YAML (7 fichiers) | ✅ tous valides |
| Dates arabes tunisiennes | ✅ 5/5 |
| CIN carte1.jpg (simulé) | ✅ 5/5 champs |
| CIN carte5.jpg (simulé) | ✅ 5/5 champs |
| Facture TN — champs critiques | ✅ 3/3 |
| Registre commerce — champs critiques | ✅ 6/6 |
| Auto-détection de template | ✅ 3/3 |

---

## Variables d'environnement clés

| Variable | Défaut | Description |
|----------|--------|-------------|
| `DEFAULT_ENGINE` | `paddle` | Moteur par défaut |
| `DATABASE_URL` | SQLite | `sqlite+aiosqlite:///./data/dev.db` |
| `ALLOWED_API_KEYS` | `["dev-key-123"]` | Clés autorisées |
| `TEMPLATES_DIR` | `app/templates` | Répertoire des templates |
| `SWIN_MODEL_PATH` | vide | Chemin modèle Swin (Phase D) |

---

## Phase D — Swin Transformer

Le classifieur Swin est configuré mais nécessite un modèle entraîné.

```bash
# Préparer dataset MidV2020
python app/models/swin/train_swin.py prepare \
  --midv_dir ./data/midv2020 \
  --output_dir ./data/midv2020_processed

# Entraîner
python app/models/swin/train_swin.py train \
  --data_dir ./data/midv2020_processed \
  --output_dir ./models/swin_doc_classifier \
  --epochs 15

# Activer dans .env
# SWIN_MODEL_PATH=./models/swin_doc_classifier
```

Statut Swin visible sur `GET /health` → champ `swin_classifier`.


## Surya (optionnel, expérimental)

Installation : `pip install surya-ocr`

- utile pour benchmark layout/OCR multilingue
- non requis pour démarrer l'application
- activable avec `ENABLE_SURYA_EXPERIMENTAL=true`
