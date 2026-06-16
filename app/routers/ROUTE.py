"""
app/routers/ROUTES_EXTRACT_SECURITY_SNIPPET.py

A intégrer dans routes_extract.py pour bloquer l'extraction côté backend.

L'objectif:
- non connecté => 401
- viewer/simple_user => 403
- operator/admin => OK

Tu dois ajouter cette vérification au début de POST /extract et /extract/async,
avant de lancer le traitement OCR.
"""

# Imports à ajouter dans routes_extract.py:
#
# from app.core.rbac import require_extract_permission
# from app.core.security import decode_access_token
# from app.db.models.user import User
# from app.db.session import get_db
# from sqlalchemy import select
# from fastapi import Header


# Helper possible:
#
# async def _current_user_from_authorization(
#     authorization: str | None,
#     db: AsyncSession,
# ) -> User:
#     if not authorization or not authorization.lower().startswith("bearer "):
#         raise HTTPException(
#             status_code=401,
#             detail="Vous devez vous connecter pour lancer une extraction.",
#         )
#
#     token = authorization.split(" ", 1)[1].strip()
#     payload = decode_access_token(token)
#     user_id = payload.get("sub")
#
#     if not user_id:
#         raise HTTPException(status_code=401, detail="Invalid token subject")
#
#     result = await db.execute(select(User).where(User.id == str(user_id)))
#     user = result.scalar_one_or_none()
#
#     require_extract_permission(user)
#
#     return user


# Dans ta route extract_sync ajoute les paramètres:
#
# authorization: str | None = Header(None, alias="Authorization"),
# db: AsyncSession = Depends(get_db),
#
# Puis au début de la fonction:
#
# current_user = await _current_user_from_authorization(authorization, db)
#
# Après, tu peux utiliser current_user.id pour user_id dans extraction_history si besoin.
