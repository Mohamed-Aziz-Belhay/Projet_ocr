/**
 * src/app/pages/admin/monitoring/monitoring.component.ts
 *
 * Dashboard de supervision natif Angular, style Grafana dark.
 * Inclut un selecteur de periode qui adapte dynamiquement les
 * fenetres PromQL (rate/histogram_quantile) cote backend.
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
      <div class="gf-stat-value">
        {{ s.confidence_avg_pct | number:'1.1-1' }}%
      </div>
      <div class="gf-stat-unit">{{ selectedRangeLabel }}</div>
    </div>

    <div class="gf-panel gf-stat gf-stat-blue">
      <div class="gf-panel-title">Extraction Rate (req/s)</div>
      <div class="gf-stat-value">
        {{ s.extraction_rate_per_sec | number:'1.0-4' }}
      </div>
      <div class="gf-stat-unit">req/s — {{ selectedRangeLabel }}</div>
    </div>

    <div class="gf-panel gf-stat"
         [class.gf-stat-green]="s.circuit_breakers_open === 0"
         [class.gf-stat-red]="s.circuit_breakers_open > 0">
      <div class="gf-panel-title">Circuit Breakers Open</div>
      <div class="gf-stat-value">{{ s.circuit_breakers_open }}</div>
      <div class="gf-stat-unit">
        {{ s.circuit_breakers_open === 0 ? 'All engines healthy' : 'Engine(s) failing' }}
      </div>
    </div>

    <div class="gf-panel gf-stat gf-stat-blue">
      <div class="gf-panel-title">Active Jobs</div>
      <div class="gf-stat-value">{{ s.active_jobs }}</div>
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
    /* ── Page ──────────────────────────────── */
    .gf-page {
      background: #111217;
      min-height: 100vh;
      font-family: 'DM Sans', 'Inter', sans-serif;
      color: #d8d9da;
      box-sizing: border-box;
    }

    /* ── Toolbar ───────────────────────────── */
    .gf-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #181b1f;
      border-bottom: 1px solid #2a2d35;
      padding: 0.65rem 1.25rem;
      gap: 1rem;
      flex-wrap: wrap;
    }
    .gf-toolbar-left { display: flex; align-items: baseline; gap: 0.75rem; }
    .gf-title  { font-size: 1.05rem; font-weight: 600; color: #d8d9da; }
    .gf-subtitle { font-size: 0.76rem; color: #8e9096; }
    .gf-toolbar-right {
      display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
    }
    .gf-toolbar-divider {
      width: 1px; height: 20px; background: #2a2d35; margin: 0 0.25rem;
    }

    /* ── Period selector ───────────────────── */
    .gf-period-selector {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      background: #22252b;
      border: 1px solid #2a2d35;
      border-radius: 5px;
      padding: 0.2rem 0.6rem;
    }
    .gf-period-icon { font-size: 0.85rem; }
    .gf-period-select {
      background: transparent;
      border: none;
      color: #d8d9da;
      font-size: 0.82rem;
      cursor: pointer;
      outline: none;
      font-family: inherit;
    }
    .gf-period-select option {
      background: #22252b;
      color: #d8d9da;
    }

    /* ── Buttons & badges ─────────────────── */
    .gf-btn-refresh {
      background: #22252b;
      border: 1px solid #2a2d35;
      border-radius: 4px;
      color: #d8d9da;
      padding: 0.22rem 0.7rem;
      font-size: 0.8rem;
      cursor: pointer;
      font-family: inherit;
      display: flex; align-items: center; gap: 0.3rem;
      transition: background 0.15s;
    }
    .gf-btn-refresh:hover { background: #2a2d35; }
    .gf-btn-refresh:disabled { opacity: 0.5; cursor: not-allowed; }
    .spinning { display: inline-block; animation: spin 0.8s linear infinite; }

    .gf-refresh-badge {
      background: #22252b; border: 1px solid #2a2d35; border-radius: 4px;
      padding: 0.2rem 0.6rem; font-size: 0.75rem; color: #8e9096;
    }
    .gf-offline-badge {
      background: rgba(242,73,92,0.15); border: 1px solid #f2495c;
      border-radius: 4px; padding: 0.2rem 0.7rem;
      font-size: 0.75rem; color: #f2495c;
    }
    .gf-online-badge {
      background: rgba(115,191,105,0.15); border: 1px solid #73bf69;
      border-radius: 4px; padding: 0.2rem 0.7rem;
      font-size: 0.75rem; color: #73bf69;
    }

    /* ── Grid rows ─────────────────────────── */
    .gf-row {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 0.75rem;
      padding: 0.75rem 1.25rem;
    }
    .gf-charts-row { grid-template-columns: 1fr 1fr; }

    /* ── Panels ────────────────────────────── */
    .gf-panel {
      background: #181b1f;
      border: 1px solid #2a2d35;
      border-radius: 6px;
      overflow: hidden;
    }
    .gf-panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.55rem 0.9rem;
      border-bottom: 1px solid #2a2d35;
    }
    .gf-panel-title {
      font-size: 0.78rem;
      color: #8e9096;
    }
    .gf-panel-period {
      font-size: 0.72rem;
      color: #5794f2;
      background: rgba(87,148,242,0.1);
      border-radius: 3px;
      padding: 0.1rem 0.4rem;
    }
    .gf-panel-body { padding: 0.75rem; min-height: 220px; }
    .gf-panel-body canvas { width: 100% !important; }

    /* ── Stat cards ────────────────────────── */
    .gf-stat {
      display: flex; flex-direction: column;
      padding: 0.85rem 1rem;
      border-left: 4px solid transparent;
      min-height: 100px;
    }
    .gf-stat .gf-panel-title {
      font-size: 0.75rem; color: #8e9096;
      margin-bottom: 0.5rem;
    }
    .gf-stat-value {
      font-size: 2.3rem; font-weight: 700;
      line-height: 1; letter-spacing: -0.5px;
    }
    .gf-stat-unit { font-size: 0.75rem; color: #8e9096; margin-top: 0.3rem; }

    .gf-stat-green  { border-left-color: #73bf69; }
    .gf-stat-green .gf-stat-value  { color: #73bf69; }
    .gf-stat-orange { border-left-color: #ff9830; }
    .gf-stat-orange .gf-stat-value { color: #ff9830; }
    .gf-stat-red    { border-left-color: #f2495c; }
    .gf-stat-red .gf-stat-value    { color: #f2495c; }
    .gf-stat-blue   { border-left-color: #5794f2; }
    .gf-stat-blue .gf-stat-value   { color: #5794f2; }

    /* ── Loading ───────────────────────────── */
    .gf-loading {
      display: flex; align-items: center; justify-content: center;
      gap: 0.75rem; padding: 4rem; color: #8e9096; font-size: 0.9rem;
    }
    .gf-spinner {
      width: 20px; height: 20px; border: 2px solid #2a2d35;
      border-top-color: #5794f2; border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
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

  constructor(private monitoringApi: MonitoringApiService) {}

  get selectedRangeLabel(): string {
    return TIME_RANGES.find(r => r.value === this.selectedRange)?.label ?? '';
  }

  ngOnInit(): void {
    this.fetchAndRender();
    this.refreshHandle = setInterval(() => this.fetchAndRender(), 30_000);
  }

  ngOnDestroy(): void {
    clearInterval(this.refreshHandle);
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

  private chartDefaults(): object {
    return {
      responsive: true,
      maintainAspectRatio: true,
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks:  { color: '#8e9096', font: { size: 11 } },
          grid:   { color: 'rgba(255,255,255,0.07)' },
          border: { color: '#2a2d35' },
        },
        y: {
          ticks:  { color: '#8e9096', font: { size: 11 } },
          grid:   { color: 'rgba(255,255,255,0.07)' },
          border: { color: '#2a2d35' },
        },
      },
    };
  }

  private tooltip(): object {
    return {
      backgroundColor: '#22252b',
      titleColor:  '#d8d9da',
      bodyColor:   '#8e9096',
      borderColor: '#2a2d35',
      borderWidth: 1,
    };
  }

  private renderDurationChart(series: MetricsSummary['duration_p95_by_template']): void {
    if (!this.durationCanvas?.nativeElement) return;
    const config: ChartConfiguration = {
      type: 'bar',
      data: {
        labels: series.map(s => s.label),
        datasets: [{
          label: 'p95 (s)',
          data:  series.map(s => s.value),
          backgroundColor: '#5794f2',
          borderColor:     '#5794f2',
          borderRadius: 3, barPercentage: 0.55,
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
      this.durationChart.update('none');
    } else {
      this.durationChart = new Chart(this.durationCanvas.nativeElement, config);
    }
  }

  private renderFieldsChart(series: MetricsSummary['field_outcomes']): void {
    if (!this.fieldsCanvas?.nativeElement) return;
    const palette = ['#73bf69','#5794f2','#ff9830','#b877d9',
                     '#fade2a','#f2495c','#73bf69','#5794f2',
                     '#ff9830','#b877d9','#fade2a','#f2495c',
                     '#73bf69','#5794f2'];
    const colors = series.map((_, i) => palette[i % palette.length]);
    const config: ChartConfiguration = {
      type: 'bar',
      data: {
        labels: series.map(s => s.label),
        datasets: [{
          label: 'Taux',
          data:  series.map(s => s.value),
          backgroundColor: colors,
          borderColor:     colors,
          borderRadius: 3, barPercentage: 0.7,
        }],
      },
      options: {
        ...(this.chartDefaults() as any),
        indexAxis: 'y',
        plugins: {
          legend: { display: false },
          tooltip: this.tooltip(),
        },
      },
    };
    if (this.fieldsChart) {
      this.fieldsChart.data = config.data;
      this.fieldsChart.update('none');
    } else {
      this.fieldsChart = new Chart(this.fieldsCanvas.nativeElement, config);
    }
  }
}