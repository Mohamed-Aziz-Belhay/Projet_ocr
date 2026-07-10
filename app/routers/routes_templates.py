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

Mécanisme (vérifié sur app/core/rbac.py) :
require_admin(user) est un simple helper qui prend un objet user et lève
une HTTPException — ce n'est PAS une dépendance FastAPI utilisable telle
quelle dans Depends(). On réutilise donc la dépendance existante
get_current_admin_user de routes_monitoring.py, qui décode le JWT,
charge l'utilisateur en base puis applique require_admin(user).

!! APRÈS INSTALLATION :
1. Import VÉRIFIÉ sur le code réel : routes_monitoring.py définit bien
   get_current_admin_user(request, db) (decode_access_token -> User ->
   require_admin) et n'importe aucun autre router — pas d'import
   circulaire. Le patch fonctionne tel quel.
2. Retester la collection Postman "Templates" :
   - PUT/DELETE avec token operator/simple_user  -> 403 Forbidden
   - PUT/DELETE sans token                        -> 401 Unauthorized
   - PUT/DELETE avec token admin                  -> comportement inchangé
   - GET (liste/détail)                           -> inchangé (RG8 :
     operators et simple_users consultent et utilisent les templates)
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

log = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["Templates"])

# Dépendance appliquée aux routes d'ÉCRITURE uniquement. Les GET restent
# accessibles (consultation/utilisation par operator et simple_user, RG8).
ADMIN_ONLY = [Depends(get_current_admin_user)]


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
    orm_obj.extra = {
        k: v for k, v in body.model_dump().items()
        if k not in known
    }
    return orm_obj


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
    return {"status": "deleted", "id": template_id}