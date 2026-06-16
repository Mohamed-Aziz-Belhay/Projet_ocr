"""
Patch pour app/routers/routes_users.py

Objectif:
- Admin voit les comptes pending operator via is_active=false.
- Admin valide un operator avec PATCH /users/{id} {"is_active": true}
- Admin peut creer role admin/operator/viewer.
"""

# Dans ta route update_user, assure-toi que le payload accepte:
# role?: "admin" | "operator" | "viewer"
# is_active?: bool
# is_superuser?: bool

async def apply_user_update(user, payload):
    if getattr(payload, "full_name", None) is not None:
        user.full_name = payload.full_name

    if getattr(payload, "role", None) is not None:
        user.role = payload.role

    if getattr(payload, "is_active", None) is not None:
        user.is_active = payload.is_active

    if getattr(payload, "is_superuser", None) is not None:
        user.is_superuser = payload.is_superuser

    return user


# Ajoute une route utile:
#
# @router.get("/users/pending")
# async def pending_users(admin=Depends(current_admin), db: AsyncSession = Depends(get_db)):
#     from sqlalchemy import select
#     from app.models.user import User
#     result = await db.execute(
#         select(User).where(User.role == "operator", User.is_active == False)
#     )
#     return {"items": [user_to_dict(u) for u in result.scalars().all()]}
#
# @router.post("/users/{user_id}/approve")
# async def approve_user(user_id: str, admin=Depends(current_admin), db: AsyncSession = Depends(get_db)):
#     from sqlalchemy import select
#     from app.models.user import User
#     result = await db.execute(select(User).where(User.id == user_id))
#     user = result.scalar_one_or_none()
#     if not user:
#         raise HTTPException(status_code=404, detail="User not found")
#     user.is_active = True
#     await db.commit()
#     await db.refresh(user)
#     return user_to_dict(user)