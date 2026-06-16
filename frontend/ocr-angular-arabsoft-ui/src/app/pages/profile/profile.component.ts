import { CommonModule } from '@angular/common';
import { Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import {
  AuthApiService,
  AuthUser,
  HistoryItem,
} from '../../services/auth-api.service';

type ProfileStats = {
  total: number;
  success: number;
  review: number;
  failed: number;
  avgConfidence: number | null;
  lastExtraction: HistoryItem | null;
};

@Component({
  selector: 'ocr-profile',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './profile.component.html',
  styleUrl: './profile.component.css',
})
export class ProfileComponent implements OnInit {
  user = signal<AuthUser | null>(null);
  loading = signal(false);
  error = signal<string | null>(null);

  statsLoading = signal(false);
  statsError = signal<string | null>(null);
  profileStats = signal<ProfileStats>({
    total: 0,
    success: 0,
    review: 0,
    failed: 0,
    avgConfidence: null,
    lastExtraction: null,
  });

  currentPassword = '';
  newPassword = '';
  confirmPassword = '';
  passwordLoading = signal(false);
  passwordMessage = signal<string | null>(null);
  passwordError = signal<string | null>(null);

  constructor(public auth: AuthApiService, private router: Router) {}

  ngOnInit(): void {
  this.user.set(this.auth.getUser());
  this.refresh();
}

  refresh(): void {
  this.loading.set(true);
  this.error.set(null);

  this.auth.me().subscribe({
    next: user => {
      this.user.set(user);
      localStorage.setItem('ocr_user', JSON.stringify(user));
      this.loading.set(false);

      // Important : charger les statistiques après récupération du vrai user connecté
      this.loadPersonalStats();
    },
    error: err => {
      this.error.set(err?.error?.detail || err?.message || 'Session invalide.');
      this.loading.set(false);
    },
  });
}

  loadPersonalStats(): void {
  this.statsLoading.set(true);
  this.statsError.set(null);

  this.auth.history(500).subscribe({
    next: response => {
      const currentUser = this.user() || this.auth.getUser();
      const items = response.items || [];

      if (!currentUser) {
        this.profileStats.set({
          total: 0,
          success: 0,
          review: 0,
          failed: 0,
          avgConfidence: null,
          lastExtraction: null,
        });

        this.statsLoading.set(false);
        return;
      }

      const currentEmail = String(currentUser.email || '').trim().toLowerCase();

      const mine = items.filter(item => {
        const itemEmail = String(item.user_email || '').trim().toLowerCase();

        return Boolean(
          currentEmail &&
          itemEmail &&
          itemEmail === currentEmail
        );
      });

      const success = mine.filter(item => this.isSuccessStatus(item.status)).length;
      const review = mine.filter(item => this.isReviewStatus(item.status)).length;
      const failed = mine.filter(item => this.isFailedStatus(item.status)).length;

      const confidenceValues = mine
        .map(item => Number(item.global_confidence))
        .filter(value => Number.isFinite(value))
        .map(value => value <= 1 ? value * 100 : value);

      const avgConfidence = confidenceValues.length
        ? confidenceValues.reduce((sum, value) => sum + value, 0) / confidenceValues.length
        : null;

      const sorted = [...mine].sort((a, b) => {
        const da = new Date(a.created_at || '').getTime();
        const db = new Date(b.created_at || '').getTime();

        return db - da;
      });

      this.profileStats.set({
        total: mine.length,
        success,
        review,
        failed,
        avgConfidence,
        lastExtraction: sorted[0] || null,
      });

      this.statsLoading.set(false);
    },
    error: err => {
      this.statsError.set(
        err?.error?.detail ||
        err?.message ||
        'Statistiques personnelles indisponibles.'
      );

      this.statsLoading.set(false);
    },
  });
}

  changePassword(): void {
    this.passwordMessage.set(null);
    this.passwordError.set(null);

    if (!this.currentPassword.trim()) {
      this.passwordError.set('Ancien mot de passe obligatoire.');
      return;
    }

    if (!this.newPassword.trim()) {
      this.passwordError.set('Nouveau mot de passe obligatoire.');
      return;
    }

    if (this.newPassword.length < 8) {
      this.passwordError.set('Le nouveau mot de passe doit contenir au moins 8 caractères.');
      return;
    }

    if (this.newPassword.length > 255) {
      this.passwordError.set('Le nouveau mot de passe est trop long.');
      return;
    }

    if (this.newPassword !== this.confirmPassword) {
      this.passwordError.set('Les deux mots de passe ne correspondent pas.');
      return;
    }

    if (this.currentPassword === this.newPassword) {
      this.passwordError.set('Le nouveau mot de passe doit être différent de l’ancien.');
      return;
    }

    this.passwordLoading.set(true);

    this.auth.changePassword(this.currentPassword, this.newPassword).subscribe({
      next: () => {
        this.passwordLoading.set(false);
        this.currentPassword = '';
        this.newPassword = '';
        this.confirmPassword = '';
        this.passwordMessage.set('Mot de passe modifié avec succès.');
      },
      error: err => {
        this.passwordLoading.set(false);

        const detail =
          err?.error?.detail ||
          err?.error?.message ||
          err?.message ||
          'Impossible de modifier le mot de passe.';

        this.passwordError.set(String(detail));
      },
    });
  }

  logout(): void {
    this.auth.logout();
    this.router.navigate(['/login']);
  }

  isAdmin(): boolean {
    const u = this.user();

    return Boolean(u?.is_superuser || u?.role === 'admin');
  }

  formatDate(value?: string | null): string {
    if (!value) {
      return '—';
    }

    const d = new Date(value);

    if (Number.isNaN(d.getTime())) {
      return value;
    }

    return d.toLocaleString('fr-FR', {
      day: '2-digit',
      month: 'long',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  percent(value?: number | null): string {
    if (value === null || value === undefined) {
      return '—';
    }

    return `${Number(value).toFixed(1)}%`;
  }

  private isSuccessStatus(status?: string | null): boolean {
    const s = String(status || '').toLowerCase();

    return s.includes('success') || s.includes('done') || s.includes('valid');
  }

  private isReviewStatus(status?: string | null): boolean {
    const s = String(status || '').toLowerCase();

    return s.includes('review') || s.includes('partial');
  }

  private isFailedStatus(status?: string | null): boolean {
    const s = String(status || '').toLowerCase();

    return s.includes('fail') || s.includes('error');
  }
}