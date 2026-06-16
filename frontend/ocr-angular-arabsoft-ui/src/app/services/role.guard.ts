import { inject } from "@angular/core";
import { ActivatedRouteSnapshot, CanActivateFn, Router } from "@angular/router";
import { AuthApiService, UserRole } from "./auth-api.service";

export const roleGuard: CanActivateFn = (route: ActivatedRouteSnapshot) => {
  const auth = inject(AuthApiService);
  const router = inject(Router);

  if (!auth.isLoggedIn()) {
    return router.createUrlTree(["/login"]);
  }

  const roles = (route.data?.["roles"] || []) as UserRole[];
  if (!roles.length) return true;

  const user = auth.getUser();
  if (!user) return router.createUrlTree(["/login"]);

  // Superuser voit tout
  if (user.is_superuser) return true;

  // Vérifie le rôle directement
  const role = (user.role || "").toString().trim();
  if (roles.map(r => r.toString().trim()).includes(role)) return true;

  // Redirections par rôle
  if (role === "viewer")   return router.createUrlTree(["/history"]);
  if (role === "operator") return router.createUrlTree(["/"]);

  return router.createUrlTree(["/profile"]);
};
