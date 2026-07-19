import { Injectable } from '@angular/core';
import { AuthApiService, JobStatus } from './auth-api.service';
import { ToastService } from './toast.service';

/**
 * [FONCTIONNALITÉ #2] Notifie l'utilisateur (via ToastService) quand un
 * job d'extraction asynchrone (POST /extract/async) se termine, sans que
 * l'utilisateur ait besoin de revenir vérifier manuellement l'historique.
 *
 * Usage — après avoir déclenché un job asynchrone :
 *   this.jobNotifier.watch(jobId, fileName);
 *
 * Fonctionnement : interroge GET /jobs/{job_id} toutes les 3 secondes
 * (backoff progressif après 10 tentatives pour ne pas marteler l'API sur
 * un job très long) jusqu'à obtenir un statut terminal ('done' ou
 * 'failed'), ou un abandon après 10 minutes.
 *
 * !! À VÉRIFIER : la forme exacte de la réponse GET /jobs/{job_id} n'a
 * pas pu être confirmée sur le code réel (fichier non fourni). Le champ
 * `status` est supposé valoir 'done' en cas de succès et 'failed' en cas
 * d'échec, conformément aux valeurs déjà vues dans routes_extract.py
 * (JobService.update(..., status="done"|"failed"|"processing")). Ajustez
 * TERMINAL_STATUSES ci-dessous si les valeurs réelles diffèrent.
 */
@Injectable({ providedIn: 'root' })
export class JobNotifierService {
  private readonly POLL_INTERVAL_MS = 3000;
  private readonly SLOW_POLL_INTERVAL_MS = 8000;
  private readonly SLOW_POLL_AFTER_ATTEMPTS = 10;
  private readonly MAX_DURATION_MS = 10 * 60 * 1000; // 10 minutes

  private readonly SUCCESS_STATUSES = new Set(['done', 'success', 'completed']);
  private readonly FAILURE_STATUSES = new Set(['failed', 'error']);

  private activeJobs = new Set<string>();

  constructor(
    private authApi: AuthApiService,
    private toast: ToastService
  ) {}

  /**
   * Démarre le suivi d'un job. Ne fait rien si ce job est déjà suivi
   * (évite le double polling si watch() est appelé deux fois par erreur).
   */
  watch(jobId: string, label?: string): void {
    if (!jobId || this.activeJobs.has(jobId)) {
      return;
    }

    this.activeJobs.add(jobId);
    const displayName = label || 'Votre extraction';
    const startedAt = Date.now();
    let attempts = 0;

    const poll = () => {
      // Abandon après MAX_DURATION_MS : évite un polling infini si un job
      // reste bloqué en 'processing' (ex. worker en panne).
      if (Date.now() - startedAt > this.MAX_DURATION_MS) {
        this.activeJobs.delete(jobId);
        this.toast.info(
          `${displayName} : le traitement prend plus de temps que prévu. Consultez l'historique pour suivre son avancement.`
        );
        return;
      }

      this.authApi.jobStatus(jobId).subscribe({
        next: (job: JobStatus) => {
          attempts++;
          const status = String(job.status || '').toLowerCase();

          if (this.SUCCESS_STATUSES.has(status)) {
            this.activeJobs.delete(jobId);
            const confidence = job.global_confidence != null
              ? ` (confiance ${(job.global_confidence <= 1 ? job.global_confidence * 100 : job.global_confidence).toFixed(0)}%)`
              : '';
            this.toast.success(`${displayName} terminée${confidence}. Consultez le résultat dans l'historique.`);
            return;
          }

          if (this.FAILURE_STATUSES.has(status)) {
            this.activeJobs.delete(jobId);
            this.toast.error(
              `${displayName} : échec du traitement${job.error ? ` (${job.error})` : ''}.`
            );
            return;
          }

          // Toujours en file ou en cours : on reprogramme le prochain poll,
          // avec un intervalle plus espacé après SLOW_POLL_AFTER_ATTEMPTS
          // tentatives pour ménager le backend sur les jobs longs.
          const interval = attempts >= this.SLOW_POLL_AFTER_ATTEMPTS
            ? this.SLOW_POLL_INTERVAL_MS
            : this.POLL_INTERVAL_MS;

          setTimeout(poll, interval);
        },
        error: () => {
          // Erreur réseau ponctuelle : on retente plutôt que d'abandonner
          // immédiatement le suivi du job.
          attempts++;
          setTimeout(poll, this.POLL_INTERVAL_MS);
        },
      });
    };

    poll();
  }

  /** Arrête le suivi d'un job sans notification (ex. navigation utilisateur). */
  stopWatching(jobId: string): void {
    this.activeJobs.delete(jobId);
  }

  isWatching(jobId: string): boolean {
    return this.activeJobs.has(jobId);
  }
}