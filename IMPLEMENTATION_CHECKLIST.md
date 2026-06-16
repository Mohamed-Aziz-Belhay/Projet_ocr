# Checklist d'implémentation / stabilisation

## Déjà intégré dans cette baseline
- [x] Pipeline `cin_specialized_v1`
- [x] Zones fixes + raffinement léger par ancres
- [x] Paddle primaire / EasyOCR fallback / Tesseract numérique
- [x] Scoring métier par champ
- [x] Statut `review_required`
- [x] Diagnostics détaillés par moteur / champ
- [x] Template `cin_tn` enrichi

## À finaliser ensuite
- [ ] Dataset benchmark versionné CIN
- [ ] File de revue humaine persistée
- [ ] Seuils calibrés par statistiques réelles
- [ ] Localisateur entraînable
- [ ] UI dédiée pour revue / correction manuelle
- [ ] Intégration optionnelle de Surya en benchmark
