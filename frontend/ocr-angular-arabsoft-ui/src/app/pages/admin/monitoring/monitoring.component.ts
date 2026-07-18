/**
 * src/app/pages/admin/monitoring/monitoring.component.ts
 *
 * Dashboard de supervision natif Angular.
 *
 * REDESIGN (alignement charte graphique) :
 * Ce composant utilisait une palette Grafana codée en dur (#111217,
 * #181b1f, #2a2d35, #8e9096, #5794f2...), indépendante du thème
 * clair/sombre de l'application (elle restait sombre même en thème
 * clair). Il utilise désormais les tokens CSS globaux (--bg, --glass,
 * --ink, --accent, --success, --warn, --danger, --radius, --blur...)
 * définis dans styles.css, ce qui :
 *   - aligne sa palette et sa typographie (Syne / DM Sans / JetBrains
 *     Mono) sur le reste de l'application ;
 *   - lui fait suivre automatiquement le thème clair/sombre choisi
 *     par l'utilisateur ;
 *   - garde une identité "console" via des panneaux vitrés (glass +
 *     blur) cohérents avec le hero et les autres panels de l'app,
 *     plutôt que le style Grafana d'origine.
 * Les graphiques Chart.js lisent désormais les couleurs directement
 * depuis les custom properties CSS (cssVar()) et se re-rendent
 * automatiquement lors d'un changement de thème (MutationObserver sur
 * [data-theme]).
 *
 * !! Si ce redesign est adopté, mettre à jour la phrase du rapport
 * (Chapitre 4, section "Composant Angular") qui explique que la
 * palette reprend volontairement celle de Grafana (fond #111217,
 * panneaux #181b1f) pour familiarité admin : ce n'est plus le cas,
 * remplacer par une phrase expliquant l'alignement sur le design
 * system global et le respect du thème clair/sombre.
 */
import {
  Component, ElementRef, OnDestroy, OnInit, ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Chart, ChartConfiguration, registerables } from 'chart.js';
import {
  MetricsSummary,
  MonitoringApiService,
} from '../../../services/monitoring-api.service';

Chart.register(...registerables);

export interface TimeRange {
  label: string;
  value: string;   // valeur PromQL ex: "1h", "6h", "24h", "7d"
}

const TIME_RANGES: TimeRange[] = [
  { label: 'Last 15 min', value: '15m' },
  { label: 'Last 1 hour',  value: '1h'  },
  { label: 'Last 3 hours', value: '3h'  },
  { label: 'Last 6 hours', value: '6h'  },
  { label: 'Last 24 hours',value: '24h' },
  { label: 'Last 7 days',  value: '7d'  },
];

@Component({
  selector: 'app-monitoring',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
<div class="gf-page">

  <!-- ═══ TOOLBAR ══════════════════════════════════════════════ -->
  <div class="gf-toolbar">
    <div class="gf-toolbar-left">
      <span class="gf-title">OCR Microservice — Enterprise</span>
      <span class="gf-subtitle">Supervision du pipeline en temps réel</span>
    </div>

    <div class="gf-toolbar-right">

      <!-- Selecteur de periode -->
      <div class="gf-period-selector">
        <span class="gf-period-icon">🕐</span>
        <select class="gf-period-select"
                [(ngModel)]="selectedRange"
                (ngModelChange)="onRangeChange($event)">
          <option *ngFor="let r of timeRanges" [value]="r.value">
            {{ r.label }}
          </option>
        </select>
      </div>

      <div class="gf-toolbar-divider"></div>

      <!-- Refresh manuel -->
      <button class="gf-btn-refresh" (click)="fetchAndRender()" [disabled]="loading">
        <span [class.spinning]="loading">⟳</span>
        {{ loading ? '' : 'Refresh' }}
      </button>

      <span class="gf-refresh-badge">Auto 30s</span>

      <!-- Statut Prometheus -->
      <span *ngIf="!prometheusAvailable && loaded" class="gf-offline-badge">
        ● Prometheus hors ligne
      </span>
      <span *ngIf="prometheusAvailable && loaded" class="gf-online-badge">
        ● Prometheus connecté
      </span>

    </div>
  </div>

  <!-- ═══ KPI ROW ═══════════════════════════════════════════════ -->
  <div class="gf-row" *ngIf="summary as s">

    <div class="gf-panel gf-stat"
         [class.gf-stat-green]="s.confidence_avg_pct >= 80"
         [class.gf-stat-orange]="s.confidence_avg_pct >= 50 && s.confidence_avg_pct < 80"
         [class.gf-stat-red]="s.confidence_avg_pct > 0 && s.confidence_avg_pct < 50"
         [class.gf-stat-blue]="s.confidence_avg_pct === 0">
      <div class="gf-panel-title">Global Extraction Confidence (avg)</div>
      <div class="gf-stat-value mono">
        {{ s.confidence_avg_pct | number:'1.1-1' }}%
      </div>
      <div class="gf-stat-unit">{{ selectedRangeLabel }}</div>
    </div>

    <div class="gf-panel gf-stat gf-stat-blue">
      <div class="gf-panel-title">Extraction Rate (req/s)</div>
      <div class="gf-stat-value mono">
        {{ s.extraction_rate_per_sec | number:'1.0-4' }}
      </div>
      <div class="gf-stat-unit">req/s — {{ selectedRangeLabel }}</div>
    </div>

    <div class="gf-panel gf-stat"
         [class.gf-stat-green]="s.circuit_breakers_open === 0"
         [class.gf-stat-red]="s.circuit_breakers_open > 0">
      <div class="gf-panel-title">Circuit Breakers Open</div>
      <div class="gf-stat-value mono">{{ s.circuit_breakers_open }}</div>
      <div class="gf-stat-unit">
        {{ s.circuit_breakers_open === 0 ? 'All engines healthy' : 'Engine(s) failing' }}
      </div>
    </div>

    <div class="gf-panel gf-stat gf-stat-blue">
      <div class="gf-panel-title">Active Jobs</div>
      <div class="gf-stat-value mono">{{ s.active_jobs }}</div>
      <div class="gf-stat-unit">jobs en file</div>
    </div>

  </div>

  <!-- ═══ CHARTS ROW ════════════════════════════════════════════ -->
  <div class="gf-row gf-charts-row" *ngIf="summary">

    <div class="gf-panel gf-chart-panel">
      <div class="gf-panel-header">
        <span class="gf-panel-title">Processing Duration by Template (p95)</span>
        <span class="gf-panel-period">{{ selectedRangeLabel }}</span>
      </div>
      <div class="gf-panel-body">
        <canvas #durationCanvas></canvas>
      </div>
    </div>

    <div class="gf-panel gf-chart-panel">
      <div class="gf-panel-header">
        <span class="gf-panel-title">Field Extraction Outcomes</span>
        <span class="gf-panel-period">{{ selectedRangeLabel }}</span>
      </div>
      <div class="gf-panel-body">
        <canvas #fieldsCanvas></canvas>
      </div>
    </div>

  </div>

  <!-- Loading -->
  <div class="gf-loading" *ngIf="!loaded">
    <div class="gf-spinner"></div>
    <span>Chargement des métriques…</span>
  </div>

</div>
  `,
  styles: [`
    /* ── Page ──────────────────────────────────────────────────
       Fond aligné sur le design system global (var(--bg)) : suit
       automatiquement le thème clair/sombre choisi par l'utilisateur,
       au lieu du gris Grafana fixe #111217 d'origine. ── */
    .gf-page {
      background: var(--bg);
      min-height: 100vh;
      color: var(--ink);
      box-sizing: border-box;
      transition: background .4s, color .3s;
    }

    /* ── Toolbar : panneau vitré, cohérent avec .top-nav / .panel ── */
    .gf-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: var(--glass);
      backdrop-filter: var(--blur);
      border-bottom: 1px solid var(--border);
      padding: 0.75rem 1.5rem;
      gap: 1rem;
      flex-wrap: wrap;
    }
    .gf-toolbar-left { display: flex; align-items: baseline; gap: 0.75rem; }
    .gf-title  {
      font-family: 'Syne', sans-serif;
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--ink);
    }
    .gf-subtitle { font-size: 0.76rem; color: var(--ink3); }
    .gf-toolbar-right {
      display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
    }
    .gf-toolbar-divider {
      width: 1px; height: 20px; background: var(--border2); margin: 0 0.25rem;
    }

    /* ── Period selector ── */
    .gf-period-selector {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      background: var(--glass2);
      border: 1px solid var(--border);
      border-radius: var(--radius-xs);
      padding: 0.25rem 0.7rem;
    }
    .gf-period-icon { font-size: 0.85rem; }
    .gf-period-select {
      background: transparent;
      border: none;
      color: var(--ink);
      font-size: 0.82rem;
      cursor: pointer;
      outline: none;
      font-family: inherit;
    }
    .gf-period-select option {
      background: var(--bg2);
      color: var(--ink);
    }
    .gf-period-select:focus-visible {
      outline: 2px solid var(--accent-ink);
      outline-offset: 2px;
      border-radius: 3px;
    }

    /* ── Buttons & badges (alignés .btn-ghost / .chip) ── */
    .gf-btn-refresh {
      background: var(--glass2);
      border: 1px solid var(--border2);
      border-radius: 999px;
      color: var(--ink);
      padding: 0.3rem 0.9rem;
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      font-family: inherit;
      display: flex; align-items: center; gap: 0.35rem;
      transition: all 0.2s;
    }
    .gf-btn-refresh:hover:not(:disabled) {
      background: var(--bg3);
      border-color: var(--accent);
      transform: translateY(-1px);
    }
    .gf-btn-refresh:focus-visible {
      outline: 2px solid var(--accent-ink);
      outline-offset: 2px;
    }
    .gf-btn-refresh:disabled { opacity: 0.5; cursor: not-allowed; }
    .spinning { display: inline-block; animation: gf-spin 0.8s linear infinite; }

    .gf-refresh-badge {
      background: var(--glass2); border: 1px solid var(--border); border-radius: 999px;
      padding: 0.25rem 0.7rem; font-size: 0.72rem; color: var(--ink3);
      letter-spacing: .02em; text-transform: uppercase;
    }
    .gf-offline-badge {
      background: color-mix(in srgb, var(--danger) 15%, transparent);
      border: 1px solid var(--danger);
      border-radius: 999px; padding: 0.25rem 0.8rem;
      font-size: 0.75rem; font-weight: 500; color: var(--danger);
    }
    .gf-online-badge {
      background: color-mix(in srgb, var(--success) 15%, transparent);
      border: 1px solid var(--success);
      border-radius: 999px; padding: 0.25rem 0.8rem;
      font-size: 0.75rem; font-weight: 500; color: var(--success);
    }

    /* ── Grid rows ── */
    .gf-row {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 1rem;
      padding: 1rem 1.5rem;
    }
    .gf-charts-row { grid-template-columns: 1fr 1fr; }
    @media (max-width: 900px) {
      .gf-row { grid-template-columns: repeat(2, 1fr); }
      .gf-charts-row { grid-template-columns: 1fr; }
    }

    /* ── Panels : mêmes codes visuels que .panel (glass + blur) ── */
    .gf-panel {
      background: var(--glass);
      backdrop-filter: var(--blur);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      box-shadow: var(--shadow-sm);
      overflow: hidden;
      transition: border-color .2s, background .4s;
    }
    .gf-panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.65rem 1rem;
      border-bottom: 1px solid var(--border);
    }
    .gf-panel-title {
      font-size: 0.78rem;
      color: var(--ink3);
      letter-spacing: .01em;
    }
    .gf-panel-period {
      font-size: 0.72rem;
      color: var(--accent-ink);
      background: color-mix(in srgb, var(--accent) 12%, transparent);
      border-radius: 999px;
      padding: 0.15rem 0.55rem;
    }
    .gf-panel-body { padding: 0.85rem; min-height: 220px; }
    .gf-panel-body canvas { width: 100% !important; }

    /* ── Stat cards ── */
    .gf-stat {
      display: flex; flex-direction: column;
      padding: 1rem 1.1rem;
      border-left: 3px solid transparent;
      min-height: 100px;
    }
    .gf-stat .gf-panel-title {
      font-size: 0.75rem; color: var(--ink3);
      margin-bottom: 0.55rem;
    }
    .gf-stat-value {
      font-size: 2.2rem; font-weight: 700;
      line-height: 1; letter-spacing: -0.02em;
      color: var(--ink);
    }
    .gf-stat-unit { font-size: 0.75rem; color: var(--ink3); margin-top: 0.35rem; }

    /* Seuils identiques à la logique métier existante (RG4/RG11) ;
       seules les couleurs changent, alignées sur les tokens globaux. */
    .gf-stat-green  { border-left-color: var(--success); }
    .gf-stat-green .gf-stat-value  { color: var(--success); }
    .gf-stat-orange { border-left-color: var(--warn); }
    .gf-stat-orange .gf-stat-value { color: var(--warn); }
    .gf-stat-red    { border-left-color: var(--danger); }
    .gf-stat-red .gf-stat-value    { color: var(--danger); }
    .gf-stat-blue   { border-left-color: var(--accent); }
    .gf-stat-blue .gf-stat-value   { color: var(--accent-ink); }

    /* ── Nombres en JetBrains Mono (cohérent avec le reste de l'app) ── */
    .mono {
      font-family: 'JetBrains Mono', 'Courier New', monospace;
    }

    /* ── Loading ── */
    .gf-loading {
      display: flex; align-items: center; justify-content: center;
      gap: 0.75rem; padding: 4rem; color: var(--ink3); font-size: 0.9rem;
    }
    .gf-spinner {
      width: 20px; height: 20px; border: 2px solid var(--border2);
      border-top-color: var(--accent); border-radius: 50%;
      animation: gf-spin 0.8s linear infinite;
    }
    @keyframes gf-spin { to { transform: rotate(360deg); } }
  `],
})
export class MonitoringComponent implements OnInit, OnDestroy {
  @ViewChild('durationCanvas') durationCanvas!: ElementRef<HTMLCanvasElement>;
  @ViewChild('fieldsCanvas')   fieldsCanvas!:   ElementRef<HTMLCanvasElement>;

  summary: MetricsSummary | null = null;
  prometheusAvailable = true;
  loaded = false;
  loading = false;

  timeRanges  = TIME_RANGES;
  selectedRange = '6h';   // valeur par defaut identique au dashboard Grafana

  private durationChart?: Chart;
  private fieldsChart?: Chart;
  private refreshHandle?: ReturnType<typeof setInterval>;

  // Ré-affiche les graphiques avec les nouvelles couleurs quand le
  // thème clair/sombre change (attribut data-theme sur <html>).
  private themeObserver?: MutationObserver;

  constructor(private monitoringApi: MonitoringApiService) {}

  get selectedRangeLabel(): string {
    return TIME_RANGES.find(r => r.value === this.selectedRange)?.label ?? '';
  }

  ngOnInit(): void {
    this.fetchAndRender();
    this.refreshHandle = setInterval(() => this.fetchAndRender(), 30_000);

    this.themeObserver = new MutationObserver(() => {
      if (this.summary) {
        setTimeout(() => this.renderCharts(this.summary as MetricsSummary), 50);
      }
    });
    this.themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
  }

  ngOnDestroy(): void {
    clearInterval(this.refreshHandle);
    this.themeObserver?.disconnect();
    this.durationChart?.destroy();
    this.fieldsChart?.destroy();
  }

  onRangeChange(_value: string): void {
    // Annule l'ancien timer et en repart immediatement avec la nouvelle periode
    clearInterval(this.refreshHandle);
    this.fetchAndRender();
    this.refreshHandle = setInterval(() => this.fetchAndRender(), 30_000);
  }

  fetchAndRender(): void {
    this.loading = true;
    this.monitoringApi.getMetricsSummary(this.selectedRange).subscribe({
      next: (data) => {
        this.summary = data;
        this.prometheusAvailable = data.prometheus_available;
        this.loaded  = true;
        this.loading = false;
        setTimeout(() => this.renderCharts(data), 50);
      },
      error: () => {
        this.prometheusAvailable = false;
        this.loaded  = true;
        this.loading = false;
      },
    });
  }

  private renderCharts(data: MetricsSummary): void {
    this.renderDurationChart(data.duration_p95_by_template);
    this.renderFieldsChart(data.field_outcomes);
  }

  /** Lit une custom property CSS (--accent, --ink3, ...) sur <html>. */
  private cssVar(name: string, fallback = ''): string {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  }

  private chartDefaults(): object {
    const tickColor = this.cssVar('--ink3', '#94a3b8');
    const gridColor = this.cssVar('--border', 'rgba(99,130,255,.18)');
    return {
      responsive: true,
      maintainAspectRatio: true,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks:  { color: tickColor, font: { size: 11 } },
          grid:   { color: gridColor },
          border: { color: gridColor },
        },
        y: {
          ticks:  { color: tickColor, font: { size: 11 } },
          grid:   { color: gridColor },
          border: { color: gridColor },
        },
      },
    };
  }

  private tooltip(): object {
    return {
      backgroundColor: this.cssVar('--bg2', '#111827'),
      titleColor:  this.cssVar('--ink', '#e8edf8'),
      bodyColor:   this.cssVar('--ink2', '#94a3b8'),
      borderColor: this.cssVar('--border2', 'rgba(99,130,255,.32)'),
      borderWidth: 1,
    };
  }

  private renderDurationChart(series: MetricsSummary['duration_p95_by_template']): void {
    if (!this.durationCanvas?.nativeElement) return;

    const accent = this.cssVar('--accent', '#4f8eff');

    const config: ChartConfiguration = {
      type: 'bar',
      data: {
        labels: series.map(s => s.label),
        datasets: [{
          label: 'p95 (s)',
          data:  series.map(s => s.value),
          backgroundColor: accent,
          borderColor:     accent,
          borderRadius: 4, barPercentage: 0.55,
        }],
      },
      options: {
        ...(this.chartDefaults() as any),
        plugins: {
          legend: { display: false },
          tooltip: {
            ...(this.tooltip() as any),
            callbacks: { label: (ctx: any) => ` ${ctx.parsed.y.toFixed(3)} s` },
          },
        },
      },
    };
    if (this.durationChart) {
      this.durationChart.data = config.data;
      this.durationChart.options = config.options as any;
      this.durationChart.update('none');
    } else {
      this.durationChart = new Chart(this.durationCanvas.nativeElement, config);
    }
  }

  private renderFieldsChart(series: MetricsSummary['field_outcomes']): void {
    if (!this.fieldsCanvas?.nativeElement) return;

    // Couleurs semantiques alignees sur la palette globale de l'app :
    // succès = --success, manquant = --warn, invalide = --danger.
    const OUTCOME_COLORS: Record<string, string> = {
      found:   this.cssVar('--success', '#22d3a0'),
      missing: this.cssVar('--warn', '#f59e0b'),
      invalid: this.cssVar('--danger', '#f87171'),
    };
    const OUTCOME_ORDER = ['found', 'missing', 'invalid'];
    const neutral = this.cssVar('--ink3', '#94a3b8');

    // Liste des champs distincts (axe Y), triee alphabetiquement.
    const fieldNames = Array.from(new Set(series.map(s => s.field_name))).sort();

    // Un dataset par outcome present dans les donnees, dans un ordre stable.
    const outcomesPresent = OUTCOME_ORDER.filter(o => series.some(s => s.outcome === o));
    // Ajoute les outcomes inconnus eventuels, sans casser l'ordre semantique.
    series.forEach(s => { if (!outcomesPresent.includes(s.outcome)) outcomesPresent.push(s.outcome); });

    const datasets = outcomesPresent.map(outcome => ({
      label: outcome,
      data: fieldNames.map(fn => {
        const match = series.find(s => s.field_name === fn && s.outcome === outcome);
        return match ? match.value : 0;
      }),
      backgroundColor: OUTCOME_COLORS[outcome] ?? neutral,
      borderColor:     OUTCOME_COLORS[outcome] ?? neutral,
      borderRadius: 4,
      barPercentage: 0.7,
    }));

    const defaults = this.chartDefaults() as any;

    const config: ChartConfiguration = {
      type: 'bar',
      data: { labels: fieldNames, datasets },
      options: {
        ...defaults,
        indexAxis: 'y',
        plugins: {
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: {
              color: this.cssVar('--ink2', '#94a3b8'),
              boxWidth: 12,
              font: { size: 11 },
            },
          },
          tooltip: this.tooltip(),
        },
        scales: {
          x: { ...defaults.scales.x, stacked: false },
          y: { ...defaults.scales.y, stacked: false },
        },
      },
    };

    if (this.fieldsChart) {
      this.fieldsChart.data = config.data;
      this.fieldsChart.options = config.options as any;
      this.fieldsChart.update('none');
    } else {
      this.fieldsChart = new Chart(this.fieldsCanvas.nativeElement, config);
    }
  }
}
