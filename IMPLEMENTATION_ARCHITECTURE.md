# OCR Enterprise — Architecture cible implémentée (baseline structurée)

Cette version du projet introduit une base cohérente pour l'industrialisation du pipeline OCR, en particulier pour `cin_tn`.

## Couches
1. API / Orchestration
2. Prétraitement image
3. Détection document / routage
4. Localisation de champs
5. Multi-engine OCR
6. Resolver métier par champ
7. Workflow de revue humaine
8. Observabilité + diagnostics

## Ce qui est effectivement branché dans cette baseline
- `run_pipeline()` gère maintenant un pipeline générique et un pipeline CIN spécialisé.
- `cin_tn.yaml` décrit la stratégie, les zones fixes, les moteurs et les seuils de review.
- `field_resolver.py` apporte un scoring métier séparé du score OCR brut.
- `engine_selector.py` construit un plan de moteurs primaire / secondaire / fallback numérique.
- `cin_localizer.py` calcule les zones et peut les raffiner à partir d'ancres OCR.
- `ExtractionResponse` supporte `review_required`, `normalized_data` et `diagnostics`.

## Limites honnêtes de cette version
- Le localisateur reste majoritairement basé sur des zones fixes et des ancres simples.
- La revue humaine est exposée au niveau des statuts et diagnostics, mais pas encore avec une file de revue persistée.
- `Surya` n'est pas branché dans le pipeline principal ; la structure permet de l'ajouter plus tard.
- Les gains de qualité dépendent encore de benchmarks réels sur un dataset CIN représentatif.
