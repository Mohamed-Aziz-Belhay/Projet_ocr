# Swin Transformer — Guide complet d'utilisation

## Vue d'ensemble

Le classificateur Swin est intégré en **Phase D** du pipeline OCR.
Il analyse visuellement l'image du document pour détecter sa famille
avant l'extraction des champs, ce qui améliore la sélection du moteur
et du template.

```
Image → Swin (visual)  ─┐
                         ├─ Fusion → doc_family → template → OCR → champs
OCR text → Heuristiques ─┘
```

---

## Workflow complet

### Étape 1 — Télécharger MidV2020

```
https://github.com/jsheinster/midv2020
Ou : https://visa.skoltech.ru/datasets/midv2020
```

Structure attendue après extraction :
```
data/midv2020/
  images/
    id_card/
      alb01/ flat/*.jpg  folded/*.jpg
      aze01/ ...
    driver_license/
    passport/
```

### Étape 2 — Ajouter vos documents custom (recommandé)

Pour les classes non couvertes par MidV2020 (factures, registres) :

```
data/custom_docs/
  invoice/           ← 50+ images de factures tunisiennes
  business_registry/ ← 50+ images de registres de commerce
  receipt/           ← 50+ images de reçus
```

Plus vous avez d'images par classe, meilleure sera la précision.
**Minimum recommandé : 50 images par classe.**

### Étape 3 — Préparer le dataset

```bash
python app/models/swin/prepare_midv2020.py \
    --midv_dir   ./data/midv2020 \
    --custom_dir ./data/custom_docs \
    --output_dir ./data/midv2020_processed \
    --frames     12 \
    --val_split  0.15 \
    --test_split 0.10
```

Résultat :
```
data/midv2020_processed/
  train/  id_document/  invoice/  business_registry/  receipt/
  val/    ...
  test/   ...
  manifest.json
```

### Étape 4 — Entraîner

```bash
# Standard (GPU recommandé, ~30min)
python app/models/swin/train_swin.py \
    --data_dir ./data/midv2020_processed \
    --output   ./models/swin_doc_classifier \
    --epochs   15 \
    --batch    16

# Sur CPU uniquement (~2-3h)
python app/models/swin/train_swin.py \
    --data_dir ./data/midv2020_processed \
    --output   ./models/swin_doc_classifier \
    --epochs   10 \
    --batch    8

# Mode rapide : geler le backbone (backbone frozen, head only)
python app/models/swin/train_swin.py \
    --data_dir ./data/midv2020_processed \
    --output   ./models/swin_doc_classifier \
    --epochs   5 --freeze
```

### Étape 5 — Activer dans .env

```
SWIN_MODEL_PATH=./models/swin_doc_classifier
SWIN_CONFIDENCE_THRESHOLD=0.75
```

Redémarrer uvicorn, ou appeler `POST /swin/reload`.

### Étape 6 — Évaluer

```bash
python app/models/swin/evaluate_swin.py \
    --model_dir ./models/swin_doc_classifier \
    --test_dir  ./data/midv2020_processed/test \
    --output    app/evaluation/reports/swin_eval.json
```

---

## Vérification

```bash
# Statut via API
curl http://localhost:8000/swin/status -H "X-API-Key: dev-key-123"

# Tester une image
curl -X POST http://localhost:8000/swin/predict \
  -H "X-API-Key: dev-key-123" \
  -F "file=@carte1.jpg"

# Rapport d'entraînement
curl http://localhost:8000/swin/training-report -H "X-API-Key: dev-key-123"
```

---

## Résultats attendus avec MidV2020

| Classe | Précision attendue | Notes |
|---|---|---|
| id_document | >92% | MidV2020 couvre bien (ID cards, passeports) |
| invoice | ~85% | Dépend de vos images custom |
| business_registry | ~80% | Dépend de vos images custom |
| receipt | ~82% | Dépend de vos images custom |

**Accuracy globale visée : >88% avec 15 epochs**

---

## Résolution de problèmes

### `torch` ou `transformers` non installé
```bash
pip install torch transformers torchvision
```

### Mémoire insuffisante (OOM)
Réduire `--batch` à 4 ou 8, ou utiliser `--freeze`.

### Modèle prédit toujours "unknown"
Vérifier `SWIN_CONFIDENCE_THRESHOLD` dans `.env`. Réduire à `0.60` si trop restrictif.

### Mauvaise précision
1. Augmenter le nombre d'images custom
2. Utiliser `--all_conditions` dans la préparation
3. Augmenter `--epochs` à 20
