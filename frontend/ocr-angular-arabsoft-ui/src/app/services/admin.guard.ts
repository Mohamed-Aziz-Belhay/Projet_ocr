import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthApiService } from './auth-api.service';

export const adminGuard: CanActivateFn = () => {
  const auth = inject(AuthApiService);
  const router = inject(Router);

  if (!auth.isLoggedIn()) {
    return router.createUrlTree(['/login']);
  }

  if (auth.canAdmin()) {
    return true;
  }

  return router.createUrlTree(['/']);
};