import { CommonModule } from '@angular/common';
import { Component, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AdminApiService, DashboardStats } from '../../../services/admin-api.service';
import { AuthApiService } from '../../../services/auth-api.service';

@Component({
  selector: 'ocr-admin-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './admin-dashboard.component.html',
  styleUrl: './admin-dashboard.component.css',
})
export class AdminDashboardComponent implements OnInit {
  stats = signal<DashboardStats | null>(null);
  loading = signal(false);
  error = signal<string | null>(null);

  constructor(private adminApi: AdminApiService, public auth: AuthApiService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);

    this.adminApi.dashboardStats().subscribe({
      next: stats => {
        this.stats.set(stats);
        this.loading.set(false);
      },
      error: err => {
        this.error.set(err?.error?.detail || err?.message || 'Dashboard indisponible.');
        this.loading.set(false);
      },
    });
  }

  successRate(s: DashboardStats): string {
    const total = Number(s.extractions?.total);
    const success = Number(s.extractions?.success);

    if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(success)) {
      return '—';
    }

    return `${((success / total) * 100).toFixed(1)}%`;
  }

  safePercent(value: any): string {
    const n = Number(value);

    if (!Number.isFinite(n)) {
      return '—';
    }

    return `${(n <= 1 ? n * 100 : n).toFixed(1)}%`;
  }

  percent(value: any): string {
    return this.safePercent(value);
  }

  formatMs(value: any): string {
    const n = Number(value);

    if (!Number.isFinite(n)) {
      return '—';
    }

    return n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(2)} s`;
  }

  formatDate(value: any): string {
    if (!value) {
      return '—';
    }

    const d = new Date(value);

    return Number.isNaN(d.getTime())
      ? String(value)
      : d.toLocaleString('fr-FR', {
          day: '2-digit',
          month: 'long',
          year: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        });
  }
}