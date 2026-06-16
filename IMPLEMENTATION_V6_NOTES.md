# V6 – comparatif séparé des moteurs + sélection par champ

## Changements principaux
- Exécution séparée des moteurs OCR pour `cin_tn` en mode `engine=auto`
- Sélection du meilleur candidat **par champ** (`id_number`, `last_name`, `first_name`, `birth_date`, `birth_place`)
- Renforcement des filtres métier pour rejeter les faux positifs contenant des labels (`بطاقة`, `التعريف`, `الجمهورية`, etc.)
- `EasyOCR` réparé et normalisé pour l'arabe (`ar` + `en`)
- Conservation de `Swin` pour la classification/routage et les scripts MIDV2020 déjà présents dans `app/models/swin/`

## Limites honnêtes
- Cette version structure mieux la sélection des résultats, mais ne garantit pas une extraction parfaite.
- Un vrai gain durable nécessite un benchmark sur vos images et, si possible, un entraînement/ajustement sur votre dataset MIDV2020.
- `Swin` aide surtout pour la **classification/routage/layout**, pas comme OCR texte direct.
