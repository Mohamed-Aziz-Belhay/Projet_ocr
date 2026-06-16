"""
app/routers/ROUTES_EXTRACT_ROLE_PATCH.py

Patch a integrer dans app/routers/routes_extract.py.

Objectif:
- Bloquer le lancement reel de l'extraction si l'utilisateur n'est pas connecte.
- Autoriser l'extraction seulement pour admin, operator valide, simple_user.
- Refuser visiteur non connecte, viewer, operator non valide/is_active=False.

Important:
Ce fichier est un PATCH explicatif. Il ne doit pas etre declare comme router FastAPI.
Copie les imports, le helper et les blocs de verification dans ton vrai routes_extract.py.
"""

# ---------------------------------------------------------------------
# 1) Imports a ajouter dans app/routers/routes_extract.py
# ---------------------------------------------------------------------

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.core.rbac import require_extract_permission
from app.db.models.user import User


# ---------------------------------------------------------------------
# 2) Helper a ajouter dans app/routers/routes_extract.py
# ---------------------------------------------------------------------

async def _current_user_from_request_or_none(
    request: Request,
    db: AsyncSession,
) -> User | None:
    """
    Retourne l'utilisateur courant a partir du Bearer token.

    Retourne None si:
    - pas de Authorization header
    - token absent
    - token invalide
    - user inexistant

    Ne verifie pas directement le role ici.
    La verification du role se fait avec:
        require_extract_permission(current_user)
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")

    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None

    token = auth_header.split(" ", 1)[1].strip()

    if not token:
        return None

    try:
        payload = decode_access_token(token)
    except Exception:
        return None

    user_id = payload.get("sub")

    if not user_id:
        return None

    result = await db.execute(select(User).where(User.id == str(user_id)))

    return result.scalar_one_or_none()


# ---------------------------------------------------------------------
# 3) A ajouter dans la route POST /extract
# ---------------------------------------------------------------------

"""
Dans la signature de ta route /extract, ajoute db:

async def extract_sync(
    http_request: Request,
    tenant: TenantDep,
    file: UploadFile = File(...),
    request: ExtractionRequest = Depends(_parse_request),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    current_user = await _current_user_from_request_or_none(http_request, db)

    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Vous devez vous connecter pour lancer une extraction.",
        )

    require_extract_permission(current_user)

    tenant.check_quota_jobs()

    ...
"""


# ---------------------------------------------------------------------
# 4) A ajouter dans la route POST /extract/async
# ---------------------------------------------------------------------

"""
Si ta route async n'a pas encore http_request, ajoute-le:

async def extract_async(
    http_request: Request,
    tenant: TenantDep,
    file: UploadFile = File(...),
    request: ExtractionRequest = Depends(_parse_request),
    storage: StorageService = Depends(get_storage_service),
    db: AsyncSession = Depends(get_db),
):
    current_user = await _current_user_from_request_or_none(http_request, db)

    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Vous devez vous connecter pour lancer une extraction.",
        )

    require_extract_permission(current_user)

    tenant.check_quota_jobs()

    ...
"""


# ---------------------------------------------------------------------
# Resultat attendu avec le rbac.py 4 roles
# ---------------------------------------------------------------------

"""
visiteur non connecte      -> 401
viewer                     -> 403
operator is_active=False   -> 403
simple_user                -> autorise
operator valide            -> autorise
admin                      -> autorise
"""
