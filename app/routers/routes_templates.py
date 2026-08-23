"""
app/routers/routes_templates.py
Templates OCR - CRUD PostgreSQL async.
Compatible Windows (pas d'emojis dans les logs).

CORRECTIF RG8 (rapport PFE, Chapitre 3) :
Les routes d'écriture PUT/DELETE sont désormais réservées au rôle admin
côté API. Avant ce correctif, AUCUN contrôle n'était appliqué (seule
dépendance : get_db), et main.py n'applique aucune authentification
globale aux routers — n'importe quel appelant pouvait donc modifier ou
supprimer un template via un appel direct à l'API, le seul blocage étant
le guard Angular côté frontend.

CORRECTIF SYNCHRONISATION (rapport PFE, Chapitre 6) :
PUT/DELETE écrivaient uniquement dans la table PostgreSQL ocr_templates,
sans jamais repasser par TemplateService -- le pipeline d'extraction,
qui résout ses templates depuis un cache chargé UNE FOIS au démarrage
depuis app/templates/*.yaml, ne voyait donc jamais les modifications
faites via l'éditeur d'administration tant que le service n'était pas
redémarré manuellement. Chaque écriture DB réussie déclenche désormais
un appel à TemplateService.update()/create()/delete(), qui réécrit le
fichier YAML ET rafraîchit le cache mémoire dans le même geste -- plus
aucun redémarrage nécessaire.

CORRECTIF ANTI-EMPILEMENT "extra" (22/08) :
_copy() rangeait dans la colonne "extra" TOUT champ du payload non
déclaré dans TemplateBody -- y compris, si le payload était un GET
renvoyé tel quel (cas observé avec l'éditeur d'administration), le mot-
clé "extra" lui-même et les colonnes ORM (is_active, created_at,
updated_at, usage_count). Résultat : un nouvel objet "extra" imbriqué
dans l'ancien à chaque cycle édition -> sauvegarde -> réédition, avec
roi_fields de plus en plus profondément enterré et donc invisible pour
le pipeline (qui le lit en attribut plat, getattr(template, "roi_fields",
[])). _NON_DOMAIN_KEYS exclut désormais explicitement ces clés de
l'empilement, aux deux endroits où le payload est retraité (_copy et
_sync_to_template_service), et cette dernière dépile récursivement tout
"extra" imbriqué déjà accumulé avant ce correctif.

Mécanisme (vérifié sur app/core/rbac.py) :
require_admin(user) est un simple helper qui prend un objet user et lève
une HTTPException — ce n'est PAS une dépendance FastAPI utilisable telle
quelle dans Depends(). On réutilise donc la dépendance existante
get_current_admin_user de routes_monitoring.py, qui décode le JWT,
charge l'utilisateur en base puis applique require_admin(user).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

# CORRECTIF RG8 : réutilisation de la dépendance admin du monitoring
# (JWT -> user -> require_admin). Si vous préférez éviter la dépendance
# entre routers, copiez la fonction get_current_admin_user de
# routes_monitoring.py dans app/api/deps.py et importez-la depuis là.
from app.routers.routes_monitoring import get_current_admin_user

# CORRECTIF SYNCHRONISATION : pont vers le TemplateService (YAML + cache mémoire),
# jusqu'ici jamais appelé par ce routeur.
from app.core.errors import TemplateNotFoundError
from app.schemas.template import TemplateSpec
from app.services.template_service import get_template_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["Templates"])

# Dépendance appliquée aux routes d'ÉCRITURE uniquement. Les GET restent
# accessibles (consultation/utilisation par operator et simple_user, RG8).
ADMIN_ONLY = [Depends(get_current_admin_user)]

# CORRECTIF ANTI-EMPILEMENT : clés qui ne doivent JAMAIS finir rangées dans
# la colonne/le contenu "extra" -- ni côté ORM, ni côté TemplateSpec --
# quelle que soit la forme du payload reçu (écrit à la main, ou renvoyé tel
# quel par l'éditeur d'administration depuis un GET précédent).
_NON_DOMAIN_KEYS = {"extra", "is_active", "created_at", "updated_at", "usage_count"}


# ── Schema ────────────────────────────────────────────────────────────────────
class TemplateBody(BaseModel):
    id: str = Field(..., min_length=1)
    name: str = ""
    version: str = "1.0"
    description: str | None = None
    doc_family: str | None = None
    document_type: str | None = None
    language: str | None = None
    preferred_engine: str = "auto"
    pipeline: str = "generic_template_v1"
    template_mode: str = "regex"
    fields: list[dict[str, Any]] = Field(default_factory=list)
    output_mapping: dict[str, Any] = Field(default_factory=dict)
    language_hints: list[str] = Field(default_factory=list)
    anchors_required: list[str] = Field(default_factory=list)
    postprocess_hooks: list[str] = Field(default_factory=list)
    fixed_zones: dict[str, Any] = Field(default_factory=dict)
    engines: dict[str, Any] = Field(default_factory=dict)
    field_policies: dict[str, Any] = Field(default_factory=dict)
    review_policy: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "allow"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _check_id(tid: str) -> None:
    if any(c in tid for c in ("/", "\\", "..")):
        raise HTTPException(400, "ID invalide")


def _copy(orm_obj: Any, body: TemplateBody) -> Any:
    for key in (
        "name", "version", "description", "doc_family", "document_type",
        "language", "preferred_engine", "pipeline", "template_mode",
        "fields", "output_mapping", "language_hints", "anchors_required",
        "postprocess_hooks", "fixed_zones", "engines",
        "field_policies", "review_policy",
    ):
        setattr(orm_obj, key, getattr(body, key))

    known = set(TemplateBody.model_fields.keys())

    # CORRECTIF ANTI-EMPILEMENT : on exclut explicitement _NON_DOMAIN_KEYS,
    # sinon un payload contenant déjà "extra" (GET renvoyé tel quel) ou les
    # colonnes ORM (is_active, created_at, updated_at, usage_count) les fait
    # empiler dans ce nouvel "extra", un niveau de plus à chaque cycle.
    orm_obj.extra = {
        k: v for k, v in body.model_dump().items()
        if k not in known and k not in _NON_DOMAIN_KEYS
    }
    return orm_obj


def _sync_to_template_service(body: TemplateBody) -> None:
    """
    Réécrit le fichier YAML correspondant et rafraîchit le cache mémoire
    du TemplateService à partir du même contenu que celui écrit en base.

    Défense~: dépile récursivement toute clé "extra" imbriquée (cas de
    l'éditeur d'administration, qui renvoie tel quel ce qu'un GET lui a
    fourni), et exclut systématiquement _NON_DOMAIN_KEYS du résultat --
    y compris si plusieurs niveaux d'empilement se sont déjà accumulés
    avant ce correctif (observé le 22/08~: "extra": {"extra": {...}}).
    Sans ce dépliage, roi_fields finit imbriqué et invisible pour le
    pipeline, qui le lit en attribut plat
    (getattr(template, "roi_fields", [])).

    Ne fait jamais échouer la requête HTTP appelante~: une erreur ici est
    journalisée mais ne remonte pas, pour ne jamais faire régresser le
    comportement du PUT/DELETE existant, qui reste maître de la réponse
    envoyée au client.
    """
    try:
        data = body.model_dump()

        while isinstance(data.get("extra"), dict):
            nested = data.pop("extra")
            for key, value in nested.items():
                if key not in _NON_DOMAIN_KEYS:
                    data.setdefault(key, value)

        for key in _NON_DOMAIN_KEYS:
            data.pop(key, None)

        spec = TemplateSpec(**data)
        service = get_template_service()

        try:
            service.update(spec.id, spec)
        except TemplateNotFoundError:
            service.create(spec)

        log.info("TemplateService synchronisé (YAML + cache)", extra={"template_id": spec.id})

    except Exception as exc:
        log.error(
            "Synchronisation TemplateService echouee -- le pipeline "
            "continuera de servir l'ancienne version tant que ce n'est "
            "pas resolu manuellement",
            extra={"template_id": body.id, "error": str(exc)},
        )


def _sync_delete_from_template_service(template_id: str) -> None:
    try:
        get_template_service().delete(template_id)
        log.info("TemplateService synchronise (suppression)", extra={"template_id": template_id})
    except TemplateNotFoundError:
        # Pas de fichier YAML correspondant -- rien a synchroniser, pas une erreur.
        pass
    except Exception as exc:
        log.error(
            "Synchronisation TemplateService (suppression) echouee",
            extra={"template_id": template_id, "error": str(exc)},
        )


# ── GET /templates ─────────────────────────────────────────────────────────────
@router.get("", summary="Liste tous les templates")
async def list_templates(
    search: str | None = Query(default=None),
    document_type: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    from app.db.models.template import OcrTemplate

    stmt = select(OcrTemplate)

    if search:
        q = f"%{search.lower()}%"
        stmt = stmt.where(
            OcrTemplate.template_id.ilike(q)
            | OcrTemplate.name.ilike(q)
            | OcrTemplate.document_type.ilike(q)
        )
    if document_type:
        stmt = stmt.where(OcrTemplate.document_type == document_type)
    if is_active is not None:
        stmt = stmt.where(OcrTemplate.is_active == is_active)

    stmt = (
        stmt.order_by(OcrTemplate.created_at.desc())
            .offset(skip)
            .limit(limit)
    )

    result = await db.execute(stmt)
    rows   = list(result.scalars().all())

    log.info("Templates retournes depuis PostgreSQL: %d", len(rows))
    return [r.to_summary() for r in rows]


# ── GET /templates/{template_id} ───────────────────────────────────────────────
@router.get("/{template_id}", summary="Detail d'un template")
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.db.models.template import OcrTemplate

    _check_id(template_id)

    stmt   = select(OcrTemplate).where(OcrTemplate.template_id == template_id)
    result = await db.execute(stmt)
    tmpl   = result.scalar_one_or_none()

    if not tmpl:
        raise HTTPException(404, f"Template '{template_id}' introuvable")

    tmpl.usage_count = (tmpl.usage_count or 0) + 1
    db.add(tmpl)
    return tmpl.to_dict()


# ── PUT /templates/{template_id} ───────────────────────────────────────────────
# CORRECTIF RG8 : écriture réservée au rôle admin (401/403 sinon).
@router.put(
    "/{template_id}",
    summary="Cree ou met a jour (upsert) - admin uniquement",
    dependencies=ADMIN_ONLY,
)
async def upsert_template(
    template_id: str,
    body: TemplateBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.db.models.template import OcrTemplate

    _check_id(template_id)
    body.id = template_id

    stmt     = select(OcrTemplate).where(OcrTemplate.template_id == template_id)
    result   = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        tmpl = _copy(existing, body)
        log.info("Template mis a jour : %s", template_id)
    else:
        tmpl = OcrTemplate(
            template_id=template_id,
            is_active=True,
            usage_count=0,
        )
        tmpl = _copy(tmpl, body)
        log.info("Template cree : %s", template_id)

    db.add(tmpl)
    await db.flush()
    await db.refresh(tmpl)

    # CORRECTIF SYNCHRONISATION : le pipeline d'extraction lit le YAML/cache
    # du TemplateService, pas cette table -- sans cet appel, la modification
    # ne serait jamais visible tant que le service n'est pas redémarré.
    _sync_to_template_service(body)

    return tmpl.to_dict()


# ── DELETE /templates/{template_id} ───────────────────────────────────────────
# status_code=200 (pas 204) car FastAPI < 0.100 interdit
# un response body avec 204
# CORRECTIF RG8 : suppression réservée au rôle admin (401/403 sinon).
@router.delete(
    "/{template_id}",
    status_code=200,
    summary="Supprime un template - admin uniquement",
    dependencies=ADMIN_ONLY,
)
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.db.models.template import OcrTemplate

    _check_id(template_id)

    stmt   = delete(OcrTemplate).where(OcrTemplate.template_id == template_id)
    result = await db.execute(stmt)

    if result.rowcount == 0:
        raise HTTPException(404, f"Template '{template_id}' introuvable")

    log.info("Template supprime : %s", template_id)

    # CORRECTIF SYNCHRONISATION : supprime aussi le YAML et le cache mémoire.
    _sync_delete_from_template_service(template_id)

    return {"status": "deleted", "id": template_id}