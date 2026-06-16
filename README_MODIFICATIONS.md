Ce zip contient une version modifiée du projet basée sur le dépôt fourni.

Contenu principal ajouté/modifié :
- pipeline CIN spécialisé v2 dans app/pipeline/runner.py
- modules CIN dédiés :
  - app/pipeline/cin_preprocessor.py
  - app/pipeline/cin_field_rois.py
  - app/services/cin_box_ocr.py
  - app/services/cin_spatial_extractor.py
  - app/services/cin_fuser.py
  - app/services/cin_rules.py
  - app/services/business_validation.py
- support templates passport / foreign ID :
  - app/templates/aze_passport.yaml
  - app/templates/est_id.yaml
  - app/extractors/passport_extractor.py
- registre extracteurs mis à jour : app/services/template_registry.py
- auto-detect templates enrichi : app/services/template_service.py
- settings enrichi : app/core/settings.py

Important:
- J’ai pu préparer une version cohérente à partir des fichiers fournis, mais je ne peux pas garantir un comportement parfait sans exécuter votre repo Windows complet avec vos dépendances et vos jeux de test réels.
- Les fichiers historiques suivants sont de bons candidats à passer en legacy après validation :
  - app/extractors/cin_extractor.py
  - app/pipeline/cin_localizer.py
  - app/pipeline/preprocess.py
- La priorité métier reste de réduire les faux success sur la CIN.
