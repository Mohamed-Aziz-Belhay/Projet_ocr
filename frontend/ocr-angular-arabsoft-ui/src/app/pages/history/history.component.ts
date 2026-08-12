import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  AuthApiService,
  HistoryDetailResponse,
  HistoryItem,
} from '../../services/auth-api.service';
import { ToastService } from '../../services/toast.service';

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

  // [FONCTIONNALITÉ #3] État du bouton d'export, pour un retour visuel
  // pendant la génération (utile si la liste s'agrandit un jour).
  exporting = signal(false);

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

  constructor(
    public authApi: AuthApiService,
    public auth: AuthApiService,
    private toast: ToastService
  ) {}

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

    this.authApi.history(300).subscribe({
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

  // ═══════════════════════════════════════════════════════════
  // [FONCTIONNALITÉ #3] Export CSV en masse de l'historique
  // ═══════════════════════════════════════════════════════════
  //
  // Choix de conception :
  // - Exporte filteredItems() (respecte recherche + filtre de date en
  //   cours), pas toujours l'intégralité des 300 lignes chargées : un
  //   utilisateur qui a filtré "ce mois" et clique "Exporter" attend
  //   un CSV du mois, pas de tout l'historique.
  // - Génération 100% cliente (les données sont déjà en mémoire via
  //   items()) : aucun appel réseau supplémentaire, aucune route
  //   backend à créer.
  // - Séparateur ';' (et non ',') : Excel en локale FR n'interprète
  //   correctement les CSV qu'avec ce séparateur par défaut.
  // - BOM UTF-8 ajouté en tête de fichier : sans lui, Excel affiche
  //   les caractères accentués (é, è, à...) de façon corrompue.
  exportCsv(): void {
    const items = this.filteredItems();

    if (!items.length) {
      this.toast.info('Aucune ligne à exporter avec les filtres actuels.');
      return;
    }

    this.exporting.set(true);

    try {
      const headers = this.csvHeaders();
      const rows = items.map(item => this.buildCsvRow(item));
      const csvContent = this.buildCsvContent(headers, rows);

      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const dateSuffix = this.formatInputDate(new Date());

      const link = document.createElement('a');
      link.href = url;
      link.download = `historique_extractly_${dateSuffix}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);

      this.toast.success(`${items.length} ligne(s) exportée(s) vers le fichier CSV.`);
    } catch (e) {
      this.toast.error("Échec de l'export CSV. Réessayez ou contactez le support.");
    } finally {
      this.exporting.set(false);
    }
  }

  // ═══════════════════════════════════════════════════════════
  // [FONCTIONNALITÉ #4] Export CSV détaillé d'une seule ligne
  // ═══════════════════════════════════════════════════════════
  //
  // Contrairement à exportCsv() (résumé compact, une ligne par
  // extraction — adapté à un export en masse), l'export d'UNE seule
  // ligne va chercher le détail complet (fields_json, même appel que
  // l'ouverture de la modale) pour lister chaque champ extrait
  // individuellement — équivalent CSV du rapport PDF (routes_exports.py
  // /exports/pdf), qui affiche déjà chaque champ avec sa valeur et sa
  // confiance.
  exportRow(item: HistoryItem, event: Event): void {
    // Empêche l'ouverture du détail : le clic sur la ligne déclenche
    // normalement selectItem() via (click) sur le <tr> — on ne veut
    // ici que l'export, pas l'ouverture de la modale de détail.
    event.stopPropagation();

    const id = item.id || item.job_id;

    if (!id) {
      this.toast.error("Impossible d'exporter : identifiant manquant.");
      return;
    }

    this.authApi.historyDetail(id).subscribe({
      next: detail => this.downloadDetailedRowCsv(item, detail),
      error: () => {
        this.toast.error("Impossible de récupérer le détail pour l'export.");
      },
    });
  }

  private downloadDetailedRowCsv(item: HistoryItem, detail: HistoryDetailResponse): void {
    const lines: string[] = [];

    // Section 1 : résumé de l'extraction
    lines.push(this.csvRowLine(['Champ', 'Valeur']));
    lines.push(this.csvRowLine(['Fichier', item.file_name ?? '']));
    lines.push(this.csvRowLine(['Utilisateur', this.userLabel(item)]));
    lines.push(this.csvRowLine(['Rôle', item.user_role ?? '']));
    lines.push(this.csvRowLine(['Job ID', item.job_id ?? '']));
    lines.push(this.csvRowLine(['Type de document', item.document_type ?? '']));
    lines.push(this.csvRowLine(['Template', item.template_id ?? '']));
    lines.push(this.csvRowLine(['Statut', item.status ?? '']));
    lines.push(this.csvRowLine(['Confiance globale (%)', this.percent(item.global_confidence).replace('%', '')]));
    lines.push(this.csvRowLine(['Moteur OCR', item.engine_used ?? '']));
    lines.push(this.csvRowLine(['Temps de traitement', this.formatMs(item.processing_time_ms)]));
    lines.push(this.csvRowLine(['Date', item.created_at ? new Date(item.created_at).toLocaleString('fr-FR') : '']));
    lines.push('');

    // Section 2 : champs extraits, un par ligne (équivalent du rapport PDF)
    const fields = this.extractFieldsArray(detail);

    if (fields.length) {
      lines.push(this.csvRowLine(['Nom du champ', 'Valeur extraite', 'Confiance (%)', 'Validé']));

      for (const f of fields) {
        const confRaw = f?.confidence;
        const confPct = confRaw != null
          ? (Number(confRaw) <= 1 ? Number(confRaw) * 100 : Number(confRaw)).toFixed(1)
          : '';

        lines.push(this.csvRowLine([
          f?.name ?? f?.field_name ?? f?.key ?? '',
          this.stringifyFieldValue(f?.value),
          confPct,
          f?.validated ? 'Oui' : 'Non',
        ]));
      }
    } else {
      lines.push('Aucun champ détaillé disponible pour cette extraction.');
    }

    const BOM = '\uFEFF';
    const csvContent = BOM + lines.join('\r\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const safeName = (item.file_name || 'extraction').replace(/[^a-z0-9_.-]/gi, '_');

    const link = document.createElement('a');
    link.href = url;
    link.download = `extraction_${safeName}_detail.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    this.toast.success(`Détail exporté : ${item.file_name || 'extraction'}.`);
  }

  /** Normalise fields_json (déjà un tableau, ou une chaîne JSON à parser). */
  private extractFieldsArray(detail: HistoryDetailResponse): any[] {
    const raw = (detail as any)?.fields_json;

    if (Array.isArray(raw)) {
      return raw;
    }

    if (typeof raw === 'string') {
      try {
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
      } catch {
        return [];
      }
    }

    return [];
  }

  private stringifyFieldValue(value: unknown): string {
    if (value === null || value === undefined) {
      return '';
    }
    if (typeof value === 'object') {
      return JSON.stringify(value);
    }
    return String(value);
  }

  private csvRowLine(cells: string[]): string {
    return cells.map(c => this.csvEscapeCell(c)).join(';');
  }

  /** Intitulés de colonnes, partagés entre l'export global et l'export par ligne. */
  private csvHeaders(): string[] {
    return [
      'Utilisateur', 'Rôle', 'Fichier', 'Job ID', 'Type de document',
      'Template', 'Statut', 'Confiance (%)', 'Champs extraits',
      'Moteur OCR', 'Temps de traitement (ms)', 'Date',
    ];
  }

  /** Construit une ligne CSV à partir d'un HistoryItem, dans l'ordre de csvHeaders(). */
  private buildCsvRow(item: HistoryItem): (string | number)[] {
    return [
      this.userLabel(item),
      item.user_role ?? '',
      item.file_name ?? '',
      item.job_id ?? '',
      item.document_type ?? '',
      item.template_id ?? '',
      item.status ?? '',
      this.percent(item.global_confidence).replace('%', '').replace('—', ''),
      item.field_count ?? '',
      item.engine_used ?? '',
      item.processing_time_ms ?? '',
      item.created_at ? new Date(item.created_at).toLocaleString('fr-FR') : '',
    ];
  }

  /** Assemble l'en-tête + les lignes en contenu CSV complet (BOM + séparateur ';'). */
  private buildCsvContent(headers: string[], rows: (string | number)[][]): string {
    const csvLines = [headers, ...rows].map(row =>
      row.map(cell => this.csvEscapeCell(String(cell))).join(';')
    );

    const BOM = '\uFEFF';
    return BOM + csvLines.join('\r\n');
  }

  /** Échappe une cellule pour le format CSV (RFC 4180). */
  private csvEscapeCell(value: string): string {
    if (value.includes(';') || value.includes('"') || value.includes('\n') || value.includes('\r')) {
      return `"${value.replace(/"/g, '""')}"`;
    }
    return value;
  }
}