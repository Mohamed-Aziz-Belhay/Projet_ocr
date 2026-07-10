import { Injectable, signal } from '@angular/core';

export interface Toast {
  id: number;
  kind: 'success' | 'error' | 'info';
  text: string;
}

/**
 * Notifications non bloquantes (succès / erreur / info).
 * Utilisé notamment pour confirmer l'enregistrement des corrections (RG11)
 * et pour signaler l'expiration de session (intercepteur HTTP).
 */
@Injectable({ providedIn: 'root' })
export class ToastService {
  toasts = signal<Toast[]>([]);
  private seq = 0;

  success(text: string, ms = 4500): void { this.push('success', text, ms); }
  error(text: string, ms = 7000): void   { this.push('error', text, ms); }
  info(text: string, ms = 5000): void    { this.push('info', text, ms); }

  dismiss(id: number): void {
    this.toasts.update(list => list.filter(t => t.id !== id));
  }

  private push(kind: Toast['kind'], text: string, ms: number): void {
    const id = ++this.seq;
    this.toasts.update(list => [...list, { id, kind, text }]);
    setTimeout(() => this.dismiss(id), ms);
  }
}
