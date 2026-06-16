# OCR Angular UI — Arabsoft style

Interface Angular professionnelle pour remplacer les pages statiques `index.html` et `templates.html`.

## Installation
```bash
npm install
ng serve --port 4200
```

API par défaut: `http://localhost:8000`. Modifie `src/environments/environment.ts` si nécessaire.

## Build
```bash
ng build --configuration production
```

## Sécurité
Ne garde pas `dev-key-123` en production. Utilise HTTPS, CORS limité, clés API serveur, limite de taille upload et nettoyage des fichiers temporaires.
