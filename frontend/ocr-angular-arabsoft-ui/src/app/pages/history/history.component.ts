import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  AuthApiService,
  HistoryDetailResponse,
  HistoryItem,
} from '../../services/auth-api.service';

type DateFilter = 'all' | 'today' | 'week' | 'month' | 'year' | 'custom';

@Component({
  selector: 'ocr-history',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './history.component.html',
  styleUrl: './history.component.css',
})
export class HistoryComponent implements OnInit {
  items = signal<HistoryItem[]>([]);
  loading = signal(false);
  detailLoading = signal(false);
  checkingStatus = signal(false);
  error = signal<string | null>(null);

  selectedItem = signal<HistoryItem | null>(null);
  selectedDetail = signal<HistoryDetailResponse | null>(null);
  detailOpen = signal(false);

  dateFilter = signal<DateFilter>('all');
  dateFrom = signal<string>('');
  dateTo = signal<string>('');
  searchQuery = signal<string>('');

  pageSize = 6;
  currentPage = signal(1);

  filteredItems = computed(() => {
    const items = this.items();
    const filter = this.dateFilter();
    const query = this.searchQuery().trim().toLowerCase();

    let result = items;

    if (filter !== 'all') {
      const range = this.getDateRange();

      if (range.start || range.end) {
        result = result.filter(item => {
          if (!item.created_at) {
            return false;
          }

          const itemDate = new Date(item.created_at);

          if (Number.isNaN(itemDate.getTime())) {
            return false;
          }

          if (range.start && itemDate < range.start) {
            return false;
          }

          if (range.end && itemDate > range.end) {
            return false;
          }

          return true;
        });
      }
    }

    if (!query) {
      return result;
    }

    return result.filter(item => {
      const searchable = [
        item.file_name,
        item.document_type,
        item.status,
        item.job_id,
        item.engine_used,
        item.user_email,
        item.user_id,
        item.user_role,
        item.template_id,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

      return searchable.includes(query);
    });
  });

  totalPages = computed(() => {
    const total = this.filteredItems().length;
    return Math.max(1, Math.ceil(total / this.pageSize));
  });

  pagedItems = computed(() => {
    const page = Math.min(this.currentPage(), this.totalPages());
    const start = (page - 1) * this.pageSize;

    return this.filteredItems().slice(start, start + this.pageSize);
  });

  constructor(public authApi: AuthApiService, public auth: AuthApiService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.selectedItem.set(null);
    this.selectedDetail.set(null);
    this.detailOpen.set(false);
    this.currentPage.set(1);

    this.authApi.history(100).subscribe({
      next: response => {
        this.items.set(response.items || []);
        this.loading.set(false);
      },
      error: err => {
        this.error.set(err?.error?.detail || err?.message || 'Historique indisponible.');
        this.loading.set(false);
      },
    });
  }

  onDateFilterChange(value: DateFilter): void {
    this.dateFilter.set(value);
    this.currentPage.set(1);

    if (value !== 'custom') {
      this.dateFrom.set('');
      this.dateTo.set('');
      return;
    }

    const today = this.formatInputDate(new Date());

    if (!this.dateFrom()) {
      this.dateFrom.set(today);
    }

    if (!this.dateTo()) {
      this.dateTo.set(today);
    }
  }

  applyCustomDateFilter(): void {
    if (!this.dateFrom() && !this.dateTo()) {
      this.error.set('Veuillez choisir une date de début ou une date de fin.');
      return;
    }

    if (this.dateFrom() && this.dateTo() && this.dateFrom() > this.dateTo()) {
      this.error.set('La date de début doit être inférieure ou égale à la date de fin.');
      return;
    }

    this.error.set(null);
    this.currentPage.set(1);
  }

  resetDateFilter(): void {
    this.dateFilter.set('all');
    this.dateFrom.set('');
    this.dateTo.set('');
    this.currentPage.set(1);
    this.error.set(null);
  }

  clearSearch(): void {
    this.searchQuery.set('');
    this.currentPage.set(1);
  }

  getDateFilterLabel(): string {
    const filter = this.dateFilter();

    if (filter === 'all') return 'Toutes les dates';
    if (filter === 'today') return 'Aujourd’hui';
    if (filter === 'week') return 'Cette semaine';
    if (filter === 'month') return 'Ce mois';
    if (filter === 'year') return 'Cette année';

    const from = this.dateFrom() ? this.formatDateOnly(this.dateFrom()) : 'début';
    const to = this.dateTo() ? this.formatDateOnly(this.dateTo()) : 'fin';

    return `Du ${from} au ${to}`;
  }

  private getDateRange(): { start: Date | null; end: Date | null } {
    const filter = this.dateFilter();
    const today = new Date();

    if (filter === 'all') {
      return { start: null, end: null };
    }

    if (filter === 'custom') {
      return {
        start: this.dateFrom() ? new Date(`${this.dateFrom()}T00:00:00`) : null,
        end: this.dateTo() ? new Date(`${this.dateTo()}T23:59:59`) : null,
      };
    }

    const start = new Date(today);
    const end = new Date(today);

    end.setHours(23, 59, 59, 999);

    if (filter === 'today') {
      start.setHours(0, 0, 0, 0);
      return { start, end };
    }

    if (filter === 'week') {
      const day = today.getDay() || 7;
      start.setDate(today.getDate() - day + 1);
      start.setHours(0, 0, 0, 0);
      return { start, end };
    }

    if (filter === 'month') {
      start.setDate(1);
      start.setHours(0, 0, 0, 0);
      return { start, end };
    }

    if (filter === 'year') {
      start.setMonth(0);
      start.setDate(1);
      start.setHours(0, 0, 0, 0);
      return { start, end };
    }

    return { start: null, end: null };
  }

  private formatInputDate(date: Date): string {
    const year = date.getFullYear();
    const month = `${date.getMonth() + 1}`.padStart(2, '0');
    const day = `${date.getDate()}`.padStart(2, '0');

    return `${year}-${month}-${day}`;
  }

  private formatDateOnly(value: string): string {
    const d = new Date(`${value}T00:00:00`);

    if (Number.isNaN(d.getTime())) {
      return value;
    }

    return d.toLocaleDateString('fr-FR', {
      day: '2-digit',
      month: 'long',
      year: 'numeric',
    });
  }

  nextPage(): void {
    if (this.currentPage() < this.totalPages()) {
      this.currentPage.update(page => page + 1);
    }
  }

  previousPage(): void {
    if (this.currentPage() > 1) {
      this.currentPage.update(page => page - 1);
    }
  }

  goToPage(page: number): void {
    if (page >= 1 && page <= this.totalPages()) {
      this.currentPage.set(page);
    }
  }

  selectItem(item: HistoryItem): void {
    this.selectedItem.set(item);
    this.selectedDetail.set(null);
    this.detailOpen.set(true);

    const id = item.id || item.job_id;

    if (!id) {
      return;
    }

    this.detailLoading.set(true);

    this.authApi.historyDetail(id).subscribe({
      next: detail => {
        this.selectedDetail.set(detail);
        this.detailLoading.set(false);
      },
      error: () => {
        this.detailLoading.set(false);
      },
    });
  }

  closeDetails(): void {
    this.selectedItem.set(null);
    this.selectedDetail.set(null);
    this.detailOpen.set(false);
    this.detailLoading.set(false);
  }

  isMine(item: HistoryItem): boolean {
    const me = this.authApi.getUser();
    return Boolean(me?.email && item.user_email === me.email);
  }

  isAdminExtraction(item: HistoryItem): boolean {
    return String(item.user_role || '').toLowerCase() === 'admin';
  }

  userLabel(item: HistoryItem): string {
    return item.user_email || item.user_id || 'Utilisateur inconnu';
  }

  userInitial(item: HistoryItem): string {
    return this.userLabel(item).slice(0, 1).toUpperCase();
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

  formatMs(value?: number | null): string {
    if (value === null || value === undefined) {
      return '—';
    }

    return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`;
  }

  percent(value?: number | null): string {
    if (value === null || value === undefined) {
      return '—';
    }

    const n = Number(value);
    return `${(n <= 1 ? n * 100 : n).toFixed(1)}%`;
  }

  statusClass(status?: string | null): string {
    const s = String(status || '').toLowerCase();

    if (s.includes('success') || s.includes('done') || s.includes('valid')) {
      return 'done';
    }

    if (s.includes('review') || s.includes('partial')) {
      return 'warning';
    }

    if (s.includes('fail') || s.includes('error')) {
      return 'failed';
    }

    return 'neutral';
  }

  riskClass(item: HistoryItem): string {
    const conf = Number(item.global_confidence);

    if (!Number.isFinite(conf)) {
      return 'risk-unknown';
    }

    const pct = conf <= 1 ? conf * 100 : conf;

    if (pct >= 85) return 'risk-low';
    if (pct >= 70) return 'risk-medium';

    return 'risk-high';
  }

  riskLabel(item: HistoryItem): string {
    const cls = this.riskClass(item);

    if (cls === 'risk-low') return 'Faible';
    if (cls === 'risk-medium') return 'Moyen';
    if (cls === 'risk-high') return 'Élevé';

    return 'Inconnu';
  }

  hasDetailContent(detail: HistoryDetailResponse | null): boolean {
    if (!detail) {
      return false;
    }

    return Boolean(
      detail.raw_text ||
      detail.result_json ||
      detail.fields_json ||
      detail.diagnostics_json
    );
  }

  prettyJson(value: unknown): string {
    if (value === null || value === undefined || value === '') {
      return '—';
    }

    if (typeof value === 'string') {
      try {
        return JSON.stringify(JSON.parse(value), null, 2);
      } catch {
        return value;
      }
    }

    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
}