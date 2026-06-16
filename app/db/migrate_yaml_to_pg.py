"""
app/db/migrate_yaml_to_pg.py
Migration one-shot : lit tous les fichiers YAML existants
et les insère dans PostgreSQL (table ocr_templates).

Usage :
    python -m app.db.migrate_yaml_to_pg
    python -m app.db.migrate_yaml_to_pg --dry-run
    python -m app.db.migrate_yaml_to_pg --dir app/templates
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml


async def migrate(templates_dir: str, dry_run: bool = False) -> None:
    # Import tardif pour avoir les settings chargés
    from app.db.session import AsyncSessionLocal, create_all_tables
    from app.db.models.template import OcrTemplate
    from sqlalchemy import select

    tdir = Path(templates_dir)
    if not tdir.exists():
        print(f"❌ Répertoire introuvable : {tdir}")
        sys.exit(1)

    yaml_files = sorted(tdir.glob("*.yaml"))
    if not yaml_files:
        print(f"⚠️  Aucun fichier .yaml trouvé dans {tdir}")
        return

    print(f"📂 {len(yaml_files)} fichier(s) YAML trouvé(s) dans {tdir}")
    if dry_run:
        print("🔍 Mode DRY-RUN — aucune écriture en base\n")

    # Crée les tables si elles n'existent pas encore
    if not dry_run:
        await create_all_tables()
        print("✅ Tables vérifiées/créées\n")

    created = 0
    updated = 0
    skipped = 0
    errors = 0

    async with AsyncSessionLocal() as db:
        for yaml_path in yaml_files:
            try:
                with open(yaml_path, encoding="utf-8") as f:
                    data: dict = yaml.safe_load(f) or {}

                template_id = data.get("id") or yaml_path.stem
                if not template_id:
                    print(f"  ⚠️  {yaml_path.name} → id manquant, ignoré")
                    skipped += 1
                    continue

                print(f"  → {yaml_path.name}  (id={template_id})", end="  ")

                if dry_run:
                    print("[DRY-RUN OK]")
                    continue

                # Vérifie si le template existe déjà
                stmt = select(OcrTemplate).where(
                    OcrTemplate.template_id == template_id
                )
                result = await db.execute(stmt)
                existing = result.scalar_one_or_none()

                orm = OcrTemplate.from_template_spec(data)

                if existing:
                    # Met à jour les colonnes
                    for col in [
                        "name", "version", "description", "doc_family",
                        "document_type", "language", "preferred_engine",
                        "pipeline", "template_mode", "fields", "output_mapping",
                        "language_hints", "anchors_required", "postprocess_hooks",
                        "fixed_zones", "engines", "field_policies", "review_policy",
                        "extra",
                    ]:
                        setattr(existing, col, getattr(orm, col))
                    db.add(existing)
                    print("[mis à jour]")
                    updated += 1
                else:
                    db.add(orm)
                    print("[créé]")
                    created += 1

            except Exception as exc:
                print(f"\n  ❌ Erreur sur {yaml_path.name} : {exc}")
                errors += 1

        if not dry_run:
            await db.commit()

    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Migration terminée
  ✅ Créés   : {created}
  🔄 Mis à jour : {updated}
  ⚠️  Ignorés : {skipped}
  ❌ Erreurs  : {errors}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migre les templates YAML vers PostgreSQL"
    )
    parser.add_argument(
        "--dir",
        default="app/templates",
        help="Dossier contenant les fichiers .yaml (défaut: app/templates)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule la migration sans écrire en base",
    )
    args = parser.parse_args()
    asyncio.run(migrate(args.dir, args.dry_run))


if __name__ == "__main__":
    main()