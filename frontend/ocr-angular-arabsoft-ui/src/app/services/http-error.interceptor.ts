import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthApiService } from './auth-api.service';
import { ToastService } from './toast.service';

/**
 * Gestion GLOBALE des erreurs HTTP.
 *  - 401 : session expirée (JWT 4 h sans refresh token) -> déconnexion propre,
 *    toast explicite, redirection /login avec returnUrl. Les corrections en
 *    cours sont déjà sauvegardées en brouillon par le composant Extraction
 *    (sessionStorage), donc rien n'est perdu.
 *  - 403 : rôle insuffisant -> message clair au lieu d'un échec silencieux.
 *  - 413 : fichier trop volumineux (RG3, 25 Mo).
 *  - 0   : backend injoignable.
 * Les 4xx métier (422 DOCUMENT_TYPE_MISMATCH, etc.) restent traités par les
 * composants, qui les traduisent déjà finement.
 */
export const httpErrorInterceptor: HttpInterceptorFn = (req, next) => {
  const router = inject(Router);
  const toast = inject(ToastService);
  const auth = inject(AuthApiService);

  return next(req).pipe(
    catchError((err: HttpErrorResponse) => {
      const isAuthCall = req.url.includes('/auth/login') || req.url.includes('/auth/register');

      if (err.status === 401 && !isAuthCall) {
        const returnUrl = router.url;
        try { sessionStorage.setItem('ocr_return_url', returnUrl); } catch {}
        auth.logout();
        toast.info(
          'Votre session a expiré (4 h). Reconnectez-vous — vos corrections en cours ont été conservées en brouillon.'
        );
        router.navigate(['/login'], { queryParams: { returnUrl } });
      } else if (err.status === 403) {
        const detail = err?.error?.detail;
        toast.error(typeof detail === 'string' ? detail : "Accès refusé : votre rôle ne permet pas cette action.");
      } else if (err.status === 413) {
        toast.error('Fichier trop volumineux : la limite est de 25 Mo (RG3).');
      } else if (err.status === 0) {
        toast.error('Serveur injoignable. Vérifiez que le backend est démarré (docker compose ps).');
      }

      return throwError(() => err);
    })
  );
};
